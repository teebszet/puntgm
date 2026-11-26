"""T1 — the advanced fixture grid: team defense from stored box scores.

Rows are a fantasy roster, columns are the next ``days`` dates; each cell shows the
opponent and how much that opponent's defense allows at each category. The defense
model is a pure function of ``player_logs`` joined to ``games``: what every opponent
player produced against a defense, summed. Rate categories are volume-weighted
(config A8) — Σmakes / Σattempts against the defense, never an average of rates.

Season basis (the ranked-data trap): box scores lag the league season, so the model
reads ``store.latest_logged_season()`` as "current". A team with fewer than
``min_team_games`` current-season games prices from the previous season instead —
an early-season grid should show last year's defense rather than a 2-game sample.
The chosen basis travels in the payload (``team_basis``).

Favorability is direction-aware so the page colors uniformly: ``favor`` is the
multiplicative edge facing this defense gives the attacker on that category, 1.00 =
league average, > 1 good for the attacker. For positive cats favor = allowed / mean;
for turnovers (the cat the attacker wants LOW) favor = mean / allowed, because a
defense that forces many turnovers hurts him. ``rank`` orders the teams per cat,
1 = most favorable. Position-adjusted cells normalize the same way against the
league-wide (all teams) allowed rate at that position, with their own ranks.

Position adjustment: rates also accumulate per (defense, G/F/C) as per-player
per-game averages at that position. A grid cell uses the position split when the
(defense, position) cell spans at least ``min_pos_playergames`` log rows;
otherwise it falls back to the team rate. The per-cell ``basis`` field names
which, so thin samples never masquerade as splits.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field

from fantasy_gm.config import CATEGORY_DIRECTION, DEFAULT_CATEGORIES, PERCENTAGE_CATEGORIES

# Counting categories accumulate directly; rate categories accumulate via their
# (makes, attempts) components.
_COUNTING_CATS = tuple(c for c in DEFAULT_CATEGORIES if c not in PERCENTAGE_CATEGORIES)

_ACCUMULATE_KEYS = (*_COUNTING_CATS, *(k for pair in PERCENTAGE_CATEGORIES.values() for k in pair))

MIN_TEAM_GAMES = 5  # a team-season sample below this defers to the other season
MIN_POS_PLAYERGAMES = 30  # a (defense, position) cell below this falls back to team rate

_NO_TEAM = ("", "TBD")
_POSITIONS = ("G", "F", "C")


@dataclass
class Defense:
    """Team-allowed per-game rates, league means, and direction-aware favorability.

    ``allowed`` holds per-game rates; ``team_games`` / ``position_games`` hold the
    sample sizes behind them; ``team_basis`` names the season each team's numbers
    came from. Position variants key on (team, position) and hold per-PLAYER
    per-game averages at that position (rows divisor), normalized against the
    league-wide per-player average at that position — the right multiplier for one
    grid-row player of that position.
    """

    allowed: dict[str, dict[str, float]]
    league_mean: dict[str, float]
    favor: dict[str, dict[str, float]]
    rank: dict[str, dict[str, int]]
    team_games: dict[str, int]
    team_basis: dict[str, str]
    by_position: dict[tuple[str, str], dict[str, float]]
    position_games: dict[tuple[str, int], int]
    position_league_mean: dict[tuple[str, str], dict[str, float]] = field(default_factory=dict)
    position_favor: dict[tuple[str, str], dict[str, float]] = field(default_factory=dict)
    position_rank: dict[tuple[str, str], dict[str, int]] = field(default_factory=dict)
    teams: list[str] = field(default_factory=list)


def _prev_season(season: str) -> str:
    year = int(season[:4])
    return f"{year - 1}-{year % 100:02d}"


def _accumulate(store, seasons: tuple[str, str], as_of: str):
    """Raw per-(season, defense) and per-(season, defense, position) stat sums.

    The defense of a log row is the other side of its game. Team game counts come
    from distinct game ids per defense, so a game present under two id spaces (the
    ESPN wire and the LeagueGameLog backfill both carry past seasons) cannot
    double-count a team's sample.
    """
    q = """
        SELECT l.season, l.game_id, l.player_id, l.team, l.stats_json,
               g.home_team, g.away_team
        FROM player_logs l JOIN games g ON g.game_id = l.game_id
        WHERE l.season IN (?, ?) AND l.game_date <= ?
    """
    sums: dict[str, dict[str, dict[str, float]]] = {s: {} for s in seasons}
    pos_sums: dict[str, dict[tuple[str, str], dict[str, float]]] = {s: {} for s in seasons}
    game_sets: dict[str, dict[str, set]] = {s: {} for s in seasons}
    pos_rows: dict[str, dict[tuple[str, str], int]] = {s: {} for s in seasons}

    positions = store.player_positions_asof(as_of)
    for r in store.conn.execute(q, (*seasons, as_of)):
        home, away, team = r["home_team"], r["away_team"], r["team"]
        if not home or not away or home in _NO_TEAM or away in _NO_TEAM:
            continue
        defense = away if team == home else (home if team == away else None)
        if defense is None or defense in _NO_TEAM:
            continue
        stats = json.loads(r["stats_json"])
        season = r["season"]
        keys = {k: float(stats[k]) for k in _ACCUMULATE_KEYS if stats.get(k) is not None}

        cell = sums[season].setdefault(defense, {})
        for key, v in keys.items():
            cell[key] = cell.get(key, 0.0) + v
        game_sets[season].setdefault(defense, set()).add(r["game_id"])

        pos_row = positions.get(r["player_id"])
        if pos_row is not None and pos_row.position in _POSITIONS:
            pcell = pos_sums[season].setdefault((defense, pos_row.position), {})
            for key, v in keys.items():
                pcell[key] = pcell.get(key, 0.0) + v
            pos_rows[season][(defense, pos_row.position)] = (
                pos_rows[season].get((defense, pos_row.position), 0) + 1)
    return sums, pos_sums, game_sets, pos_rows


def _rates(cat: str, sums: dict[str, float], n_games: int) -> float | None:
    """Per-game allowed rate for one category; rate cats are Σmakes / Σattempts."""
    if n_games <= 0:
        return None
    if cat in PERCENTAGE_CATEGORIES:
        makes, attempts = PERCENTAGE_CATEGORIES[cat]
        m, a = sums.get(makes, 0.0), sums.get(attempts, 0.0)
        return m / a if a > 0 else None
    return sums.get(cat, 0.0) / n_games


def _directional(favorable_if: float, baseline: float, direction: int) -> float | None:
    """Multiplicative attacker edge vs a defense, > 1 good, direction-aware."""
    if baseline <= 0 or favorable_if <= 0:
        return None
    ratio = favorable_if / baseline
    return ratio if direction > 0 else 1.0 / ratio


def _mean_map(cells: dict, cats: list[str]) -> dict:
    """Unweighted league mean of each cat across the cells that carry it."""
    out: dict = {}
    for cat in cats:
        vals = [c[cat] for c in cells.values() if cat in c]
        out[cat] = sum(vals) / len(vals) if vals else 0.0
    return out


def _score(cells: dict, means: dict, cats: list[str]) -> tuple[dict, dict]:
    """Direction-aware favor and rank (1 = most favorable) for every cell."""
    favor = {key: {} for key in cells}
    rank = {key: {} for key in cells}
    for cat in cats:
        direction = CATEGORY_DIRECTION[cat]
        scored = []
        for key, rates in cells.items():
            f = _directional(rates.get(cat, 0.0), means.get(cat, 0.0), direction)
            if f is not None:
                favor[key][cat] = f
                scored.append((key, f))
        scored.sort(key=lambda kf: -kf[1])
        for i, (key, _) in enumerate(scored, start=1):
            rank[key][cat] = i
    return favor, rank


def team_defense(store, as_of: str, *, min_team_games: int = MIN_TEAM_GAMES,
                 min_pos_playergames: int = MIN_POS_PLAYERGAMES) -> Defense:
    """Team-allowed per-cat rates as of ``as_of``, with favorability and ranks."""
    current = store.latest_logged_season()
    if not current:
        raise ValueError("no player logs in the store — nothing to derive defense from")
    seasons = (current, _prev_season(current))
    sums, pos_sums, game_sets, pos_rows = _accumulate(store, seasons, as_of)

    allowed: dict[str, dict[str, float]] = {}
    team_games: dict[str, int] = {}
    team_basis: dict[str, str] = {}
    by_position: dict[tuple[str, str], dict[str, float]] = {}
    position_games: dict[tuple[str, int], int] = {}

    teams = sorted(
        {t for s in seasons for t in game_sets[s]} | {t for s in seasons for t in sums[s]})
    for team in teams:
        cur_n = len(game_sets[seasons[0]].get(team, ()))
        prev_n = len(game_sets[seasons[1]].get(team, ()))
        if cur_n >= min_team_games:
            basis = seasons[0]
        else:
            basis = seasons[1] if prev_n > cur_n else seasons[0]
        n = len(game_sets[basis].get(team, ()))
        cell = sums[basis].get(team)
        if n <= 0 or not cell:
            continue
        team_games[team] = n
        team_basis[team] = basis
        allowed[team] = {cat: rate for cat in DEFAULT_CATEGORIES
                         if (rate := _rates(cat, cell, n)) is not None}

        for pos in _POSITIONS:
            pcell = pos_sums[basis].get((team, pos))
            pn = pos_rows[basis].get((team, pos), 0) if pcell else 0
            if not pcell or pn < min_pos_playergames:
                continue
            by_position[(team, pos)] = {
                cat: rate for cat in DEFAULT_CATEGORIES
                if (rate := _rates(cat, pcell, pn)) is not None
            }
            position_games[(team, pos)] = pn

    cats = [c for c in DEFAULT_CATEGORIES
            if c not in PERCENTAGE_CATEGORIES or any(c in t for t in allowed.values())]
    league_mean = _mean_map(allowed, cats)

    position_league_mean = {}
    for pos in _POSITIONS:
        slice_ = {team: rates for (team, p), rates in by_position.items()
                  if p == pos and team in allowed}
        if slice_:
            position_league_mean[pos] = _mean_map(slice_, cats)

    team_favor, team_rank = _score(allowed, league_mean, cats)
    position_favor: dict[tuple[str, str], dict[str, float]] = {}
    position_rank: dict[tuple[str, str], dict[str, int]] = {}
    for pos, means in position_league_mean.items():
        slice_ = {(team, pos): rates for (team, p), rates in by_position.items()
                  if p == pos and team in allowed}
        f, r = _score(slice_, means, cats)
        position_favor.update(f)
        position_rank.update(r)

    return Defense(
        allowed=allowed, league_mean=league_mean, favor=team_favor, rank=team_rank,
        team_games=team_games, team_basis=team_basis, by_position=by_position,
        position_games=position_games, position_league_mean=position_league_mean,
        position_favor=position_favor, position_rank=position_rank, teams=teams,
    )

def _schedule(store, start: str, end: str) -> dict[str, list[dict]]:
    """date -> [{home, away}] from the stored schedule window.

    The ESPN wire and the LeagueGameLog backfill store the same real game under two
    ids (the store's own remaining-games counter dedupes DISTINCT (date, home, away)
    for the same reason), and the copies can disagree on home/away orientation
    (live 2026-11-24: IND@DEN stored both ways). Dedupe by unordered pair; the
    official NBA gid's orientation wins when the copies conflict.
    """
    seen: dict[tuple[str, frozenset], dict] = {}
    for r in store.conn.execute(
        """SELECT game_id, game_date, home_team, away_team FROM games
           WHERE game_date >= ? AND game_date <= ?
           ORDER BY game_date""",
        (start, end),
    ):
        home, away = r["home_team"], r["away_team"]
        if not home or not away or home in _NO_TEAM or away in _NO_TEAM:
            continue
        official = not str(r["game_id"]).startswith("esp-")
        key = (r["game_date"], frozenset((home, away)))
        prior = seen.get(key)
        if prior is None:
            seen[key] = {"home": home, "away": away, "official": official}
        elif official and not prior["official"]:
            seen[key] = {"home": home, "away": away, "official": True}
    by_date: dict[str, list[dict]] = {}
    for (date, _pair), g in seen.items():
        by_date.setdefault(date, []).append({"home": g["home"], "away": g["away"]})
    return by_date


def _cell_favor(defense: Defense, opp: str, pos: str | None) -> dict[str, dict]:
    """Favorability for facing ``opp`` at ``pos``; the position split only when the
    sample is thick (``team_defense`` enforces that before it stores a split)."""
    pos_key = (opp, pos) if pos else None
    if pos_key is not None and pos_key in defense.position_favor:
        favor, rank, basis = defense.position_favor[pos_key], defense.position_rank.get(pos_key, {}), "position"
    else:
        favor, rank, basis = defense.favor.get(opp, {}), defense.rank.get(opp, {}), "team"
    return {
        cat: {
            "favor": round(f, 2),
            "rank": rank.get(cat),
            "basis": basis,
        }
        for cat, f in sorted(favor.items())
    }


def fixture_grid_payload(store, league_id: str, team_id: str, as_of: str, *,
                         days: int = 14) -> dict:
    """T1: roster rows × upcoming dates, per-game opponent favorability per cat."""
    resolved = store.resolve_team_id(league_id, team_id) or team_id
    # The covering dated read, never the per-player fold: the fold resurrects
    # traded-away players (live 2026-10-27); dated reads are full snapshots.
    lineup = store.lineup_covering(league_id, resolved, as_of)
    if lineup is None:
        lineup = store.lineup_asof(league_id, resolved, as_of)
    names = store.player_names(as_of)
    positions = store.player_positions_asof(as_of)
    defense = team_defense(store, as_of)

    start = dt.date.fromisoformat(as_of)
    dates = [(start + dt.timedelta(days=i)).isoformat() for i in range(days)]
    schedule = _schedule(store, dates[0], dates[-1])

    players = []
    for pid, slot in sorted(lineup.items(), key=lambda kv: (kv[1], kv[0])):
        nba_team = store.player_team(pid, as_of)
        pos_row = positions.get(pid)
        games = []
        if nba_team:
            for date in dates:
                for g in schedule.get(date, ()):
                    if nba_team not in (g["home"], g["away"]):
                        continue
                    opp = g["away"] if nba_team == g["home"] else g["home"]
                    games.append({
                        "date": date,
                        "opponent": opp,
                        "is_home": nba_team == g["home"],
                        "favorability": _cell_favor(defense, opp,
                                                    pos_row.position if pos_row else None),
                    })
        row = {
            "player_id": pid,
            "name": names.get(pid, pid),
            "slot": slot,
            "nba_team": nba_team,
            "position": pos_row.position if pos_row else None,
            "games": games,
        }
        avail = store.availability_asof(pid, as_of)
        if avail is not None:
            row["status"] = avail.status
        players.append(row)

    return {
        "as_of": as_of,
        "league_id": league_id,
        "team_id": resolved,
        "days": days,
        "dates": dates,
        "cats": list(defense.league_mean),
        "league_mean": {c: round(v, 2) for c, v in defense.league_mean.items()},
        "team_basis": defense.team_basis,
        "team_games": defense.team_games,
        "players": players,
    }
