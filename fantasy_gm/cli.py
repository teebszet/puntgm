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
    counts = backfill_season(store, args.season, cache, dry_run=args.dry_run)
    tag = "dry-run" if args.dry_run else "stored"
    print(f"[nba_api] season {args.season} ({tag}): "
          f"{counts['rows']} rows -> {counts['games']} games, {counts['logs']} logs, "
          f"{counts['usage']} usage snapshots")
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


def cmd_project(args: argparse.Namespace) -> int:
    from fantasy_gm.engine.projection import Projector

    config = Config()
    store = _store(config)
    proj = Projector(config).project(store, args.league, args.team, args.as_of)
    if not proj.opponent_id:
        print("no active matchup for that team/date", file=sys.stderr)
        return 1
    print(f"projection: league={proj.league_id} team={proj.team_id} "
          f"period={proj.period_index} opp={proj.opponent_id} as_of={args.as_of}")
    for c, p in proj.categories.items():
        print(f"  {c:7} mine={p.mine_total:8.1f} opp={p.opp_total:8.1f} "
              f"win={p.win_prob:.2f}  {p.label}")
    print(f"contested: {', '.join(proj.contested()) or '-'}")
    return 0


def cmd_feed(args: argparse.Namespace) -> int:
    from fantasy_gm.engine.reconcile import Reconciler
    from fantasy_gm.engine.signals import SignalEngine
    from fantasy_gm.log.reclog import FeedLog
    from fantasy_gm.models import Perspective

    config = Config()
    store = _store(config)
    signals = SignalEngine(config).detect(store, args.league, args.team, args.as_of)
    moves = Reconciler(config).reconcile(store, args.league, args.team, args.as_of)

    strong = [s for s in signals if s.band == "strong"]
    shown = strong if (strong and not args.all) else signals
    print(f"live signals (as_of {args.as_of}) — {len(strong)} strong / {len(signals)} total")
    for s in shown[: args.top]:
        print(f"  [{s.band:6}] {s.signal_type:15} {s.subject_name:16} "
              f"str={s.strength:<5} — {s.evidence}")

    print(f"\nend-of-day reconciliation — {len(moves)} candidate move(s)")
    for m in moves:
        flag = " (drops unplayed!)" if m.drops_unplayed else ""
        deltas = ", ".join(f"{c} {b:.2f}->{a:.2f}" for c, (b, a) in m.projected_impact.items())
        print(f"  {m.line_of_play}: drop {m.drop_name} -> add {m.add_name}"
              f"  conf={m.confidence}{flag}")
        print(f"      projected: {deltas}")

    if moves or signals:
        log = FeedLog(store)
        m = store.matchup_for_team(args.league, args.team, args.as_of)
        opp = (m.team_b if m.team_a == args.team else m.team_a) if m else ""
        persp = Perspective(args.league, args.team, m.period_index if m else -1, opp)
        ns = log.append_signals(signals, persp)
        nm = log.append_moves(moves)
        print(f"\nlogged {ns} signal(s) + {nm} move(s); "
              f"feed log holds {log.signal_count()} / {log.move_count()}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from fantasy_gm.validation import derive_variance_profile, measure_category_cv

    config = Config()
    store = _store(config)
    cv = measure_category_cv(store, args.season)
    if not cv:
        print("no data to measure (backfill a season first)", file=sys.stderr)
        return 1
    profile = derive_variance_profile(cv)
    print(f"measured category variance (season {args.season})")
    print("  NOTE: only meaningful on REAL data — synthetic is generated from the "
          "assumptions it would 'validate'.")
    print(f"  {'category':8} {'CV (σ/μ)':>10} {'multiplier':>11}")
    for c in sorted(cv, key=cv.get, reverse=True):
        print(f"  {c:8} {cv[c]:>10.3f} {profile[c]:>11.3f}")
    hi = max(cv, key=cv.get)
    lo = min(cv, key=cv.get)
    print(f"highest variance: {hi}   lowest: {lo}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fantasy-gm", description="Fantasy NBA GM CLI")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("backfill", help="backfill an NBA season into the local store")
    b.add_argument("--season", default=PRIMARY_SEASON, choices=ALL_SEASONS)
    b.add_argument("--synthetic", action="store_true",
                   help="generate a deterministic synthetic season (offline)")
    b.add_argument("--dry-run", action="store_true",
                   help="real backfill: fetch + parse but don't store (sanity check)")
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

    pr = sub.add_parser("project", help="project category outcomes for a team's matchup")
    pr.add_argument("--as-of", dest="as_of", required=True)
    pr.add_argument("--league", required=True)
    pr.add_argument("--team", required=True)
    pr.set_defaults(func=cmd_project)

    fd = sub.add_parser("feed", help="live signals + end-of-day reconciliation for a team")
    fd.add_argument("--as-of", dest="as_of", required=True)
    fd.add_argument("--league", required=True)
    fd.add_argument("--team", required=True)
    fd.add_argument("--top", type=int, default=10)
    fd.add_argument("--all", action="store_true", help="show soft signals too")
    fd.set_defaults(func=cmd_feed)

    v = sub.add_parser("validate", help="measure category variance from backfilled data (A1)")
    v.add_argument("--season", default=PRIMARY_SEASON, choices=ALL_SEASONS)
    v.set_defaults(func=cmd_validate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
