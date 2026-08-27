"""Task 3.14 — what is positional assignment worth, and is the answer real?

`draft/assignment.py` has been built, pinned by tests, and uncalled since task 3.3. Wiring it
in is three changes, not one, and this script measures the third:

* **step 0** — `eligible_positions` expands the store's coarse G/F/C listings into the fine
  slot vocabulary. Without it `RosterSlot.accepts` was empty for every guard and forward.
* **step 1** — `simulate_standings(lineups=...)` grades a *starting lineup*; the bench scores
  nothing.
* **step 2** — `HScoreEngine(positional=True)` scores a roster over its best legal lineup and
  stops valuing future picks that have no open slot to fill.

**The cell that separates a finding from an artifact is the middle one.** Turning on lineup
grading changes what every team is worth, H₀'s and the null's alike, and a position-blind
board that under-drafts centres is punished by the grading whether or not H₀ knows anything
about positions. So three arms, not two:

    blind        no lineup grading, engine blind      the recorded task 3.8 room
    grade-only   lineup grading, engine blind         how much the grading alone moves H₀
    full         lineup grading, engine positional    the change under test

Each is differenced against **its own null** — the same seat drafted by the static G-score
board in the same room, under common random numbers — because the two rooms' absolute rates
are not comparable: lineup grading drops three players from every team and the unlisted from
the pool. `full` minus `grade-only` is the part attributable to the objective term.

Scarcity scopes the result before it is run: centre is the only binding position in our pool
at 1.25-1.29x forced demand (`scripts/position_coverage.py`), guards and forwards run about
six times over-supplied. Whatever this measures, it measures it through centre.

Usage::

    python scripts/positional_assignment.py <season> <most_categories|each_category> \
        [out.json] [g_score|adp]

The default ``g_score`` field is the paper's room, where H0 already leads. ``adp`` is the room
where H0 *loses* (task 3.15, 0/6 runs) -- and since 3.14 exists to explain that deficit, the
adp pass is the one that bears on it. Measuring only the g_score room answers a different
question: whether positional assignment adds where H0 is already ahead.
"""

from __future__ import annotations

import json
import sys

from fantasy_gm.config import Config
from fantasy_gm.data.store import Store
from fantasy_gm.draft.papersim import run_paper_sim
from fantasy_gm.draft.settings import Objective

ARM = "h_paper"          # the paper-faithful engine; `h_score` is the shipped config
N_SEASONS = 2000
SEED = 7
ENGINE_STEPS = 8

# (cell, positional grading, engine optimises over the lineup)
CELLS = (
    ("blind", False, False),
    ("grade-only", True, False),
    ("full", True, True),
)


def main(
    season: str, objective_name: str, out_path: str | None = None, field: str = "g_score"
) -> None:
    store = Store(Config().db_path)
    objective = Objective(objective_name)

    def run(arm: str, positional: bool, engine_positional: bool):
        return run_paper_sim(
            store, season, objective=objective, arm=arm, n_seasons=N_SEASONS, seed=SEED,
            engine_steps=ENGINE_STEPS, positional=positional,
            engine_positional=engine_positional, field=field,
        )

    # The null depends only on the grading, not on the engine flag — the G-score arm has no
    # engine. Computed once per grading mode and shared, which is not an optimisation but a
    # correctness property: `grade-only` and `full` are then differenced against the *same*
    # null rather than two independent estimates of it.
    nulls = {positional: run("g_score", positional, False) for positional in (False, True)}

    rows = []
    for cell, positional, engine_positional in CELLS:
        a = run(ARM, positional, engine_positional)
        b = nulls[positional]
        rows.append({
            "season": season,
            "objective": objective.value,
            "field": field,
            "cell": cell,
            "positional_grading": positional,
            "engine_positional": engine_positional,
            "arm": ARM,
            "n_seasons": N_SEASONS,
            "title": a.mean_title_rate, "null_title": b.mean_title_rate,
            "title_delta_pp": 100 * (a.mean_title_rate - b.mean_title_rate),
            "cat": a.mean_cat_win_rate, "null_cat": b.mean_cat_win_rate,
            "cat_delta_pp": 100 * (a.mean_cat_win_rate - b.mean_cat_win_rate),
            "matchup": a.mean_matchup_win_rate, "null_matchup": b.mean_matchup_win_rate,
            "matchup_delta_pp": 100 * (a.mean_matchup_win_rate - b.mean_matchup_win_rate),
            "seats_ahead": sum(
                1 for x, y in zip(a.seats, b.seats, strict=True)
                if x.cat_win_rate > y.cat_win_rate
            ),
            "per_seat": [
                {"seat": x.seat,
                 "title": x.title_rate, "null_title": y.title_rate,
                 "cat": x.cat_win_rate, "null_cat": y.cat_win_rate,
                 "matchup": x.matchup_win_rate, "null_matchup": y.matchup_win_rate}
                for x, y in zip(a.seats, b.seats, strict=True)
            ],
        })
        print(f"  {cell} done", flush=True)

    print(
        f"\n{'cell':<11} {'grading':<8} {'engine':<7} "
        f"{'title Δ':>9} {'cat Δ':>8} {'matchup Δ':>10} {'seats>':>8}"
    )
    for r in rows:
        print(
            f"{r['cell']:<11} {str(r['positional_grading']):<8} "
            f"{str(r['engine_positional']):<7} {r['title_delta_pp']:>+8.2f}pp "
            f"{r['cat_delta_pp']:>+7.2f}pp {r['matchup_delta_pp']:>+9.2f}pp "
            f"{r['seats_ahead']:>6}/12"
        )

    if out_path:
        with open(out_path, "w") as fh:
            json.dump(rows, fh, indent=2)
        print(f"\nwrote {out_path}")
    print("RUN COMPLETE", flush=True)


if __name__ == "__main__":
    main(
        sys.argv[1],
        sys.argv[2],
        sys.argv[3] if len(sys.argv) > 3 else None,
        sys.argv[4] if len(sys.argv) > 4 else "g_score",
    )
