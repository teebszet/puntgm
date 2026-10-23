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


# --- ESPN scoreboard schedule wire (in-season, 2026-10-23) ---------------------------------
#
# The games table is the source of ``remaining_games_for_team``, so a stale schedule
# silently prices every player at zero games once the loaded window ends (found live
# 2026-10-22: week 1 only, 2026-10-20..25, loaded once). The NBA schedule is a priori
# knowledge, so it may be fetched ahead; only the *result* (score) is volatile. ESPN's
# public scoreboard endpoint needs no token.

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)

# ESPN abbreviations that differ from the NBA.com codes the store keys teams on
# (verified against the week-1 load, 2026-10-20). Everything else is already identical.
ESPN_ABBREV_TO_CODE = {
    "SA": "SAS",
    "NY": "NYK",
    "GS": "GSW",
    "NO": "NOP",
    "UTAH": "UTA",
    "WSH": "WAS",
}


def espn_team_code(abbrev: str) -> str:
    """Map an ESPN team abbreviation to the store's NBA.com-style code."""
    return ESPN_ABBREV_TO_CODE.get(abbrev, abbrev)


def _espn_all_scored(raw: dict) -> bool:
    """True when every event in an ESPN payload already carries both final scores."""
    for event in raw.get("events", []):
        try:
            comps = event["competitions"][0]["competitors"]
        except (KeyError, IndexError, TypeError):
            continue
        for comp in comps:
            if comp.get("score") in (None, ""):
                return False
    return True


def parse_espn_scoreboard(raw: dict, season: str) -> list[Game]:
    """Pure mapping: one ESPN scoreboard payload -> scheduled games.

    ``game_date`` is the US/Eastern calendar date of the tip, matching the convention
    the store's player_logs already use (NBA.com GAME_DATE is also the US date) — a
    UTC-midnight-crossing late tip belongs to the previous US day. Unplayed games carry
    no score; played ones carry final scores when ESPN has them.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    games: list[Game] = []
    for event in raw.get("events", []):
        try:
            comps = event["competitions"][0]["competitors"]
        except (KeyError, IndexError, TypeError):
            continue
        home = away = None
        home_pts = away_pts = None
        for comp in comps:
            abbrev = espn_team_code(str(comp.get("team", {}).get("abbreviation", "")))
            if comp.get("homeAway") == "home":
                home = abbrev
                if comp.get("score") not in (None, ""):
                    home_pts = int(float(comp["score"]))
            else:
                away = abbrev
                if comp.get("score") not in (None, ""):
                    away_pts = int(float(comp["score"]))
        if not home or not away:
            continue
        try:
            utc = datetime.fromisoformat(str(event["date"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        game_date = utc.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        games.append(Game(f"esp-{event.get('id', '')}", season, game_date, home, away,
                          home_pts, away_pts))
    return games


def refresh_schedule(
    store: Store, cache: RawCache, season: str, today: str, window_days: int = 21
) -> tuple[int, int, str]:
    """One schedule-refresh pass: fetch ESPN scoreboards for [today, today+window_days].

    Past/today payloads bypass the disk cache (scores keep arriving); strictly-future
    dates are cached (the schedule there is fixed). Existing ``esp-*`` rows inside the
    window are replaced wholesale so a reschedule is corrected, not duplicated. Returns
    ``(games in window, changed, window end)`` where ``changed`` says whether the stored
    window rows actually differ from before — the watcher rebuilds only on change. A
    provenance row is written on any change.
    """
    import time as _time
    from datetime import date, timedelta

    import requests

    start = date.fromisoformat(today)
    days = [(start + timedelta(days=i)).strftime("%Y%m%d") for i in range(window_days)]
    games: list[Game] = []
    for day in days:
        endpoint, params = "espn.scoreboard", {"dates": day}
        past = day <= start.strftime("%Y%m%d")
        raw = cache.get(endpoint, params) if cache.has(endpoint, params) else None
        # A played or playing slate keeps receiving final scores, so a cached payload
        # that still has unscored games is refetched — once, until scores land. A
        # scored past payload (or any strictly-future day, unplayable yet) uses cache.
        if raw is None or (past and not _espn_all_scored(raw)):
            resp = requests.get(ESPN_SCOREBOARD_URL, params=params, timeout=30)
            resp.raise_for_status()
            raw = resp.json()
            cache.set(endpoint, params, raw)
        games.extend(parse_espn_scoreboard(raw, season))
    window_end = (start + timedelta(days=window_days - 1)).isoformat()

    # Replace only the refreshable window: dates >= today. Already-played rows before
    # today stay untouched (participation reads count them).
    before = store.conn.execute(
        """SELECT game_id, game_date, home_team, away_team FROM games
           WHERE season = ? AND game_id LIKE 'esp-%' AND game_date >= ?""",
        (season, today),
    ).fetchall()
    with store.conn:
        store.conn.execute(
            "DELETE FROM games WHERE season = ? AND game_id LIKE 'esp-%' AND game_date >= ?",
            (season, today),
        )
        store.upsert_games(games)
    in_window = len(games)
    after = {(g.game_id, g.game_date, g.home_team, g.away_team) for g in games}
    changed = {(r["game_id"], r["game_date"], r["home_team"], r["away_team"])
               for r in before} != after
    if changed:
        store.record_provenance(
            f"games:season={season}",
            f"ESPN scoreboard refresh: window {today}..{window_end}, {in_window} games "
            "(esp-* ids; ESPN abbrevs mapped via nba_source.ESPN_ABBREV_TO_CODE)",
            _time.strftime("%Y-%m-%d"),
        )
    return in_window, changed, window_end
