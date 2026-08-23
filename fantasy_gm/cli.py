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
    proj = Projector(config, method=args.method).project(store, args.league, args.team, args.as_of)
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
    from fantasy_gm.validation import measure_autocorrelation, measure_category_cv

    config = Config()
    store = _store(config)
    cv = measure_category_cv(store, args.season)
    if not cv:
        print("no data to measure (backfill a season first)", file=sys.stderr)
        return 1
    ac = measure_autocorrelation(store, args.season)
    print(f"measured category variance (season {args.season})")
    print("  NOTE: only meaningful on REAL data — synthetic is generated from the "
          "assumptions it would 'validate'.")
    print(f"  {'category':8} {'CV (σ/μ)':>10} {'lag1-autocorr':>14}")
    for c in sorted(cv, key=cv.get, reverse=True):
        print(f"  {c:8} {cv[c]:>10.3f} {ac.get(c, float('nan')):>14.3f}")
    print(f"highest variance: {max(cv, key=cv.get)}   lowest: {min(cv, key=cv.get)}")
    print("  autocorr ≈ 0  -> games independent; measured σ suffices, no multiplier needed.")
    print("  (fg_pct/ft_pct omitted: they use a volume-weighted binomial model, not CV — "
          "their variance model is a separate, not-yet-validated check.)")
    return 0


def cmd_wire(args: argparse.Namespace) -> int:
    from fantasy_gm.engine.wire import WireAnalyzer

    config = Config()
    store = _store(config)
    wa = WireAnalyzer(config).analyze(store, args.league, args.team, args.as_of)
    if not wa.perspective.opponent_team_id:
        print("no active matchup for that team/date", file=sys.stderr)
        return 1
    p = wa.perspective
    print(f"wire analysis: league={p.league_id} team={p.team_id} "
          f"opp={p.opponent_team_id} as_of={args.as_of}")
    print("bundle depth on wire: "
          + ", ".join(f"{b}={n}" for b, n in wa.bundle_depth.items()))
    if not wa.options:
        print("  no contested categories")
    for o in wa.options:
        if o.verdict == "infeasible":
            print(f"  {o.category:6} INFEASIBLE — no available add improves it")
        else:
            conc = ", ".join(f"{c} {d:+.2f}" for c, d in o.concedes.items()) or "nothing"
            print(f"  {o.category:6} {o.verdict:9} add {o.add_name} (+{o.gain:.2f}); "
                  f"concedes: {conc}; net cats {o.net_categories:+d}")
    return 0


def cmd_values(args: argparse.Namespace) -> int:
    from fantasy_gm.valuation import player_values

    config = Config()
    store = _store(config)
    vals = player_values(store, args.season)
    if not vals:
        print("no data to value (backfill a season first)", file=sys.stderr)
        return 1
    top = sorted(vals.items(), key=lambda kv: (-kv[1], kv[0]))[: args.top]
    print(f"top {len(top)} players by 9-cat z-value (season {args.season})")
    for pid, z in top:
        row = store.conn.execute(
            "SELECT player_name FROM player_logs WHERE player_id = ? LIMIT 1", (pid,)
        ).fetchone()
        print(f"  {z:>6.2f}  {row['player_name'] if row else pid}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    from fantasy_gm.data.simulate import simulate_league
    from fantasy_gm.engine.scoring import replay_season

    config = Config()
    store = _store(config)
    tot = {"moves": 0, "hit": 0, "helped": 0, "flips": 0, "unflips": 0, "delta_sum": 0.0}
    cal = {"safe": [0, 0], "contested": [0, 0], "gone": [0, 0]}
    for seed in range(1, args.leagues + 1):
        lg = simulate_league(store, season=args.season, seed=seed, n_teams=12, roster_size=13)
        r = replay_season(store, lg, config)
        for k in tot:
            tot[k] += r.get(k, 0)
        for lab, (w, t) in r.get("calibration", {}).items():
            cal[lab][0] += w
            cal[lab][1] += t
    n = tot["moves"]
    if n == 0:
        print("no moves to grade (backfill a season first)", file=sys.stderr)
        return 1
    cats_n = sum(t for _w, t in cal.values())
    print(f"track record — season {args.season}, {args.leagues} simulated league(s)")
    print(f"\nProjection calibration ({cats_n} category calls):")
    for lab in ("safe", "contested", "gone"):
        w, t = cal[lab]
        if t:
            print(f"  called {lab:9}: won {w / t:.0%}  (n={t})")
    print(f"\nWaiver calls ({n} graded):")
    print(f"  add out-produced the drop in the TARGET category:  {tot['hit'] / n:.0%}")
    print(f"  avg realized target-category gain per call:        {tot['delta_sum'] / n:+.2f}")
    print(f"  categories flipped to you / away* :                {tot['flips']} / {tot['unflips']}")
    print("  * opponent-dependent — static opponents this pass (see deferred baseline).")
    return 0


def cmd_player_index(args: argparse.Namespace) -> int:
    from fantasy_gm.data.player_index import ingest_player_index

    config = Config()
    store = _store(config)
    cache = RawCache(config.cache_dir)
    counts = ingest_player_index(store, args.season, cache, args.known_from,
                                 dry_run=args.dry_run, as_of=args.as_of)
    tag = "dry-run" if args.dry_run else "stored"
    print(f"[playerindex] season {args.season} ({tag}), known_from {args.known_from}")
    print(f"  {counts['rows']} player rows, {counts['rostered']} on an NBA roster "
          f"across {counts['teams']} teams")
    print(f"  -> {counts['positions']} position(s), "
          f"{counts['forward_roster']} forward-roster row(s), "
          f"{counts['incoming']} incoming player(s) with no game logs")
    if not args.dry_run:
        print("  depth chart is DERIVED by ranking each new roster on its players' own "
              "minutes history (A-DRAFT-12) — override by hand for names you disagree with")
    return 0


def cmd_reconstruct_rosters(args: argparse.Namespace) -> int:
    """Backtest-only: opening-night rosters for a past season, from its own first box scores.

    Prints what the reconstruction could not see as loudly as what it wrote — a roster set
    that misses everyone who never played is exactly the kind of thing that quietly turns a
    backtest into a flattering one.
    """
    from fantasy_gm.data.reconstruct import ReconstructionError, reconstruct_forward_roster

    config = Config()
    store = _store(config)
    try:
        c = reconstruct_forward_roster(store, args.season, args.as_of,
                                       window_days=args.window_days, dry_run=args.dry_run)
    except ReconstructionError as e:
        print(f"cannot reconstruct: {e}", file=sys.stderr)
        return 1
    tag = "dry-run" if args.dry_run else "stored"
    print(f"[reconstruct] season {args.season} ({tag}), known_from {args.as_of}")
    print(f"  {c['forward_roster']} forward-roster row(s) across {c['teams']} teams; "
          f"{c['movers']} player(s) on a different team than they last played for")
    print(f"  opening window {c['season_start']}..{c['cutoff']} ({c['window_days']}d): "
          f"{c['opening_window']} of {c['players_with_logs']} players with logs; "
          f"{c['late_debut_excluded']} later debut(s) excluded as midseason arrivals")
    print("  LOOKAHEAD COMPROMISE — backtest instrument, never a live source: team identity is "
          "read from rows dated after the cut, and anyone who missed the whole season is "
          "invisible here, so this pool is biased toward players who stayed healthy")
    return 0


def cmd_projections(args: argparse.Namespace) -> int:
    from fantasy_gm.projections.derived import DerivedProjectionSource

    config = Config()
    store = _store(config)
    src = DerivedProjectionSource(store)
    projections = src.project(args.season, args.as_of)
    if not projections:
        print("nothing to project (is a season backfilled?)", file=sys.stderr)
        return 1
    fit = src.fit(args.season, args.as_of)
    print(f"derived projections — season {args.season}, as_of {args.as_of} "
          f"({len(projections)} players)")
    print(f"  minutes fit: {fit.minutes.n_players} players, "
          f"shrinkage {fit.minutes.shrinkage_games:.1f} games, "
          f"drift σ {fit.minutes.drift_var ** 0.5:.2f} min, "
          f"team-change drift ×{fit.minutes.team_change_drift_mult:.2f} "
          f"({fit.minutes.n_movers} movers)")
    print(f"  availability fit: prior {fit.games.prior_games:.1f} games, "
          f"pool rate {fit.games.pool_rate:.3f} [{fit.games.basis}]")
    fallbacks = sorted(k for k, v in fit.minutes.basis.items() if v == "fallback")
    if fallbacks:
        print(f"  UNMEASURED (fallback): {', '.join(fallbacks)}")

    ranked = sorted(
        projections.items(),
        key=lambda kv: -(kv[1].expected_games * kv[1].estimate("pts").per_game_mean),
    )[: args.top]
    print(f"\n  {'player':<24} {'min':>5} {'gp':>5} {'pts':>5} {'reb':>5} {'ast':>5} "
          f"{'fg%':>5} {'±μ':>5}  basis")
    for pid, p in ranked:
        row = store.conn.execute(
            "SELECT player_name FROM player_logs WHERE player_id = ? LIMIT 1", (pid,)
        ).fetchone()
        name = row["player_name"] if row else pid
        fg = p.percentage("fg_pct")
        print(f"  {name[:24]:<24} {p.notes.get('minutes', '-'):>5} {p.expected_games:>5.1f} "
              f"{p.estimate('pts').per_game_mean:>5.1f} {p.estimate('reb').per_game_mean:>5.1f} "
              f"{p.estimate('ast').per_game_mean:>5.1f} "
              f"{(f'{fg:.3f}' if fg is not None else '-'):>5} "
              f"{p.estimate('pts').mean_stderr:>5.2f}  {p.basis}")
    return 0


def cmd_projection_backtest(args: argparse.Namespace) -> int:
    from fantasy_gm.projections.availability import measure_games_production_correlation
    from fantasy_gm.projections.backtest import backtest_projection

    config = Config()
    store = _store(config)
    report = backtest_projection(store, args.season, as_of=args.as_of, mode=args.mode)
    if report is None:
        print(f"season {args.season} is not in the store", file=sys.stderr)
        return 1
    print(f"projection backtest — season {args.season} [{report.mode}]")
    print(f"  fit through {report.as_of}; scored on {report.eval_start}..{report.eval_end} "
          f"({report.n_players} players)")
    if "blocked" in report.notes:
        print(f"  BLOCKED: {report.notes['blocked']}", file=sys.stderr)
        return 1
    if "proxy" in report.notes:
        print(f"  NOTE: {report.notes['proxy']}")
    print(f"\n  {report.model.line()}")
    print(f"  {report.naive.line()}")
    print(f"\n  {'category':<8} {'model':>8} {'naive':>8} {'better?':>8}")
    for c in sorted(report.model.category_mae):
        m, n = report.model.category_mae[c], report.naive.category_mae.get(c, float('nan'))
        print(f"  {c:<8} {m:>8.3f} {n:>8.3f} {'yes' if m < n else 'no':>8}")
    beaten = report.categories_beaten()
    print(f"\n  categories beaten: {len(beaten)}/{len(report.model.category_mae)} "
          f"({', '.join(beaten) or 'none'})")
    print(f"  {report.verdict()}")
    passed = report.beats_naive_minutes and report.minutes_edge_sigmas >= 2.0

    corr = measure_games_production_correlation(store, args.season)
    if corr:
        print(f"\n  A-DRAFT-7 (is E[games] separable from E[per-game]?), "
              f"n={corr['n_players']:.0f}:")
        print(f"    corr(games played, minutes/g) = {corr['corr_games_minutes']:+.3f}")
        print(f"    corr(games played, pts/g)     = {corr['corr_games_scoring']:+.3f}")
        if "post_absence_minutes_ratio" in corr:
            print(f"    minutes on return from an absence = "
                  f"{corr['post_absence_minutes_ratio']:.3f}× own average "
                  f"(n={corr['n_returns']:.0f} returns)")
    return 0 if passed else 2


def cmd_adp(args: argparse.Namespace) -> int:
    from fantasy_gm.projections.adp import adp_for_pool, ingest_adp_file

    config = Config()
    store = _store(config)
    result = ingest_adp_file(store, args.file, args.season, args.known_from, source=args.source)
    print(f"ADP ingest — season {args.season}, known_from {args.known_from}, "
          f"source {args.source}")
    print(f"  {result.rows} row(s) -> {result.stored} stored; "
          f"{len(result.unresolved)} unresolved name(s); "
          f"{len(result.missing_adp)} row(s) with no average pick")
    for name in result.unresolved[:10]:
        print(f"    unresolved: {name}")
    pool = store.draft_pool_asof(args.season, args.known_from)
    priced = adp_for_pool(store, args.season, args.known_from, pool, source=args.source)
    unpriced = [p for p, a in priced.items() if a is None]
    print(f"  draft pool {len(pool)}: {len(pool) - len(unpriced)} priced, "
          f"{len(unpriced)} with no ADP (represented as absent, not as a default)")
    return 0


def cmd_manage(args: argparse.Namespace) -> int:
    """Give every team in a simulated league a baseline manager, so the wire drains."""
    from fantasy_gm.data.manage import apply_baseline_management

    config = Config()
    store = _store(config)
    try:
        report = apply_baseline_management(
            store, args.league, config, moves_per_period=args.moves, force=args.force)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{args.league}: {report.moves} moves over {report.periods} periods "
          f"({report.teams} teams, {report.skipped_no_candidate} periods with no useful move)")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Strategy comparison: the engine's calls against naive baselines on identical slots."""
    from fantasy_gm.validation.replay import (
        STRATEGIES,
        by_category,
        extract_slots,
        run_strategies,
        summarize,
    )

    config = Config()
    store = _store(config)
    slots = []
    for lg in args.leagues:
        s = extract_slots(store, lg, config)
        print(f"{lg}: {len(s)} decision slots", file=sys.stderr)
        slots += s
    if not slots:
        print("no decision slots (simulate a league first)", file=sys.stderr)
        return 1

    results = run_strategies(store, slots, args.season, config)
    print(f"\nstrategy comparison — {len(slots)} decision slots, season {args.season}")
    print("(same target category and drop for every strategy; only the ADD varies)\n")
    print(f"{'strategy':<12}{'n':>6}{'hit':>6}{'tie':>6}{'miss':>6}"
          f"{'hit%':>8}{'decided%':>10}{'±se':>8}{'avg delta':>11}{'add DNP':>9}")
    for name in STRATEGIES:
        s = summarize(results[name])
        if not s["n"]:
            continue
        print(f"{name:<12}{s['n']:>6}{s['hit']:>6}{s['tie']:>6}{s['miss']:>6}"
              f"{s['hit_rate']:>8.3f}{s['hit_rate_decided']:>10.3f}{s['se']:>8.4f}"
              f"{s['avg_delta']:>11.2f}{s['add_dnp']:>9}")

    if args.by_category:
        for name in args.by_category:
            print(f"\n--- {name}, by target category ---")
            for c, s in by_category(results[name]).items():
                print(f"  {c:<8}{s['n']:>5}{s['hit_rate']:>8.3f}{s['avg_delta']:>10.2f}")
    return 0


def cmd_yahoo_check(args: argparse.Namespace) -> int:
    """Verify a token can actually reach the Fantasy API (a valid token is not enough)."""
    from pathlib import Path

    from fantasy_gm.data.yahoo_fetch import check_access

    token = Path(args.token).read_text().strip()
    ok, message = check_access(token)
    print(("OK  " if ok else "FAIL ") + message)
    return 0 if ok else 1


def cmd_yahoo_import(args: argparse.Namespace) -> int:
    """Load a fetched Yahoo snapshot as a real league (read-only, point-in-time)."""
    import json

    from fantasy_gm.data.yahoo_import import ImportRefused, import_snapshot

    config = Config()
    store = _store(config)
    with open(args.snapshot) as fh:
        snapshot = json.load(fh)
    overrides = {}
    if args.overrides:
        with open(args.overrides) as fh:
            overrides = json.load(fh)
    try:
        report = import_snapshot(store, snapshot, overrides=overrides, force=args.force)
    except ImportRefused as exc:
        print(f"import refused: {exc}", file=sys.stderr)
        return 1
    for k, v in report.items():
        print(f"  {k}: {v}")
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
    pr.add_argument("--method", default="normal", choices=["normal", "bootstrap"],
                    help="win-prob method: normal (fast Φ) or bootstrap (more accurate, A3)")
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

    vv = sub.add_parser("values", help="rank players by data-derived 9-cat z-value (A6)")
    vv.add_argument("--season", default=PRIMARY_SEASON, choices=ALL_SEASONS)
    vv.add_argument("--top", type=int, default=20)
    vv.set_defaults(func=cmd_values)

    w = sub.add_parser("wire", help="wire availability by bundle + marginal trade-off per cat (A9)")
    w.add_argument("--as-of", dest="as_of", required=True)
    w.add_argument("--league", required=True)
    w.add_argument("--team", required=True)
    w.set_defaults(func=cmd_wire)

    rp = sub.add_parser("replay", help="grade every recommended call over a season (track record)")
    rp.add_argument("--season", default=PRIMARY_SEASON, choices=ALL_SEASONS)
    rp.add_argument("--leagues", type=int, default=1,
                    help="simulated leagues to average over (more = robuster but slower)")
    rp.set_defaults(func=cmd_replay)

    pi = sub.add_parser("player-index",
                        help="forward-season teams, positions, and rookies from NBA playerindex")
    pi.add_argument("--season", required=True, help="season being entered, e.g. 2026-27")
    pi.add_argument("--known-from", dest="known_from", required=True,
                    help="date this snapshot was taken; reads before it will not see it")
    pi.add_argument("--as-of", dest="as_of", default=None,
                    help="history cut for the derived depth chart (defaults to --known-from)")
    pi.add_argument("--dry-run", action="store_true",
                    help="fetch + parse but don't write (this writes three tables)")
    pi.set_defaults(func=cmd_player_index)

    rr = sub.add_parser("reconstruct-rosters",
                        help="BACKTEST ONLY: opening-night rosters for a past season, "
                             "rebuilt from its own first box scores (lookahead compromise)")
    rr.add_argument("--season", required=True, help="completed season, e.g. 2025-26")
    rr.add_argument("--as-of", dest="as_of", required=True,
                    help="cut before the season starts; stamped as known_from and used as "
                         "the history cut for the derived depth chart")
    rr.add_argument("--window-days", dest="window_days", type=int, default=14,
                    help="days from tip-off a debut still counts as opening-night (default 14)")
    rr.add_argument("--dry-run", action="store_true")
    rr.set_defaults(func=cmd_reconstruct_rosters)

    pj = sub.add_parser("projections",
                        help="forward-season projections from the minutes/role model (2.5-2.9)")
    pj.add_argument("--season", required=True, help="season being projected, e.g. 2026-27")
    pj.add_argument("--as-of", dest="as_of", required=True,
                    help="what was known on this date; nothing after it is read")
    pj.add_argument("--top", type=int, default=25)
    pj.set_defaults(func=cmd_projections)

    pb = sub.add_parser("projection-backtest",
                        help="score the projection method against realized production (A-DRAFT-5)")
    pb.add_argument("--season", default=PRIMARY_SEASON, choices=ALL_SEASONS)
    pb.add_argument("--as-of", dest="as_of", default=None,
                    help="cut date; defaults to the day before the season (cross) or mid-season")
    pb.add_argument("--mode", default=None, choices=["cross-season", "split-season"],
                    help="defaults to cross-season when a prior season is backfilled")
    pb.set_defaults(func=cmd_projection_backtest)

    ad = sub.add_parser("adp", help="ingest a saved Yahoo draft_analysis payload as ADP (2.4)")
    ad.add_argument("--file", required=True, help="saved draft_analysis JSON")
    ad.add_argument("--season", required=True)
    ad.add_argument("--known-from", dest="known_from", required=True,
                    help="date the market snapshot was taken")
    ad.add_argument("--source", default="yahoo")
    ad.set_defaults(func=cmd_adp)
    mg = sub.add_parser("manage",
                        help="give simulated teams a baseline manager so the wire drains")
    mg.add_argument("league", help="league id to manage")
    mg.add_argument("--moves", type=int, default=1, help="moves per team per scoring period")
    mg.add_argument("--force", action="store_true",
                    help="run a second pass (management is not idempotent)")
    mg.set_defaults(func=cmd_manage)

    cp = sub.add_parser("compare",
                        help="engine vs naive baselines on identical decision slots")
    cp.add_argument("leagues", nargs="+", help="league ids to pool slots from")
    cp.add_argument("--season", default=PRIMARY_SEASON, choices=ALL_SEASONS)
    cp.add_argument("--by-category", nargs="*", default=None,
                    metavar="STRATEGY", help="also break these strategies down per category")
    cp.set_defaults(func=cmd_compare)

    yc = sub.add_parser("yahoo-check",
                        help="verify an access token can actually reach the Fantasy API")
    yc.add_argument("--token", default="data/yahoo_access_token.txt",
                    help="file containing the bearer token")
    yc.set_defaults(func=cmd_yahoo_check)

    yi = sub.add_parser("yahoo-import",
                        help="load a fetched Yahoo snapshot as a real league (read-only)")
    yi.add_argument("snapshot", help="path to the JSON snapshot from yahoo_fetch")
    yi.add_argument("--overrides", help="JSON map of yahoo_player_id -> nba_player_id")
    yi.add_argument("--force", action="store_true",
                    help="load despite unresolved players / unverified history")
    yi.set_defaults(func=cmd_yahoo_import)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
