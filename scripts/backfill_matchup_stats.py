"""Backfill Yahoo's official weekly team totals into the store.

The league scoreboard endpoint's ``team_stats`` are the platform-scored ground
truth for a completed matchup week. Our player-log banked tallies drift from them
when a wire move posts after the morning lineup read (known 2026-11-23 class:
week 5 read 4-5 for Tim's team while Yahoo scored it 2-7), so the all-pairs
scoreboard matrix prefers these actuals for completed weeks and uses the store
tally only as a labelled fallback.

Idempotent: every completed week is re-fetched and upserted (INSERT OR REPLACE),
so re-running after a correction on Yahoo's side converges. Only weeks with
``period_end < today`` are fetched — the live week keeps moving. Fails loud: a
week whose teams are missing any scoring category aborts the run rather than
storing a partial board of record.

Run (daily, right before the snapshot commit — see puntgm_daily_snapshot.sh):

    python3 scripts/backfill_matchup_stats.py \
        --store /Users/tim/projects/fantasy-nba-gm/data/fantasy_gm.sqlite
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_LEAGUE = "yahoo-478.l.25733"
DEFAULT_STORE = "/Users/tim/projects/fantasy-nba-gm/data/fantasy_gm.sqlite"
DEFAULT_DATA_DIR = "/Users/tim/projects/fantasy-nba-gm/data"

# Yahoo's scoreboard team_stats use their own id scheme (NOT the standard
# player-stat table): 9004003 carries "FGM/FGA" and 9007006 "FTM/FTA" as combined
# strings, the rates sit at 5/8, and the counting cats at 10/12/15-19. Mapping read
# off the live week-5 payload 2026-11-27; the matrix only needs the 9 comparable
# cats, so the component ids are left unmapped.
STAT_ID_TO_CAT = {
    "5": "fg_pct", "8": "ft_pct",
    "10": "fg3m", "12": "pts", "15": "reb", "16": "ast",
    "17": "stl", "18": "blk", "19": "tov",
}
REQUIRED_CATS = ("pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fg_pct", "ft_pct")
RATE_CATS = ("fg_pct", "ft_pct")


def fetch_scoreboard(league_key: str, week: int, token: str) -> dict:
    import requests

    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/scoreboard;week={week}"
    resp = requests.get(url, params={"format": "json"},
                        headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if resp.status_code == 401:
        raise RuntimeError("401 from Yahoo — token expired")
    resp.raise_for_status()
    return resp.json()


def parse_team_stats(raw: dict) -> dict[str, dict[str, float]]:
    """team_key -> {cat: value} for one week's scoreboard payload."""
    try:
        matchups = raw["fantasy_content"]["league"][1]["scoreboard"]["0"]["matchups"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("scoreboard payload has no matchups — payload shape changed")
    out: dict[str, dict[str, float]] = {}
    for key, entry in matchups.items():
        if not key.isdigit():
            continue
        teams = entry["matchup"]["0"]["teams"]
        for i in ("0", "1"):
            team = teams[i]["team"]
            team_key = team[0][0]["team_key"]
            stats_block = team[1].get("team_stats", {}).get("stats", {})
            entries = stats_block.values() if isinstance(stats_block, dict) else stats_block
            values: dict[str, float] = {}
            for item in entries:
                st = item.get("stat", item) if isinstance(item, dict) else {}
                cat = STAT_ID_TO_CAT.get(str(st.get("stat_id")))
                if cat is None or st.get("value") is None:
                    continue
                v = float(st["value"])
                if cat in RATE_CATS and v > 1.0:  # Yahoo sometimes answers in percent
                    v = v / 100.0
                values[cat] = v
            missing = [c for c in REQUIRED_CATS if c not in values]
            if missing:
                raise RuntimeError(
                    f"{team_key}: missing categories {missing} — refusing to store a "
                    f"partial board (stat ids mapped: {sorted(STAT_ID_TO_CAT)})")
            out[team_key] = values
    if not out:
        raise RuntimeError("no teams parsed from the scoreboard payload")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                    help="where the Yahoo token files live")
    ap.add_argument("--league", default=DEFAULT_LEAGUE,
                    help="store league id (yahoo-prefixed)")
    ap.add_argument("--today", default=date.today().isoformat(),
                    help="override today (UTC date) — weeks ending before it backfill")
    args = ap.parse_args()

    from fantasy_gm.data.store import Store
    from fantasy_gm.data.yahoo_fetch import refresh_token_from_files

    data_dir = Path(args.data_dir)
    token_path = data_dir / "yahoo_access_token.txt"
    league_key = args.league.removeprefix("yahoo-")
    today = args.today

    store = Store(args.store)
    periods = [p for p in store.matchup_periods(args.league) if p["end"] < today]
    if not periods:
        print(f"no completed weeks before {today}; nothing to backfill")
        return 0

    token = token_path.read_text().strip()
    refreshed = False
    stored_teams = 0
    for period in periods:
        week = period["week"]
        try:
            raw = fetch_scoreboard(league_key, week, token)
        except RuntimeError as exc:
            if refreshed or "401" not in str(exc):
                raise
            token = refresh_token_from_files(
                token_path=token_path,
                pkce_path=data_dir / "yahoo_pkce.json",
                secret_path=data_dir / "yahoo_token.json")
            refreshed = True
            raw = fetch_scoreboard(league_key, week, token)
        stats = parse_team_stats(raw)
        for team_key, values in sorted(stats.items()):
            store.add_matchup_team_stats(args.league, week, team_key, values, today)
            stored_teams += 1
        line = " · ".join(
            f"{k.removeprefix(league_key + '.')} "
            f"{values['pts']:.0f}p {values['reb']:.0f}r {values['ast']:.0f}a "
            f"{values['stl']:.0f}s {values['blk']:.0f}b {values['fg3m']:.0f}t "
            f"{values['tov']:.0f}to {values['fg_pct']:.3f}/{values['ft_pct']:.3f}"
            for k, values in sorted(stats.items()))
        print(f"week {week} ({period['start']}→{period['end']}) "
              f"{len(stats)} teams: {line}")

    print(f"stored {stored_teams} team-weeks across {len(periods)} weeks "
          f"(known_from {today})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
