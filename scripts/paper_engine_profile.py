"""What does the corrected H₀ actually *do* differently? (task 3.8 diagnostic)

A title rate says the corrected engine works; it does not say why, and "it works now" is
exactly the kind of claim that deserves a look at the output rather than trust. The published
result says H₀'s strategy includes punting a subset of categories which it is never told to
punt. If ours reproduces the number without reproducing the behaviour, the number is suspect.

Prints, for one seat of the published room: each arm's roster, its standardised total per
category, and the strategy weights the engine finished on.
"""

from __future__ import annotations

import sys

from fantasy_gm.data.store import Store
from fantasy_gm.draft.hscore import HScoreEngine
from fantasy_gm.draft.papersim import ARMS, basis_from_panel, build_panel
from fantasy_gm.draft.replay import hscore_strategy, snake_draft, static_order_strategy
from fantasy_gm.draft.settings import DraftSettings, Objective
from fantasy_gm.valuation import rosterable_pool

DB = "/Users/tim/projects/fantasy-nba-gm/data/fantasy_gm.sqlite"


def main(season: str = "2025-26", seat: int = 0, objective: str = "most_categories") -> None:
    store = Store(DB)
    settings = DraftSettings(n_teams=12, rounds=13, objective=Objective(objective))
    panel = build_panel(store, season, settings.categories)
    eligible = set(panel.eligible())
    pool = [p for p in rosterable_pool(store, season, pool_size=468) if p in eligible][:156]
    basis = basis_from_panel(panel, pool, settings.categories)
    board = sorted(basis.pool, key=lambda p: (-basis.total(p), p))
    names = {p: (store.conn.execute(
        "SELECT player_name FROM player_logs WHERE player_id = ? LIMIT 1", (p,)
    ).fetchone() or {"player_name": p})["player_name"] for p in board}

    rows = {}
    for arm in ("g_score", "h_score", "h_paper"):
        strategies = [static_order_strategy(board) for _ in range(settings.n_teams)]
        engine = None
        if arm != "g_score":
            engine = HScoreEngine(basis, settings, steps=8, **ARMS[arm])
            strategies[seat] = hscore_strategy(engine)
        rosters = snake_draft(strategies, board, settings)
        roster = rosters[seat]
        totals = {
            c: sum(basis.category_score(p, c) for p in roster) for c in settings.categories
        }
        rows[arm] = (roster, totals, engine)

    print(f"{season} seat {seat}, objective {objective}\n")
    for arm, (roster, totals, engine) in rows.items():
        print(f"--- {arm}")
        print("  roster: " + ", ".join(
            f"{names.get(p, p)}(#{board.index(p) + 1})" for p in roster
        ))
        print("  standardised totals: " + "  ".join(
            f"{c}={totals[c]:+.1f}" for c in settings.categories
        ))
        if engine is not None and engine._warm is not None:
            print("  final strategy weights: " + "  ".join(
                f"{c}={w:+.2f}" for c, w in zip(settings.categories, engine._warm, strict=True)
            ))
        print()

    base = rows["g_score"][1]
    print(f"{'cat':<8} {'g_score':>9} {'h_score':>9} {'h_paper':>9} "
          f"{'shipped Δ':>11} {'paper Δ':>9}")
    for c in settings.categories:
        print(f"{c:<8} {base[c]:>9.1f} {rows['h_score'][1][c]:>9.1f} "
              f"{rows['h_paper'][1][c]:>9.1f} "
              f"{rows['h_score'][1][c] - base[c]:>+11.1f} "
              f"{rows['h_paper'][1][c] - base[c]:>+9.1f}")


if __name__ == "__main__":
    main(*sys.argv[1:])
