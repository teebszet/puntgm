"""Command-line entry point: ``backfill``, ``simulate``, and ``recommend``.

The ``recommend`` path is perspective-scoped (``--league`` + ``--team``) and writes every
call to the append-only recommendation log, exercising the full data -> engine -> log path.
"""

from __future__ import annotations

import argparse
import sys

from fantasy_gm.config import ALL_SEASONS, PRIMARY_SEASON, Config
from fantasy_gm.data.cache import RawCache
from fantasy_gm.data.simulate import simulate_league
from fantasy_gm.data.store import Store
from fantasy_gm.data.synthetic import seed_synthetic_season
from fantasy_gm.engine.engine import DecisionEngine
from fantasy_gm.log.reclog import RecommendationLog


def _store(config: Config) -> Store:
    config.ensure_dirs()
    return Store(config.db_path)


def cmd_backfill(args: argparse.Namespace) -> int:
    config = Config()
    store = _store(config)
    if args.synthetic:
        counts = seed_synthetic_season(store, season=args.season, seed=args.seed)
        print(f"[synthetic] season {args.season}: "
              f"{counts['players']} players, {counts['games']} games, {counts['logs']} logs")
        return 0
    from fantasy_gm.data.nba_source import backfill_season

    cache = RawCache(config.cache_dir)
    n = backfill_season(store, args.season, cache)
    print(f"[nba_api] season {args.season}: {n} player-log rows")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    config = Config()
    store = _store(config)
    league_id = simulate_league(
        store, season=args.season, seed=args.seed, n_teams=args.teams,
        roster_size=args.roster, cadence=args.cadence,
    )
    teams = store.team_ids(league_id)
    print(f"created simulated league {league_id} ({len(teams)} teams, cadence={args.cadence})")
    return 0


def cmd_recommend(args: argparse.Namespace) -> int:
    config = Config()
    store = _store(config)
    engine = DecisionEngine(config)
    recs = engine.recommend(store, args.league, args.team, args.as_of, top_n=args.top)
    if not recs:
        print("no candidates (is the league backfilled and simulated?)", file=sys.stderr)
        return 1
    log = RecommendationLog(store)
    written = log.append(recs)
    p = recs[0].perspective
    print(f"perspective: league={p.league_id} team={p.team_id} "
          f"period={p.period_index} opp={p.opponent_team_id or '-'} as_of={args.as_of}")
    for r in recs:
        print(f"  #{r.rank:<2} {r.candidate_name:<18} score={r.score:<9} "
              f"conf={r.confidence:<5} — {r.reasoning}")
    print(f"logged {written} recommendation(s); log now holds {log.count()} row(s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fantasy-gm", description="Fantasy NBA GM CLI")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("backfill", help="backfill an NBA season into the local store")
    b.add_argument("--season", default=PRIMARY_SEASON, choices=ALL_SEASONS)
    b.add_argument("--synthetic", action="store_true",
                   help="generate a deterministic synthetic season (offline)")
    b.add_argument("--seed", type=int, default=7)
    b.set_defaults(func=cmd_backfill)

    s = sub.add_parser("simulate", help="generate a simulated league over a backfilled season")
    s.add_argument("--season", default=PRIMARY_SEASON, choices=ALL_SEASONS)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--teams", type=int, default=8)
    s.add_argument("--roster", type=int, default=10)
    s.add_argument("--cadence", default="weekly-lock", choices=["weekly-lock", "daily-change"])
    s.set_defaults(func=cmd_simulate)

    r = sub.add_parser("recommend", help="rank waiver candidates for a team's upcoming window")
    r.add_argument("--as-of", dest="as_of", required=True, help="decision date YYYY-MM-DD")
    r.add_argument("--league", required=True)
    r.add_argument("--team", required=True)
    r.add_argument("--top", type=int, default=10)
    r.set_defaults(func=cmd_recommend)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
