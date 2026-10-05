"""Re-grade the ranking models on the real market field.

The published comparisons (results.md, steelman_z_replay.py) ran the boards against
bots on a value-ranking proxy — a lower bound, stated every time it was quoted. The
Yahoo draft_analysis ingest (task 2.4) makes the actual market ordering available for
2026-27, so this script re-runs the comparison with the bots drafting on the market
order itself.

There is no 2026-27 season to grade on yet, so this is a pre-season read under one
stated assumption: **production repeats** — rosters are graded on 2025-26 realized
weekly totals. Every board is built exactly as it will be built on draft night
(2025-26 production, availability projected as of the day before that season began),
so nothing from the season being graded reaches a board.

Field shape: bots pick near the market order with bounded noise (AdpBot), which is
what a room of humans following ADP produces. Players the market has not priced are
appended after the priced names — the market has no opinion on them, and the bots
treat them accordingly.

Rooms are two-armed by the steelman discipline: similar arms placed together spend
the draft removing each other's next pick, so each z arm gets its own room against
the same G board, plus a market-board control and a same-board null (the noise floor).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fantasy_gm.data.store import Store
from fantasy_gm.draft.board import AvailabilityMode, build_board
from fantasy_gm.draft.opponents import adp_order_from_market
from fantasy_gm.draft.replay import run_board_replay
from fantasy_gm.draft.settings import DraftSettings
from fantasy_gm.draft.zvariants import HINDSIGHT_ARMS, Z_ARMS, z_order

DB = "data/fantasy_gm.sqlite"
# Draft day for the graded season: the last date before its first game. Same convention
# as every forward-honest experiment in results.md.
AS_OF = {"2023-24": "2023-10-23", "2024-25": "2024-10-21", "2025-26": "2025-10-20"}
SEASON = "2025-26"
MARKET_SEASON = "2026-27"
ROTATIONS = 12
SEED = 7


def main(out_path: str | None = None) -> int:
    store = Store(DB)
    season, as_of = SEASON, AS_OF[SEASON]

    board = build_board(store, season, availability=AvailabilityMode.PROJECTED, as_of=as_of)
    g_order = [r.player_id for r in board.rows]
    pool = list(g_order)

    market_order = adp_order_from_market(store, MARKET_SEASON, source="yahoo",
                                         restrict_to=pool)
    if market_order is None:
        print(f"no market ADP stored for {MARKET_SEASON} — run "
              "`fantasy-gm adp --live ...` first", file=sys.stderr)
        return 1
    known = store.adp_asof(MARKET_SEASON, "9999-12-31", source="yahoo")
    pool_set = set(pool)
    n_priced = len([p for p in known if p in pool_set])
    top_overlap = len(pool_set & set(market_order[:len(pool)]))
    print(f"pool {len(pool)} | market-priced in pool: {n_priced} | "
          f"top-{len(pool)} market overlap with G pool: {top_overlap}")

    rooms: dict[str, dict[str, list[str]]] = {
        # The calibration arm: the G board against itself. Its measured "edge" is this
        # harness's noise floor, and no result below that floor means anything.
        "null_same_board": {"g_projected": list(g_order), "null_same_board": list(g_order)},
    }
    for arm, kwargs in Z_ARMS.items():
        if arm in HINDSIGHT_ARMS:
            continue  # hindsight arm excluded — may not be quoted as a baseline
        rooms[arm] = {"g_projected": list(g_order),
                      arm: z_order(store, season, as_of=as_of, **kwargs)}
    rooms["adp_market"] = {"g_projected": list(g_order), "adp_market": list(market_order)}

    results = {}
    for name, orders in rooms.items():
        res = run_board_replay(
            store, season, orders, pool,
            settings=DraftSettings(),
            rotations=ROTATIONS, seed=SEED,
            include_adp=False, mirror=True,
            adp_order=market_order,
        )
        results[name] = {k: {"cat": v.category_win_rate, "matchup": v.matchup_win_rate,
                             "n": v.category_games} for k, v in res.items()}
        g = results[name]["g_projected"]
        other = next(k for k in results[name] if k != "g_projected")
        o = results[name][other]
        print(f"{name:20s} G {100*g['cat']:5.1f}%/{100*g['matchup']:5.1f}%   "
              f"{other:18s} {100*o['cat']:5.1f}%/{100*o['matchup']:5.1f}%   "
              f"diff {100*(g['cat']-o['cat']):+6.2f}pp cat, "
              f"{100*(g['matchup']-o['matchup']):+6.2f}pp matchup")

    payload = {
        "season": season, "as_of": as_of, "seed": SEED, "rotations": ROTATIONS,
        "field": f"market_order_{MARKET_SEASON}_yahoo_adp",
        "assumption": "production repeats (graded on 2025-26 realized weekly totals)",
        "pool_size": len(pool),
        "market_priced_in_pool": n_priced,
        "top_overlap": top_overlap,
        "rooms": results,
    }
    out = out_path or "runs/market-field-regrade.json"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(payload, indent=2))
    print(f"\nsaved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:2]))
