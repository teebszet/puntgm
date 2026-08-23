"""Task 3.8, the payoff — does the paper-faithful engine beat the static board on REAL data?

`scripts/paper_validation.py` answers whether our H₀ reproduces the published simulation. If it
does only after the implementation is corrected, the next question is immediate: does the
corrected engine also overturn the result that has been sitting in `results.md` since 2026-08-17,
that H₀ loses to a static G-score board on completed seasons?

Three **two-arm rooms** per season, because two similar arms in one room spend the draft
removing each other's next pick:

* ``null`` — the same G-score board in both seats. The noise floor, measured rather than
  assumed. A seat-adjacency artifact of up to +9.5pp is why this row is mandatory.
* ``shipped`` — G-score vs H₀ as it has been all along; reproduces the standing result.
* ``paper`` — G-score vs the paper-faithful engine.

Every room is seat-mirrored over all twelve rotations, so no arm can profit from adjacency or
from a seat set its opponent never visited.

Usage::

    python scripts/paper_engine_replay.py <season> [out.json]
"""

from __future__ import annotations

import json
import sys
import time

from fantasy_gm.data.store import Store
from fantasy_gm.draft.hscore import HScoreEngine
from fantasy_gm.draft.papersim import ARMS
from fantasy_gm.draft.replay import (
    hscore_strategy,
    run_strategy_replay,
    static_order_strategy,
)
from fantasy_gm.draft.settings import DraftSettings
from fantasy_gm.draft.xscore import xscore_basis

DB = "/Users/tim/projects/fantasy-nba-gm/data/fantasy_gm.sqlite"
ROTATIONS = 12
ENGINE_STEPS = 8
SEEDS = [7, 11]


def main(season: str, out_path: str | None = None) -> None:
    store = Store(DB)
    # The replay basis keeps `include_idle_weeks=True` — grading a finished season, a week the
    # player missed is a week the manager lost, and every number in `results.md` is on this
    # basis. Nothing about task 3.8 touches that choice.
    basis = xscore_basis(store, season)
    settings = DraftSettings()
    board = sorted(basis.pool, key=lambda p: (-basis.total(p), p))

    def g_board():
        return static_order_strategy(board)

    def engine(**kwargs):
        return hscore_strategy(
            HScoreEngine(basis, settings, steps=ENGINE_STEPS, **kwargs)
        )

    rooms = {
        "null": lambda: {"g_score": g_board(), "g_score_b": g_board()},
        "shipped": lambda: {"g_score": g_board(), "h_score": engine(**ARMS["h_score"])},
        "paper": lambda: {"g_score": g_board(), "h_paper": engine(**ARMS["h_paper"])},
    }

    rows = []
    for room, build in rooms.items():
        for seed in SEEDS:
            t = time.time()
            res = run_strategy_replay(
                store, season, build(), board, settings,
                rotations=ROTATIONS, seed=seed, mirror=True,
            )
            names = list(res)
            challenger = names[1]
            a, b = res[names[0]], res[challenger]
            row = {
                "season": season,
                "room": room,
                "seed": seed,
                "rotations": ROTATIONS,
                "baseline": names[0],
                "challenger": challenger,
                "baseline_cat": a.category_win_rate,
                "challenger_cat": b.category_win_rate,
                "cat_delta_pp": 100 * (b.category_win_rate - a.category_win_rate),
                "baseline_matchup": a.matchup_win_rate,
                "challenger_matchup": b.matchup_win_rate,
                "matchup_delta_pp": 100 * (b.matchup_win_rate - a.matchup_win_rate),
                "decisions": b.category_games,
            }
            rows.append(row)
            print(
                f"{season} {room:<8} seed {seed:<3} "
                f"{challenger} - {names[0]}: "
                f"{row['cat_delta_pp']:+6.2f}pp cat  "
                f"{row['matchup_delta_pp']:+6.2f}pp matchup  "
                f"(n={row['decisions']}, {time.time() - t:.0f}s)",
                flush=True,
            )

    print(f"\n{'room':<10} {'cat delta':>22} {'matchup delta':>22}")
    for room in rooms:
        vals = [r for r in rows if r["room"] == room]
        cat = "  ".join(f"{v['cat_delta_pp']:+6.2f}" for v in vals)
        mu = "  ".join(f"{v['matchup_delta_pp']:+6.2f}" for v in vals)
        print(f"{room:<10} {cat:>22} {mu:>22}")

    if out_path:
        with open(out_path, "w") as fh:
            json.dump(rows, fh, indent=2)
        print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
