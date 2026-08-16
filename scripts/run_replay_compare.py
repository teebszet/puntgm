"""Driver: extract decision slots per league (slow, cached) then compare strategies (fast)."""

from __future__ import annotations

import json
import os
import sys
import time

from fantasy_gm.data.store import Store
from fantasy_gm.validation.replay import (
    STRATEGIES,
    by_category,
    extract_slots,
    load_slots,
    run_strategies,
    save_slots,
    summarize,
)

DB = "data/fantasy_gm.sqlite"
SEASON = "2025-26"
CACHE = "data/slots"


def main(leagues: list[str]) -> None:
    os.makedirs(CACHE, exist_ok=True)
    store = Store(DB)
    all_slots = []
    for lg in leagues:
        path = f"{CACHE}/{lg}.json"
        if os.path.exists(path):
            slots = load_slots(path)
            print(f"{lg}: {len(slots)} slots (cached)", flush=True)
        else:
            t = time.time()
            slots = extract_slots(store, lg)
            save_slots(slots, path)
            print(f"{lg}: {len(slots)} slots ({time.time()-t:.0f}s)", flush=True)
        all_slots += slots

    print(f"\ntotal slots: {len(all_slots)}\n", flush=True)
    t = time.time()
    results = run_strategies(store, all_slots, SEASON)
    print(f"strategies run in {time.time()-t:.0f}s\n", flush=True)

    print(f"{'strategy':<12} {'n':>5} {'hit':>5} {'tie':>5} {'miss':>5} "
          f"{'hit%':>7} {'dec%':>7} {'±se':>7} {'avgΔ':>9} {'addDNP':>7}")
    for name in STRATEGIES:
        s = summarize(results[name])
        if not s["n"]:
            continue
        print(f"{name:<12} {s['n']:>5} {s['hit']:>5} {s['tie']:>5} {s['miss']:>5} "
              f"{s['hit_rate']:>7.3f} {s['hit_rate_decided']:>7.3f} {s['se']:>7.4f} "
              f"{s['avg_delta']:>9.2f} {s['add_dnp']:>7}")

    print("\n--- engine, by target category ---")
    print(f"{'cat':<8} {'n':>5} {'hit%':>7} {'dec%':>7} {'avgΔ':>9}")
    for c, s in by_category(results["engine"]).items():
        dec = s["hit_rate_decided"]
        print(f"{c:<8} {s['n']:>5} {s['hit_rate']:>7.3f} "
              f"{dec if dec is not None else float('nan'):>7.3f} {s['avg_delta']:>9.2f}")

    print("\n--- most_games, by target category ---")
    for c, s in by_category(results["most_games"]).items():
        dec = s["hit_rate_decided"]
        print(f"{c:<8} {s['n']:>5} {s['hit_rate']:>7.3f} "
              f"{dec if dec is not None else float('nan'):>7.3f} {s['avg_delta']:>9.2f}")

    with open("data/strategy_results.json", "w") as fh:
        json.dump({k: summarize(v) for k, v in results.items()}, fh, indent=2)


if __name__ == "__main__":
    main(sys.argv[1:] or ["sim-2025-26-1-8x10"])
