"""Tasks 3.12 / 3.13 — why does H₀ lose to the static G-score board?

Two hypotheses from `results.md`, tested together because they share a replay:

* **3.12** the single-representative-opponent simplification. H₀ optimises against one stand-in
  and is graded against eleven, so conceding a category is cheap in the objective and ruinous
  in the grading. Three arms separate "the stand-in was arbitrary" (``STRONGEST``) from "a
  stand-in is the wrong model" (``FIELD``).
* **3.13** all-play-all grading may itself penalise concentration, since real H2H plays one
  opponent a week — which is the setting punting is *for*. ``--schedule`` grades a rotating
  round-robin instead.

All arms draft as separate seats in the *same* room against the same bots and are graded on the
same weeks; running them as separate replays would confound the comparison with whichever pool
each happened to face. ``--seasons`` replicates the whole thing on independent seasons, which
is the difference between a result and an anecdote.

    FANTASY_GM_DATA_DIR=/Users/tim/projects/fantasy-nba-gm/data \
        python scripts/opponent_model_replay.py --seasons 2023-24,2024-25,2025-26
"""

from __future__ import annotations

import argparse
import time

from fantasy_gm.config import Config
from fantasy_gm.data.store import Store
from fantasy_gm.draft.hscore import OpponentModel
from fantasy_gm.draft.replay import format_replay, run_draft_replay
from fantasy_gm.draft.settings import DraftSettings
from fantasy_gm.draft.xscore import xscore_basis

ARMS = (OpponentModel.REPRESENTATIVE, OpponentModel.STRONGEST, OpponentModel.FIELD)


def run_one(store, season: str, args, schedule: bool) -> dict:
    settings = DraftSettings()
    basis = xscore_basis(store, season, pool_size=args.pool)
    t0 = time.time()
    results = run_draft_replay(
        store,
        season,
        basis,
        settings,
        rotations=args.rotations,
        seed=args.seed,
        engine_steps=args.steps,
        pool_size=args.pool,
        opponent_arms=ARMS,
        schedule=schedule,
    )
    grading = "schedule" if schedule else "all-play-all"
    print(f"\n=== {season}  grading={grading}  ({time.time() - t0:.0f}s) ===\n")
    print(format_replay(results))
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2025-26", help="comma-separated")
    ap.add_argument("--rotations", type=int, default=12)
    ap.add_argument("--steps", type=int, default=5)
    ap.add_argument("--pool", type=int, default=180)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--schedule", action="store_true", help="also run schedule grading (3.13)")
    args = ap.parse_args()

    store = Store(Config().db_path)
    seasons = [s.strip() for s in args.seasons.split(",") if s.strip()]
    gradings = [False, True] if args.schedule else [False]

    print(f"rotations={args.rotations} steps={args.steps} pool={args.pool} seed={args.seed}")

    pooled: dict[tuple[str, bool], dict] = {}
    for schedule in gradings:
        for season in seasons:
            pooled[(season, schedule)] = run_one(store, season, args, schedule)

    # Pooled across seasons: the per-season runs are independent replications, so the mean is
    # the number to quote and the spread is the honest error bar.
    for schedule in gradings:
        rows = [pooled[(s, schedule)] for s in seasons]
        if len(rows) < 2:
            continue
        grading = "schedule" if schedule else "all-play-all"
        print(f"\n=== pooled over {len(seasons)} seasons  grading={grading} ===\n")
        names = sorted(rows[0], key=lambda n: -sum(r[n].category_win_rate for r in rows))
        width = max(len(n) for n in names)
        print(f"{'strategy':<{width}} {'cat win%':>10} {'matchup%':>10}   per-season cat%")
        for name in names:
            cats = [100 * r[name].category_win_rate for r in rows]
            mus = [100 * r[name].matchup_win_rate for r in rows]
            spread = "  ".join(f"{c:.1f}" for c in cats)
            print(f"{name:<{width}} {sum(cats) / len(cats):>9.1f}% "
                  f"{sum(mus) / len(mus):>9.1f}%   [{spread}]")


if __name__ == "__main__":
    main()
