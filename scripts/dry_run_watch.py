"""Dry-run the full live path against a real Yahoo mock draft (task 4.8).

Watch mode: poll the league's draft_results every N seconds, reconcile every new pick
into the tracker (reporting discrepancies), and print the on-the-clock recommendation
for the seat being watched. Run it alongside the Yahoo mock draft client:

    python scripts/dry_run_watch.py 478.l.25733 --seat 5

This is the mandatory rehearsal before the real draft: it exercises token refresh,
live polling, reconciliation and recommendations against Yahoo's actual draft flow.
Manual entry still works at any moment (fantasy-gm draft) if the poll degrades.
"""

from __future__ import annotations

import sys
import time

from fantasy_gm.config import Config
from fantasy_gm.data.store import Store
from fantasy_gm.draft.live import (
    DraftState,
    build_gm,
    parse_columns,
    poll_draft_results,
    recommend,
    reconcile,
    render_recommendation,
    save_state,
)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    league = sys.argv[1]
    seat = int(sys.argv[sys.argv.index("--seat") + 1]) if "--seat" in sys.argv else 1
    interval = (float(sys.argv[sys.argv.index("--interval") + 1])
                if "--interval" in sys.argv else 15.0)
    columns_spec = (sys.argv[sys.argv.index("--columns") + 1]
                    if "--columns" in sys.argv else None)
    try:
        columns = parse_columns(columns_spec)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    store = Store(Config().db_path)
    state_path = f"data/draft_{league.replace('.', '_')}.json"
    state = DraftState(league_key=league, n_teams=12, my_seat=seat)

    print("Building board + market order (once) ...")
    gm = build_gm(store, "2025-26", "2025-10-20")
    print(f"  board ready: {len(gm['pool'])} players; watching seat {seat} of league {league}")

    seen = 0
    while True:
        try:
            picks = poll_draft_results(league)
        except RuntimeError as exc:
            print(f"[poll failed: {exc}] — manual entry remains available", flush=True)
            time.sleep(interval)
            continue
        issues = reconcile(state, picks, gm["names"])
        for i in issues:
            print(f"  ! {i}", flush=True)
        if len(state.picks) > seen:
            for p in state.picks[seen:]:
                who = "YOU" if p.team_seat == seat else f"seat {p.team_seat}"
                print(f"#{p.number:>3} {who:<4} {p.name or p.player_id}", flush=True)
            seen = len(state.picks)
            save_state(state, state_path)
        if state.is_my_pick():
            rec = recommend(store, "2025-26", state, gm["pool"], board=gm["board"],
                            adp_order=gm["adp_order"], names=gm["names"], budget_s=8.0)
            print(render_recommendation(rec, columns), flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
