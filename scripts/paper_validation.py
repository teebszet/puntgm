"""Task 3.8 — does our H₀ reproduce the published simulation, and if not, which part is ours?

Runs the published experiment (one H₀ drafter vs eleven G-score drafters, twelve teams,
thirteen rounds, twenty-week resampled seasons) at every draft seat, for one season and one
objective per invocation, over a ladder of engine configurations.

**Every arm is paired with the null** — the identical room with that seat drafted by a twelfth
G-score drafter — under common random numbers, so what is reported is a difference of paired
estimates. The null is not a formality: a snake over an *odd* number of rounds is strongly
seat-dependent even when every drafter runs the same board, and reading a seat's title rate
against the 1/12 chance baseline credits the seat to the algorithm.

Usage::

    python scripts/paper_validation.py <season> <most_categories|each_category> [out.json]

One invocation per season/objective keeps each run a few minutes; ``merge_paper_validation``
stitches the shards into the table that goes in `results.md`.
"""

from __future__ import annotations

import json
import sys
import time

from fantasy_gm.data.store import Store
from fantasy_gm.draft.papersim import (
    ARMS,
    PAPER_EACH_CATEGORY,
    PAPER_MOST_CATEGORIES,
    run_paper_sim,
)
from fantasy_gm.draft.settings import Objective

DB = "/Users/tim/projects/fantasy-nba-gm/data/fantasy_gm.sqlite"
N_SEASONS = 2000
ENGINE_STEPS = 8


def main(season: str, objective: str, out_path: str | None = None) -> None:
    obj = Objective(objective)
    published = (
        PAPER_MOST_CATEGORIES if obj is Objective.MOST_CATEGORIES else PAPER_EACH_CATEGORY
    )
    store = Store(DB)
    got = {}
    for arm in ARMS:
        t = time.time()
        r = run_paper_sim(
            store, season, objective=obj, arm=arm,
            n_seasons=N_SEASONS, engine_steps=ENGINE_STEPS,
        )
        got[arm] = r
        print(
            f"{season} {obj.value:<16} {arm:<22} "
            f"title {100 * r.mean_title_rate:5.1f}%  "
            f"cat {100 * r.mean_cat_win_rate:5.1f}%  ({time.time() - t:.0f}s)",
            flush=True,
        )

    null = got["g_score"]
    rows = []
    for arm, r in got.items():
        if arm == "g_score":
            continue
        rows.append({
            "season": season,
            "objective": obj.value,
            "arm": arm,
            "config": {k: v for k, v in ARMS[arm].items()},
            "n_seasons": N_SEASONS,
            "engine_steps": ENGINE_STEPS,
            "chance": 1.0 / r.n_teams,
            "published_title_rate": published,
            "title": r.mean_title_rate,
            "null_title": null.mean_title_rate,
            "delta_pp": 100 * (r.mean_title_rate - null.mean_title_rate),
            "cat": r.mean_cat_win_rate,
            "null_cat": null.mean_cat_win_rate,
            "cat_delta_pp": 100 * (r.mean_cat_win_rate - null.mean_cat_win_rate),
            "seats_ahead": sum(
                1 for a, b in zip(r.seats, null.seats, strict=True)
                if a.title_rate > b.title_rate
            ),
            "per_seat": [
                {"seat": a.seat, "title": a.title_rate, "null_title": b.title_rate,
                 "cat": a.cat_win_rate, "null_cat": b.cat_win_rate}
                for a, b in zip(r.seats, null.seats, strict=True)
            ],
        })

    print(f"\n{'arm':<22} {'title':>7} {'null':>7} {'delta':>8} {'paper':>7} {'seats>':>8}")
    for r in rows:
        print(f"{r['arm']:<22} {100 * r['title']:>6.1f}% {100 * r['null_title']:>6.1f}% "
              f"{r['delta_pp']:>+7.1f}pp {100 * r['published_title_rate']:>6.1f}% "
              f"{r['seats_ahead']:>6}/12")

    if out_path:
        with open(out_path, "w") as fh:
            json.dump(rows, fh, indent=2)
        print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
