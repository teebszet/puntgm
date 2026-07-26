"""Deterministic synthetic NBA season for offline development and verification.

This fabricates a small, clearly-synthetic season (teams, players, a dated schedule,
box-score logs, and a couple of dated injury designations) so the store, engine, log,
and league simulation can be exercised end-to-end with no network. It is NOT real NBA
data and is never presented as such — the real path is ``nba_source.backfill_season``.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from fantasy_gm.models import Availability, Game, PlayerGameLog, UsageRole

# A few fake NBA-style team abbreviations.
TEAMS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]


def seed_synthetic_season(
    store,
    season: str = "2025-26",
    start: str = "2025-10-21",
    weeks: int = 6,
    players_per_team: int = 14,
    seed: int = 7,
) -> dict[str, int]:
    """Populate ``store`` with a synthetic season. Deterministic given ``seed``.

    Returns simple counts for sanity-checking. Each NBA team plays 2–4 games per week on
    fixed weekdays; every player gets a per-game box line drawn from a per-player skill
    level so recent-production signals are meaningful.
    """
    rng = random.Random(seed)
    season_start = date.fromisoformat(start)

    # --- players: stable ids + a hidden skill level per player ---------------
    players: list[dict] = []
    for team in TEAMS:
        for i in range(players_per_team):
            pid = f"{team}-P{i:02d}"
            players.append(
                {"player_id": pid, "name": f"{team} Player {i}", "team": team,
                 "skill": rng.uniform(0.4, 1.0)}
            )

    games: list[Game] = []
    logs: list[PlayerGameLog] = []
    game_seq = 0
    # Each week: pair teams on rotating weekdays (Tue/Thu/Sat), giving varied game counts.
    for w in range(weeks):
        week_start = season_start + timedelta(weeks=w)
        weekday_offsets = [1, 3, 5]  # Tue, Thu, Sat relative to a Monday-aligned start
        rotation = TEAMS[w % len(TEAMS):] + TEAMS[: w % len(TEAMS)]
        pairs = list(zip(rotation[::2], rotation[1::2], strict=False))
        for di, off in enumerate(weekday_offsets):
            gdate = (week_start + timedelta(days=off)).isoformat()
            # Not every pair plays every game-day, so weekly game counts vary by team.
            for home, away in pairs:
                # stable (process-independent) hash so the schedule is deterministic;
                # Python's built-in hash() for str is randomized per process.
                stable = sum(ord(c) for c in home + away)
                if (di + stable) % 3 == 0 and di != 0:
                    continue
                game_seq += 1
                gid = f"G{season}-{game_seq:04d}"
                home_pts, away_pts = 0, 0
                for team, is_home in ((home, True), (away, False)):
                    for p in [pp for pp in players if pp["team"] == team]:
                        line = _box_line(rng, p["skill"])
                        pts = line["pts"]
                        if is_home:
                            home_pts += pts
                        else:
                            away_pts += pts
                        logs.append(
                            PlayerGameLog(gid, season, gdate, p["player_id"], p["name"],
                                          team, line)
                        )
                games.append(Game(gid, season, gdate, home, away, home_pts, away_pts))

    store.upsert_games(games)
    store.upsert_player_logs(logs)

    # --- usage/role snapshots (D5) -------------------------------------------
    # Weekly snapshots per player: minutes/fga scale with skill and are stable, except
    # one deterministic breakout whose role climbs from mid-season (a real depth-chart
    # cause the signal engine can detect and grade "strong").
    breakout = players[10]  # a mid-skill player
    usage: list[UsageRole] = []
    for w in range(weeks):
        known = (season_start + timedelta(weeks=w)).isoformat()
        for p in players:
            base_min = 30.0 * p["skill"]
            base_fga = 12.0 * p["skill"]
            starter = p["skill"] > 0.7
            depth = 1 if starter else 3
            if p["player_id"] == breakout["player_id"] and w >= weeks // 2:
                # sustained climb: more minutes, more shots, moves up the depth chart
                bump = (w - weeks // 2 + 1)
                base_min += 6.0 * bump
                base_fga += 3.0 * bump
                starter = True
                depth = max(1, 3 - bump)
            usage.append(UsageRole(p["player_id"], known, round(base_min, 1),
                                   round(base_fga, 1), starter, depth))
    store.add_usage_role(usage)

    # --- a couple of dated availability designations (D4) --------------------
    injured = players[3]
    mid = (season_start + timedelta(weeks=weeks // 2)).isoformat()
    store.add_availability([
        Availability(injured["player_id"], "QUESTIONABLE", mid, "official", 0.9,
                     "synthetic injury"),
        Availability(injured["player_id"], "OUT",
                     (date.fromisoformat(mid) + timedelta(days=2)).isoformat(),
                     "official", 0.95, "synthetic injury"),
    ])
    store.record_provenance(
        f"availability:{injured['player_id']}",
        "synthetic dated injury; real backfill may lack perfectly-dated history",
        mid,
    )

    return {"players": len(players), "games": len(games), "logs": len(logs),
            "usage_snapshots": len(usage)}


def _box_line(rng: random.Random, skill: float) -> dict[str, float]:
    """A plausible per-game 9-cat line scaled by a player's skill level."""
    fga = max(1, int(rng.gauss(12 * skill, 3)))
    fgm = max(0, min(fga, int(rng.gauss(fga * 0.47, 2))))
    fta = max(0, int(rng.gauss(4 * skill, 2)))
    ftm = max(0, min(fta, int(rng.gauss(fta * 0.78, 1))))
    fg3m = max(0, int(rng.gauss(2 * skill, 1)))
    pts = 2 * (fgm - fg3m) + 3 * fg3m + ftm
    return {
        "pts": float(pts),
        "reb": float(max(0, int(rng.gauss(6 * skill, 2)))),
        "ast": float(max(0, int(rng.gauss(4 * skill, 2)))),
        "stl": float(max(0, int(rng.gauss(1.2 * skill, 1)))),
        "blk": float(max(0, int(rng.gauss(0.8 * skill, 1)))),
        "tov": float(max(0, int(rng.gauss(2.0, 1)))),
        "fg3m": float(fg3m),
        "fgm": float(fgm),
        "fga": float(fga),
        "ftm": float(ftm),
        "fta": float(fta),
        "fg_pct": round(fgm / fga, 3) if fga else 0.0,
        "ft_pct": round(ftm / fta, 3) if fta else 0.0,
    }
