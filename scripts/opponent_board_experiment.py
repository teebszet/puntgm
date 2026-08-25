"""Is H₀'s deficit a wiring gap? Tell it who else is in the room and re-measure.

`scripts/room_decomposition.py` established that H₀'s sign flip is owned entirely by **the
field**: against eleven G-score drafters it beats its own null at 10-12 of 12 draft seats;
against ADP bots it beats it at 0-1 of 12. Availability barely moves it either way.

That is a suspiciously specific failure, and the code says why. `HScoreEngine` prices the
opposing side's unknown picks with ``_weighted_future(avail, neutral)`` — a softmax over
*our own* board. The engine's model of the room is "everyone else drafts best-available on the
G-score board". In the published setup that is not an approximation, it is literally true, and
H₀ wins. In any other room it is wrong.

Corroborating, from the opposite direction: ``adp_ranks`` and ``survival_probability`` — task
3.5's scarcity model — have **no callers** in `fantasy_gm/` or `scripts/`. They are defined,
covered by tests in isolation, and unreachable. ``PickScore.survival`` is documented as
"if an ADP model was given"; nothing has ever given one.

So this script asks one question, on one axis:

    In an ADP field, does handing the engine the field's own board close the deficit?

* **Closes** — the deficit was ours, not the method's, and H₀ is a product candidate again
  (still pending a measurement under projection error, which nothing has done).
* **Survives** — H₀ needs a field it models even when told what the field is, and real leagues
  do not draft a G-score board. That is the much harder answer, and it is worth knowing.

Both idle-week settings are run, because the decomposition showed availability is not the
driver and a fix that only works with injuries modelled away would not be a fix.

The null is a G-score board at the same seat and does not depend on ``model_the_field``, so it
is run once per availability setting and shared by both arms — the difference of interest is
between the two H₀ columns, and they must be read against the same null.

Usage::

    python scripts/opponent_board_experiment.py <season> <most_categories|each_category> [out.json]
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

ARMS_UNDER_TEST = ["h_score", "h_paper"]
IDLE_SETTINGS = [False, True]


def main(season: str, objective: str, out_path: str | None = None) -> None:
    obj = Objective(objective)
    store = Store(DB)
    rows = []

    for idle in IDLE_SETTINGS:
        common = dict(
            season=season, objective=obj, n_seasons=N_SEASONS,
            engine_steps=ENGINE_STEPS, field="adp", include_idle_weeks=idle,
        )
        t = time.time()
        null = run_paper_sim(store, arm="g_score", **common)
        print(
            f"{season} {obj.value:<16} idle={str(idle):<5} null      "
            f"title {100 * null.mean_title_rate:5.1f}%  "
            f"cat {100 * null.mean_cat_win_rate:5.1f}%  ({time.time() - t:.0f}s)",
            flush=True,
        )

        for arm in ARMS_UNDER_TEST:
            for modelled in (False, True):
                t = time.time()
                r = run_paper_sim(
                    store, arm=arm, model_the_field=modelled, **common
                )
                label = "told" if modelled else "blind"
                print(
                    f"{season} {obj.value:<16} idle={str(idle):<5} {arm:<9} {label:<5} "
                    f"title {100 * r.mean_title_rate:5.1f}%  "
                    f"cat {100 * r.mean_cat_win_rate:5.1f}%  ({time.time() - t:.0f}s)",
                    flush=True,
                )
                rows.append({
                    "season": season,
                    "objective": obj.value,
                    "field": "adp",
                    "include_idle_weeks": idle,
                    "arm": arm,
                    "model_the_field": modelled,
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
        f"\n{'idle':<5} {'arm':<9} {'opp model':<10} "
        f"{'title Δ':>9} {'cat Δ':>8} {'matchup Δ':>10} {'seats>':>8}"
    )
    for r in rows:
        print(
            f"{str(r['include_idle_weeks']):<5} {r['arm']:<9} "
            f"{('field' if r['model_the_field'] else 'our board'):<10} "
            f"{r['title_delta_pp']:>+8.2f}pp {r['cat_delta_pp']:>+7.2f}pp "
            f"{r['matchup_delta_pp']:>+9.2f}pp {r['seats_ahead']:>6}/12"
        )

    if out_path:
        with open(out_path, "w") as fh:
            json.dump(rows, fh, indent=2)
        print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
