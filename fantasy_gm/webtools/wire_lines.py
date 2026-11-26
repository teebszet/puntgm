"""Wire-lines board: free agents who strictly beat a stat threshold last night.

Codifies the hand-run table built with Tim (buzz thread 8f81f55a, 2026-11-24/25):
every player ON the waiver wire whose last-night box line strictly beat one of the
thresholds (>20 pts, >5 ast, >9 reb, >2 stl, >2 blk), with the full line — minutes,
all cats, shooting splits, which thresholds were beaten — plus the games his NBA
team still has scheduled in the live matchup week (an OUT player reads 0: remaining
schedule is what an add can actually accrue).

Basis contract (verified against both hand-run tables the day they were posted):

- box lines: ``player_logs`` rows on ``game_date`` (default: the day before
  ``as_of``), minutes from the ``usage_role`` snapshot taken that same date —
  box scores carry no MIN;
- wire: everyone outside the latest FULL-league lineup read on/before ``as_of``
  (``Store.rosters_asof`` — a roster-events fold resurrects traded-away players);
- shooting splits computed from makes/attempts, never a stored rate; a no-attempt
  line renders ``null``, a 0/N line renders 0.0 — the hand-run's "—" conflated the
  two (Jamal Shead's 0/2 on 2026-11-23 printed as "—"); the codified board shows
  the true line;
- games left: DISTINCT (date, home, away) scheduled games for the player's current
  NBA team from ``as_of`` through the active matchup's ``period_end`` (the slate
  dedupe mirrors ``fixture_grid._schedule``: the ESPN and LeagueGameLog wires store
  one real game twice, and the copies can disagree on orientation);
- order: pts desc, then reb, ast, stl, blk desc, then player id — deterministic and
  it reproduces the posted tables' tie order (Slawson ahead of Tre Jones on 10 vs 4
  rebounds; Grimes ahead of Kris Murray on 8 vs 1 assists).

Near misses are the deterministic slice of the hand-run's narrative: a wire player
who played and hit a threshold EXACTLY on some cat but strictly beat none
(Huerter's 5 ast, Carter's 2 stl, Ighodaro's 2 stl + 2 blk).
"""

from __future__ import annotations

import datetime as dt

# Strictly more than (Tim, buzz thread 8f81f55a — "scored MORE than 20 points…").
# Key order is the display/beat order of the original ask.
THRESHOLDS: dict[str, int] = {"pts": 20, "ast": 5, "reb": 9, "stl": 2, "blk": 2}

_LINE_CATS: tuple[str, ...] = ("pts", "reb", "ast", "stl", "blk", "fg3m", "tov")
_ORDER_TIEBREAKS: tuple[str, ...] = ("reb", "ast", "stl", "blk")  # after pts desc


def _pct(stats: dict[str, float], makes: str, attempts: str) -> float | None:
    """Shooting split from makes/attempts; ``None`` when there were no attempts."""
    att = stats.get(attempts) or 0
    if not att:
        return None
    return round((stats.get(makes) or 0) / att * 100, 1)


def _slate(store, game_date: str) -> dict:
    """The night's deduped matchups and how many are final.

    Same dedupe as ``fixture_grid._schedule`` (unordered pair, official gid's
    orientation wins). "Final" means the box score is banked — distinct
    ``player_logs`` game ids on the date — never the games table's score columns,
    which carry placeholders for the live season (found 2026-11-24: zero real
    2026-27 scores stored).
    """
    seen: dict[tuple[str, frozenset], dict] = {}
    for r in store.conn.execute(
        """SELECT game_id, home_team, away_team FROM games
           WHERE game_date = ?""",
        (game_date,),
    ):
        home, away = r["home_team"], r["away_team"]
        if not home or not away:
            continue
        official = not str(r["game_id"]).startswith("esp-")
        key = (game_date, frozenset((home, away)))
        prior = seen.get(key)
        if prior is None:
            seen[key] = {"home": home, "away": away, "official": official}
        elif official and not prior["official"]:
            seen[key].update(home=home, away=away, official=True)
    banked = {
        r["game_id"]
        for r in store.conn.execute(
            "SELECT DISTINCT game_id FROM player_logs WHERE game_date = ?", (game_date,)
        )
    }
    games = list(seen.values())
    return {
        "games": len(games),
        "finals": len(banked),
        "matchups": [f"{g['away']}@{g['home']}" for g in games],
    }


def wire_lines_payload(
    store, league_id: str, as_of: str, *,
    game_date: str | None = None, thresholds: dict[str, int] | None = None,
) -> dict:
    """The wire-lines board for the night before ``as_of``.

    Returns rows of free agents who strictly beat a threshold, the near misses
    (exactly at a threshold, none beaten), the deduped slate, and the matchup week
    the games-left column reads against.
    """
    th = dict(thresholds or THRESHOLDS)
    if game_date is None:
        game_date = (dt.date.fromisoformat(as_of) - dt.timedelta(days=1)).isoformat()

    week = None
    matchup = store.active_matchup(league_id, as_of)
    if matchup is not None:
        week = {"start": matchup.period_start, "end": matchup.period_end}

    names = store.player_names(as_of)
    rosters = store.rosters_asof(league_id, as_of)
    rostered = {p for ids in rosters.values() for p in ids}

    rows: list[dict] = []
    near_misses: list[dict] = []
    scanned = 0
    for g in store.player_game_stream_asof(as_of, since=game_date):
        if g["game_date"] != game_date:
            continue
        scanned += 1
        if g["player_id"] in rostered:
            continue
        stats = {cat: float(g["stats"].get(cat) or 0) for cat in _LINE_CATS}
        beat = [cat for cat in th if stats[cat] > th[cat]]
        nba_team = store.player_team(g["player_id"], as_of) or g["team"]
        at = [cat for cat in th if stats[cat] == th[cat]]
        out = False
        games_left = None
        if week is not None:
            av = store.availability_asof(g["player_id"], as_of)
            out = bool(av and av.status == "OUT")
            games_left = 0 if out else store.remaining_games_for_team(
                nba_team, as_of, week["end"])
        row = {
            "player_id": g["player_id"],
            "name": names.get(g["player_id"], g["player_id"]),
            "nba_team": nba_team,
            "minutes": None if g["minutes"] is None else int(round(g["minutes"])),
            "beat": beat,
            "stats": stats,
            "fg_pct": _pct(g["stats"], "fgm", "fga"),
            "ft_pct": _pct(g["stats"], "ftm", "fta"),
            "games_left": games_left,
            "out": out,
        }
        if beat:
            rows.append(row)
        elif at:
            near_misses.append({
                "player_id": row["player_id"], "name": row["name"],
                "nba_team": nba_team, "minutes": row["minutes"], "at": at,
                "stats": stats, "fg_pct": row["fg_pct"], "ft_pct": row["ft_pct"],
                "games_left": games_left,
            })

    def _order(item: dict) -> tuple:
        key = [-item["stats"].get("pts", 0.0)]
        key += [-item["stats"].get(cat, 0.0) for cat in _ORDER_TIEBREAKS]
        key.append(item["player_id"])
        return tuple(key)

    rows.sort(key=_order)
    near_misses.sort(key=_order)
    return {
        "as_of": as_of,
        "game_date": game_date,
        "week": week,
        "slate": _slate(store, game_date),
        "rostered_count": len(rostered),
        "player_games_scanned": scanned,
        "rows": rows,
        "near_misses": near_misses,
    }
