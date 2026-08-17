"""Task 3.12 — does the single-representative-opponent simplification explain H₀'s deficit?

Runs the three opponent models as separate seats in the *same* draft room, against the same
bots, graded on the same weeks. Running them in separate replays would confound the comparison
with whichever pool each happened to face.

    FANTASY_GM_DATA_DIR=/Users/tim/projects/fantasy-nba-gm/data \
        python scripts/opponent_model_replay.py [--rotations 12] [--steps 5] [--pool 180]
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

SEASON = "2025-26"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rotations", type=int, default=12)
    ap.add_argument("--steps", type=int, default=5)
    ap.add_argument("--pool", type=int, default=180)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    cfg = Config()
    store = Store(cfg.db_path)
    settings = DraftSettings()
    basis = xscore_basis(store, SEASON, pool_size=args.pool)

    arms = (OpponentModel.REPRESENTATIVE, OpponentModel.STRONGEST, OpponentModel.FIELD)
    t0 = time.time()
    results = run_draft_replay(
        store,
        SEASON,
        basis,
        settings,
        rotations=args.rotations,
        seed=args.seed,
        engine_steps=args.steps,
        pool_size=args.pool,
        opponent_arms=arms,
    )
    elapsed = time.time() - t0

    print(f"\nrotations={args.rotations} steps={args.steps} pool={args.pool} "
          f"seed={args.seed}  ({elapsed:.0f}s)\n")
    print(format_replay(results))

    print("\nper-category win rate")
    cats = settings.categories
    print(f"{'strategy':<22}" + "".join(f"{c:>8}" for c in cats))
    for name, r in sorted(results.items(), key=lambda kv: -kv[1].category_win_rate):
        rates = r.category_rates()
        print(f"{name:<22}" + "".join(f"{100 * rates.get(c, 0.0):>7.1f}%" for c in cats))


if __name__ == "__main__":
    main()
