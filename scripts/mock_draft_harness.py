"""Run the on-the-clock tool as a drafter across several mock drafts and rank it.

This is the mock harness Tim asked for: our tool competes as a *drafter* against
teams running other models, on the real market field, graded on realized production
(2025-26). Each mock is a full snake draft; each arm drafts with its own board; the
room is filled with market-ADP bots. Arms:

* ``ours``       — the live tool's on-the-clock logic (board + survival + reach rule)
* ``g_score``    — static G board, no clock awareness
* ``z_steelman`` — the strongest z variant (forward availability + replacement)
* ``z_pergame``  — the classic published form
* ``adp_market`` — straight down the real Yahoo ADP board
* ``null``       — our board vs itself, for the noise floor

Grading is all-play-all on realized weekly category results. All arms share one room
(which is what a real mock draft is) and rotate across seats, so seat is controlled;
the caveat is that similar boards cannibalize each other's picks in a shared room —
the null and g_score arms are the same board, so both are slightly penalized, and the
gaps between them understate their agreement rather than overstating it.
"""

from __future__ import annotations

import random
from pathlib import Path

from fantasy_gm.draft.board import AvailabilityMode, build_board
from fantasy_gm.draft.live import _seat_of, recommend
from fantasy_gm.draft.opponents import adp_order_from_market
from fantasy_gm.draft.replay import callable_factory, score_rosters, snake_draft
from fantasy_gm.draft.settings import DraftSettings
from fantasy_gm.draft.zvariants import z_order

DB = "data/fantasy_gm.sqlite"
AS_OF = {"2023-24": "2023-10-23", "2024-25": "2024-10-21", "2025-26": "2025-10-20"}
SEASON = "2025-26"
MARKET_SEASON = "2026-27"
MOCKS = 5  # mock drafts per arm (each rotation places the arm at another seat)
SEED = 11


def tool_pick_factory(store, season, gm, adp_order):
    """The live tool's pick function: recommend() under a mock-draft clock."""
    from fantasy_gm.draft.live import DraftState as LiveState

    def make(rotation):
        def pick(state, available):
            # The tool is invoked exactly when its seat is on the clock, so the seat
            # is derivable from how many picks have been made.
            total = len(state.my_roster) + sum(len(r) for r in state.opponent_rosters)
            seat = _seat_of(total + 1, 12)
            live_state = LiveState.from_parts(
                state.my_roster, state.opponent_rosters, set(state.taken),
                n_teams=12, my_seat=seat,
            )
            rec = recommend(store, season, live_state, available,
                            board=gm["board"], adp_order=adp_order,
                            names=gm["names"], budget_s=5.0, top_n=8)
            if not rec.candidates:
                return None
            return rec.candidates[0].player_id
        return pick
    return make


def main() -> int:
    from fantasy_gm.data.store import Store

    store = Store(DB)
    season, as_of = SEASON, AS_OF[SEASON]
    settings = DraftSettings()

    board = build_board(store, season, availability=AvailabilityMode.PROJECTED, as_of=as_of)
    g_order = [r.player_id for r in board.rows]
    pool = list(g_order)
    market = adp_order_from_market(store, MARKET_SEASON, source="yahoo", restrict_to=pool) or []
    names = {r.player_id: r.player_name for r in board.rows}
    gm = {"board": board, "names": names, "adp_order": market}

    z_steel = z_order(store, season, as_of=as_of, availability="projected",
                      replacement_iters=5)
    z_pg = z_order(store, season, as_of=as_of)

    arms = {
        "ours": tool_pick_factory(store, season, gm, market),
        "g_score": static(g_order),
        "z_steelman": static(z_steel),
        "z_pergame": static(z_pg),
        "adp_market": static(market),
        "null": static(g_order),
    }

    # Place arms one per room (plus market-ADP bots), rotating across seats; run each
    # mock with a fresh seed so bot noise varies across mocks.
    results = {name: {"cat_wins": 0.0, "cat_games": 0, "matchup_wins": 0.0, "matchups": 0}
               for name in arms}
    from fantasy_gm.draft.opponents import AdpBot
    from fantasy_gm.draft.replay import bot_strategy

    for mock in range(MOCKS):
        seed = SEED + mock * 1000
        placement = list(arms)
        n_teams = settings.n_teams
        for rot in range(settings.n_teams):
            seats = [None] * n_teams
            seat_of = {}
            for i, name in enumerate(placement):
                seat = (i + rot) % n_teams
                factory = arms[name]
                seats[seat] = factory(rot) if callable_factory(factory) else factory
                seat_of[name] = seat
            for s in range(n_teams):
                if seats[s] is None:
                    seats[s] = bot_strategy(AdpBot(market, random.Random(seed + rot * 100 + s)))
            rosters = snake_draft(seats, pool, settings)
            graded = score_rosters(store, season, rosters, settings)
            for name in arms:
                g = graded[seat_of[name]]
                r = results[name]
                r["cat_wins"] += g["cat_wins"]
                r["cat_games"] += g["cat_games"]
                r["matchup_wins"] += g["matchup_wins"]
                r["matchups"] += g["matchups"]

    print(f"{'arm':<12} {'cat win%':>9} {'matchup%':>9} {'n':>8}")
    ranked = sorted(results.items(), key=lambda kv: -kv[1]["cat_wins"] / max(1, kv[1]["cat_games"]))
    for name, r in ranked:
        cat = 100 * r["cat_wins"] / r["cat_games"] if r["cat_games"] else 0
        m = 100 * r["matchup_wins"] / r["matchups"] if r["matchups"] else 0
        print(f"{name:<12} {cat:>8.1f}% {m:>8.1f}% {r['cat_games']:>8}")

    import json
    out = "runs/mock-draft-harness.json"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({
        "season": season, "as_of": as_of, "mocks_per_rotation": MOCKS,
        "rotations": settings.n_teams, "field": "market_adp_bots",
        "assumption": "production repeats",
        "results": results,
    }, indent=2))
    print(f"saved -> {out}")
    return 0


def static(order):
    from fantasy_gm.draft.replay import static_order_strategy
    return static_order_strategy(order)


if __name__ == "__main__":
    raise SystemExit(main())
