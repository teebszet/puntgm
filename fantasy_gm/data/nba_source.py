"""Real backfill from NBA.com via ``nba_api`` (D1).

``nba_api`` is imported lazily so the rest of the package — store, engine, log, simulation,
and the entire offline path — runs with no network and no heavy dependency. Only this real
backfill needs it, and it runs locally, once (D1), from a residential IP where stats.nba.com
is reachable (datacenter/VPN IPs are blocked by NBA's Akamai WAF).

One `LeagueGameLog` call returns every player-game box line for a season; from it we derive
both the per-player logs and the game schedule. Responses are cached to disk so the backfill
is resumable/idempotent. The parsing (`parse_league_game_log`) is a pure function so it can be
unit-tested without network.
"""

from __future__ import annotations

from typing import Any

from fantasy_gm.data.cache import RawCache
from fantasy_gm.data.store import Store
from fantasy_gm.models import Game, PlayerGameLog, UsageRole

# nba_api LeagueGameLog column -> our category key
_STAT_MAP = {
    "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk", "TOV": "tov",
    "FG3M": "fg3m", "FGM": "fgm", "FGA": "fga", "FTM": "ftm", "FTA": "fta",
    "FG_PCT": "fg_pct", "FT_PCT": "ft_pct",
}


def _iso_date(v: Any) -> str:
    """nba_api GAME_DATE is 'YYYY-MM-DD' (sometimes with a time suffix)."""
    return str(v)[:10]


def _to_minutes(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v)
    if ":" in s:  # "MM:SS"
        mm, _, ss = s.partition(":")
        try:
            return float(mm) + float(ss) / 60.0
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_matchup(team: str, matchup: str) -> tuple[str, str]:
    """'LAL vs. BOS' -> (home=LAL, away=BOS); 'LAL @ BOS' -> (home=BOS, away=LAL)."""
    if " vs. " in matchup:
        _team, _, opp = matchup.partition(" vs. ")
        return team, opp.strip()
    if " @ " in matchup:
        _team, _, opp = matchup.partition(" @ ")
        return opp.strip(), team
    return team, ""  # unknown format — leave away blank


def parse_league_game_log(
    rows: list[dict], season: str
) -> tuple[list[Game], list[PlayerGameLog], list[UsageRole]]:
    """Pure mapping: LeagueGameLog player rows -> (games, player logs, usage snapshots).

    ``rows`` is ``get_normalized_dict()['LeagueGameLog']`` — one row per player per game.
    Games are derived from the same rows (parsing MATCHUP for home/away). Usage snapshots
    take per-game minutes/attempts; starter/depth are best-effort heuristics from minutes,
    since LeagueGameLog does not carry them.
    """
    games: dict[str, Game] = {}
    logs: list[PlayerGameLog] = []
    usage: list[UsageRole] = []
    for r in rows:
        gid = str(r["GAME_ID"])
        gdate = _iso_date(r["GAME_DATE"])
        team = r.get("TEAM_ABBREVIATION") or str(r.get("TEAM_ID", ""))
        pid = str(r["PLAYER_ID"])
        stats = {key: float(r.get(col) or 0.0) for col, key in _STAT_MAP.items()}
        logs.append(PlayerGameLog(gid, season, gdate, pid,
                                  r.get("PLAYER_NAME", pid), team, stats))

        minutes = _to_minutes(r.get("MIN"))
        starter = minutes >= 24.0  # heuristic (LeagueGameLog lacks starter/depth)
        usage.append(UsageRole(pid, gdate, round(minutes, 1),
                               stats["fga"], starter, 1 if starter else 3))

        if gid not in games:
            home, away = _parse_matchup(team, r.get("MATCHUP", ""))
            games[gid] = Game(gid, season, gdate, home, away)
    return list(games.values()), logs, usage


def _fetch_rows(season: str, cache: RawCache) -> list[dict]:
    endpoint, params = "leaguegamelog.player", {"season": season, "player_or_team": "P"}
    if cache.has(endpoint, params):
        raw = cache.get(endpoint, params)
    else:  # pragma: no cover - network path, runs on the user's machine
        from nba_api.stats.endpoints import leaguegamelog
        raw = leaguegamelog.LeagueGameLog(
            season=season, player_or_team_abbreviation="P",
            season_type_all_star="Regular Season", timeout=60,
        ).get_normalized_dict()
        cache.set(endpoint, params, raw)
    # normalized dict is keyed by result-set name; take LeagueGameLog (or the first set)
    return raw.get("LeagueGameLog") or next(iter(raw.values()), [])


def backfill_season(
    store: Store, season: str, cache: RawCache, dry_run: bool = False
) -> dict[str, int]:
    """Backfill a full season's games + player box-score logs (+ usage) into the store.

    ``dry_run`` fetches and parses but does not write — a fast check that the parse mapping
    matches the live payload before committing a full store write.
    """
    rows = _fetch_rows(season, cache)
    games, logs, usage = parse_league_game_log(rows, season)
    counts = {"rows": len(rows), "games": len(games), "logs": len(logs), "usage": len(usage)}
    if dry_run:
        return counts
    store.upsert_games(games)
    store.upsert_player_logs(logs)
    store.add_usage_role(usage)
    return counts
