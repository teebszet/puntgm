"""Stitch the per-season/objective task-3.8 shards into the tables that go in `results.md`."""

from __future__ import annotations

import glob
import json
import sys
from collections import defaultdict
from statistics import fmean

SEASON_ORDER = ["2025-26", "2024-25", "2023-24"]
ARM_ORDER = [
    "h_score", "h_full_pool", "h_normalised", "h_fullpool_normalised",
    "h_future_slices", "h_paper",
]


def main(pattern: str) -> None:
    rows = []
    for path in sorted(glob.glob(pattern)):
        with open(path) as fh:
            rows += json.load(fh)
    if not rows:
        print(f"no shards matched {pattern}")
        return

    by = defaultdict(dict)
    for r in rows:
        by[(r["objective"], r["arm"])][r["season"]] = r
    seasons = [s for s in SEASON_ORDER if any(s in v for v in by.values())]
    published = {r["objective"]: r["published_title_rate"] for r in rows}
    null = defaultdict(dict)
    for r in rows:
        null[r["objective"]][r["season"]] = r["null_title"]

    for obj in sorted({r["objective"] for r in rows}):
        print(f"\n### {obj} — share of simulated seasons finished first "
              f"(published: {100 * published[obj]:.1f}%, chance: 8.3%)\n")
        head = "| arm | " + " | ".join(seasons) + " | mean |"
        print(head)
        print("|" + "---|" * (len(seasons) + 2))
        cells = " | ".join(
            f"{100 * null[obj][s]:.1f}%" for s in seasons if s in null[obj]
        )
        print(f"| **null** (12th G-score drafter) | {cells} | "
              f"{100 * fmean(null[obj].values()):.1f}% |")
        for arm in ARM_ORDER:
            got = by.get((obj, arm))
            if not got:
                continue
            cells = " | ".join(
                f"{100 * got[s]['title']:.1f}%" for s in seasons if s in got
            )
            mean = fmean(got[s]["title"] for s in seasons if s in got)
            print(f"| `{arm}` | {cells} | {100 * mean:.1f}% |")

    print("\n### seats where the arm beat the null, out of 12\n")
    print("| arm | " + " | ".join(f"{o} {s}" for o in sorted({r['objective'] for r in rows})
                                  for s in seasons) + " |")
    print("|" + "---|" * (1 + len(seasons) * len({r["objective"] for r in rows})))
    for arm in ARM_ORDER:
        cells = []
        for obj in sorted({r["objective"] for r in rows}):
            for s in seasons:
                got = by.get((obj, arm), {}).get(s)
                cells.append(str(got["seats_ahead"]) if got else "—")
        if any(c != "—" for c in cells):
            print(f"| `{arm}` | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1
         else "openspec/changes/h-score-draft-engine/runs/paper-validation/*.json")
