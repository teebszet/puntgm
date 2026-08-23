"""Does G-score still beat z-score once z-score gets the tweaks a real tool applies?

The published claim rests on beating :mod:`fantasy_gm.valuation`'s per-game z-score. That is
the form every free ranking list publishes, and it is blind to games played — which is where
most of the measured edge came from (A-DRAFT-14). Serious tools are not blind to it: they
expose a total-value mode that multiplies by games. Beating only the per-game form is a
strawman result, and this project's standing rule is to quote the honest baseline.

**Design: two-arm rooms, not one ladder-wide room.** Five z variants placed in one draft would
spend the whole draft removing each other's next pick, and every one of them would score worse
than it does alone. So each z arm is run in its own room against the *same* G arm, with ADP
bots filling every other seat. Composition is therefore identical across rooms and the only
thing that changes is which z board occupies that one seat.

The arm that decides the published number is ``z_steelman``: it receives the *same forward
availability projection* the G board receives, so whatever separates them is the weekly
variance term and nothing else. ``z_total_realized`` is a hindsight ceiling, reported to bound
how much of the gap availability could possibly explain, and is never quoted as a baseline.
"""

from __future__ import annotations

import json
import sys
import time

from fantasy_gm.data.store import Store
from fantasy_gm.draft.board import AvailabilityMode, build_board
from fantasy_gm.draft.replay import run_board_replay
from fantasy_gm.draft.settings import DraftSettings
from fantasy_gm.draft.zvariants import HINDSIGHT_ARMS, Z_ARMS, z_order

DB = "/Users/tim/projects/fantasy-nba-gm/data/fantasy_gm.sqlite"

# Draft day for each completed season: the last date before the season's first game. Every
# forward projection in this experiment is cut here, so nothing from the season being graded
# reaches a board that claims to be forward-honest.
AS_OF = {
    "2023-24": "2023-10-23",
    "2024-25": "2024-10-21",
    "2025-26": "2025-10-20",
}
SEASONS = ["2025-26", "2024-25"]
SEEDS = [7, 11, 23, 41]
# Every arm must visit every seat: with fewer rotations than teams, two adjacent arms sample
# different seat sets and the better slot is scored as a better board.
ROTATIONS = 12


def main(out_path: str | None = None) -> None:
    store = Store(DB)
    rows = []
    for season in SEASONS:
        as_of = AS_OF[season]
        board = build_board(
            store, season, availability=AvailabilityMode.PROJECTED, as_of=as_of
        )
        g_order = [r.player_id for r in board.rows]
        pool = list(g_order)  # every arm drafts from the same 156-player universe

        # The matched hindsight pair. Both sides see realized games, so the variance term is
        # isolated a second time under completely different information — if the edge only
        # appears in the forward-honest pair it is an artifact of the projection, not of
        # G-score, and this is what would show that.
        realized_board = build_board(
            store, season, availability=AvailabilityMode.REALIZED, as_of=as_of
        )
        g_realized = [r.player_id for r in realized_board.rows]

        z_orders = {
            arm: z_order(store, season, as_of=as_of, **kw) for arm, kw in Z_ARMS.items()
        }
        # The calibration arm: the G board against itself. Its measured "edge" is this
        # harness's noise floor, and no result below that floor means anything.
        z_orders["null_same_board"] = list(g_order)
        for arm, order in z_orders.items():
            for seed in SEEDS:
                t = time.time()
                # Pair each z arm with the G board holding the *same* availability
                # information: hindsight against hindsight, forward against forward. Pairing
                # a forward G board against a hindsight z arm would measure the projection,
                # not the metric.
                g_arm = g_realized if arm in HINDSIGHT_ARMS else g_order
                res = run_board_replay(
                    store,
                    season,
                    {"g_projected": g_arm, arm: order},
                    pool=pool,
                    settings=DraftSettings(),
                    rotations=ROTATIONS,
                    seed=seed,
                )
                row = {
                    "season": season,
                    "arm": arm,
                    "seed": seed,
                    "hindsight": arm in HINDSIGHT_ARMS,
                    "g_cat": round(res["g_projected"].category_win_rate, 4),
                    "z_cat": round(res[arm].category_win_rate, 4),
                    "adp_cat": round(res["adp"].category_win_rate, 4),
                    "g_matchup": round(res["g_projected"].matchup_win_rate, 4),
                    "z_matchup": round(res[arm].matchup_win_rate, 4),
                    "n": res["g_projected"].category_games,
                }
                row["edge_cat_pp"] = round(100 * (row["g_cat"] - row["z_cat"]), 2)
                row["edge_matchup_pp"] = round(100 * (row["g_matchup"] - row["z_matchup"]), 2)
                rows.append(row)
                print(
                    f"{season} {arm:<18} seed={seed}  G {row['g_cat']:.3f}  "
                    f"z {row['z_cat']:.3f}  edge {row['edge_cat_pp']:+.1f}pp  "
                    f"adp {row['adp_cat']:.3f}  ({time.time()-t:.0f}s)",
                    flush=True,
                )

    print("\n=== G-projected edge over each z arm, category win% (pp) ===")
    print(f"{'arm':<20}" + "".join(f"{s+' s'+str(sd):>14}" for s in SEASONS for sd in SEEDS))
    for arm in [*Z_ARMS, "null_same_board"]:
        cells = []
        for s in SEASONS:
            for sd in SEEDS:
                r = next(x for x in rows if x["season"] == s and x["arm"] == arm
                         and x["seed"] == sd)
                cells.append(f"{r['edge_cat_pp']:>+13.1f}")
        flag = "  (hindsight)" if arm in HINDSIGHT_ARMS else ""
        print(f"{arm:<20}" + "".join(cells) + flag)

    if out_path:
        with open(out_path, "w") as fh:
            json.dump(rows, fh, indent=2)
        print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
