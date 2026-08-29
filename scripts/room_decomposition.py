"""Why does H₀ win the paper's room and lose ours? Vary one axis at a time.

Two experiments in this repo disagree about the same engine, and both are ours:

* :mod:`scripts.paper_validation` — one arm against eleven G-score drafters, twenty-week
  seasons resampled from *played* weeks, differenced against the identical room with a G-score
  board in the arm's seat. **H₀ wins**: +1.2 to +2.2pp category rate over the null, every
  season, and the corrected engine reproduces the published title rate.
* :mod:`scripts.paper_engine_replay` — G-score vs H₀ at adjacent seats among ADP bots, graded
  on the realized season including the weeks a player missed. **H₀ loses**: −1.5 to −6.7pp.

A sign flip that large is not noise, so something between the two rooms owns it. Four things
differ, and until now they differed all at once:

1. **the field** — eleven G-score drafters, or ten ADP bots and one G-score board;
2. **availability** — a season resampled from healthy weeks, or one that charges missed weeks
   to the manager;
3. **the metric** — share of seasons finished first, or pooled category win rate;
4. **the room design** — one seat swapped and differenced against its own null, or two similar
   arms seated adjacently, each stripping the other's next pick.

This script holds 3 and 4 fixed at the *safe* setting — the paper room reports both metrics, so
the metric is free, and the single-seat swap is the design without the adjacency artifact — and
crosses the other two. Four cells, each with its own null:

                       played weeks        weeks incl. idle
    G-score field      the paper room      availability only
    ADP field          field only          both

If H₀'s edge survives the ADP field and dies when idle weeks are added, availability owns the
flip and H₀'s deficit is a durability problem, not a drafting one — which would put it on the
same axis as everything else this project has measured. If it dies with the field instead, H₀
is a method that only beats opponents it models, which is a much harder product problem.

Every cell is differenced against a null run in the *same* room under common random numbers,
because neither cell's absolute rate is comparable to the other's: a G-score board among ADP
bots wins far more than 1/12 on merit, and idle weeks change what a title is worth.

Usage::

    python scripts/room_decomposition.py <season> <most_categories|each_category> [out.json]
"""

from __future__ import annotations

import json
import sys
import time

from fantasy_gm.data.store import Store
from fantasy_gm.draft.papersim import run_paper_sim
from fantasy_gm.draft.settings import Objective

DB = "/Users/tim/projects/fantasy-nba-gm/data/fantasy_gm.sqlite"
N_SEASONS = 2000
ENGINE_STEPS = 8

# The two engines that matter. `h_score` is what every historical number in `results.md` was
# measured on; `h_paper` is the corrected build that reproduces the publication. Carrying both
# costs one extra arm per cell and means a flip cannot be blamed on the correction.
ARMS_UNDER_TEST = ["h_score", "h_paper"]

CELLS = [
    ("paper", "g_score", False),      # the published room, reproduced
    ("field", "adp", False),          # + our field
    ("idle", "g_score", True),        # + our availability
    ("both", "adp", True),            # both — should land near the replay result
]


def main(season: str, objective: str, out_path: str | None = None) -> None:
    obj = Objective(objective)
    store = Store(DB)
    rows = []

    for cell, field, idle in CELLS:
        runs = {}
        for arm in ["g_score", *ARMS_UNDER_TEST]:
            t = time.time()
            runs[arm] = run_paper_sim(
                store, season, objective=obj, arm=arm,
                n_seasons=N_SEASONS, engine_steps=ENGINE_STEPS,
                field=field, include_idle_weeks=idle,
            )
            print(
                f"{season} {obj.value:<16} {cell:<6} {arm:<9} "
                f"title {100 * runs[arm].mean_title_rate:5.1f}%  "
                f"cat {100 * runs[arm].mean_cat_win_rate:5.1f}%  "
                f"({time.time() - t:.0f}s)",
                flush=True,
            )

        null = runs["g_score"]
        for arm in ARMS_UNDER_TEST:
            r = runs[arm]
            rows.append({
                "season": season,
                "objective": obj.value,
                "cell": cell,
                "field": field,
                "include_idle_weeks": idle,
                "arm": arm,
                "n_seasons": N_SEASONS,
                "engine_steps": ENGINE_STEPS,
                "title": r.mean_title_rate,
                "null_title": null.mean_title_rate,
                "title_delta_pp": 100 * (r.mean_title_rate - null.mean_title_rate),
                "cat": r.mean_cat_win_rate,
                "null_cat": null.mean_cat_win_rate,
                "cat_delta_pp": 100 * (r.mean_cat_win_rate - null.mean_cat_win_rate),
                "matchup": r.mean_matchup_win_rate,
                "null_matchup": null.mean_matchup_win_rate,
                "matchup_delta_pp": 100 * (
                    r.mean_matchup_win_rate - null.mean_matchup_win_rate
                ),
                "seats_ahead": sum(
                    1 for a, b in zip(r.seats, null.seats, strict=True)
                    if a.cat_win_rate > b.cat_win_rate
                ),
                "per_seat": [
                    {"seat": a.seat, "title": a.title_rate, "null_title": b.title_rate,
                     "cat": a.cat_win_rate, "null_cat": b.cat_win_rate,
                     "matchup": a.matchup_win_rate, "null_matchup": b.matchup_win_rate}
                    for a, b in zip(r.seats, null.seats, strict=True)
                ],
            })

    print(
        f"\n{'cell':<6} {'field':<8} {'idle':<5} {'arm':<9} "
        f"{'title Δ':>9} {'cat Δ':>8} {'matchup Δ':>10} {'seats>':>8}"
    )
    for r in rows:
        print(
            f"{r['cell']:<6} {r['field']:<8} {str(r['include_idle_weeks']):<5} "
            f"{r['arm']:<9} {r['title_delta_pp']:>+8.2f}pp {r['cat_delta_pp']:>+7.2f}pp "
            f"{r['matchup_delta_pp']:>+9.2f}pp {r['seats_ahead']:>6}/12"
        )

    if out_path:
        with open(out_path, "w") as fh:
            json.dump(rows, fh, indent=2)
        print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
