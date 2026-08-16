"""Point-in-time roster reconstruction from a final roster + transaction log."""

from __future__ import annotations

from fantasy_gm.data.store import Store
from fantasy_gm.data.yahoo_reconstruct import (
    Movement,
    Transaction,
    apply_to_store,
    reconstruct,
    rewind_to_draft,
    verify_against_draft,
)

DRAFT = "2025-10-21"
DRAFTED = {"1": ["p1", "p2", "p3"], "2": ["p4", "p5", "p6"]}

TXNS = [
    # team 1 streams: drops p3 for p7
    Transaction("2025-11-03", "add/drop", [
        Movement("p7", from_team=None, to_team="1"),
        Movement("p3", from_team="1", to_team=None),
    ]),
    # a trade: p1 <-> p4
    Transaction("2025-11-20", "trade", [
        Movement("p1", from_team="1", to_team="2"),
        Movement("p4", from_team="2", to_team="1"),
    ]),
    # team 2 picks up a free agent, no drop
    Transaction("2025-12-01", "add", [Movement("p8", from_team=None, to_team="2")]),
]

# what the rosters actually look like at the end, given DRAFTED + TXNS
FINAL = {"1": ["p2", "p4", "p7"], "2": ["p1", "p5", "p6", "p8"]}


def test_rewind_recovers_the_draft_day_rosters():
    initial, warnings = rewind_to_draft(FINAL, TXNS)
    assert not warnings
    assert {t: sorted(p) for t, p in initial.items()} == DRAFTED


def test_reconstruct_round_trips_through_the_store():
    """The real contract: after loading reconstructed events, the store's as-of layer must
    return the correct roster at every intermediate date."""
    recon = reconstruct(FINAL, TXNS, DRAFT, draft_results=DRAFTED)
    assert recon.ok, (recon.conflicts, recon.warnings)

    store = Store(":memory:")
    store.create_league("y-test", "Yahoo Test", "2025-26", "daily-change",
                        ["pts"], is_real=True)
    for tid in ("1", "2"):
        store.add_team("y-test", tid, f"Team {tid}")
    apply_to_store(store, "y-test", recon)

    # draft day
    assert store.roster_asof("y-test", "1", DRAFT) == ["p1", "p2", "p3"]
    # after the stream, before the trade
    assert store.roster_asof("y-test", "1", "2025-11-10") == ["p1", "p2", "p7"]
    # after the trade
    assert store.roster_asof("y-test", "1", "2025-11-25") == ["p2", "p4", "p7"]
    assert store.roster_asof("y-test", "2", "2025-11-25") == ["p1", "p5", "p6"]
    # end state matches what we started from
    assert store.roster_asof("y-test", "1", "2026-01-01") == sorted(FINAL["1"])
    assert store.roster_asof("y-test", "2", "2026-01-01") == sorted(FINAL["2"])


def test_rewind_warns_when_the_log_contradicts_the_roster():
    """A log missing an intermediate drop tells the rewind to undo an add for a player who
    is no longer there — that inconsistency is detectable and must be reported."""
    txns = [
        Transaction("2025-11-03", "add", [Movement("p7", to_team="1")]),
        # the 11-10 drop of p7 is missing from the log
        Transaction("2025-11-17", "add", [Movement("p7", to_team="1")]),
    ]
    _initial, warnings = rewind_to_draft({"1": ["p1", "p2", "p7"]}, txns)
    assert any("incomplete" in w for w in warnings)


def test_oldest_end_truncation_is_silent_to_rewind_but_caught_by_draft_check():
    """Pagination drops the *oldest* transactions. Every remaining undo stays internally
    consistent, so the rewind cannot notice — only the draft-results comparison can. This
    is why `verified` gates `ok`."""
    truncated = TXNS[1:]  # lost the earliest add/drop entirely
    initial, warnings = rewind_to_draft(FINAL, truncated)
    assert warnings == []                                  # silent, as feared
    assert sorted(initial["1"]) != sorted(DRAFTED["1"])    # and wrong

    recon = reconstruct(FINAL, truncated, DRAFT, draft_results=DRAFTED)
    assert not recon.verified
    assert not recon.ok
    assert any(w.startswith("1:") for w in recon.warnings)


def test_unverified_reconstruction_is_never_ok():
    recon = reconstruct(FINAL, TXNS, DRAFT)  # no draft_results supplied
    assert recon.warnings == [] and recon.conflicts == []
    assert not recon.verified and not recon.ok


def test_draft_verification_catches_a_mismatch():
    recon = reconstruct(FINAL, TXNS, DRAFT)
    assert verify_against_draft(recon.initial_rosters, DRAFTED) == []

    wrong = {"1": ["p1", "p2", "p9"], "2": DRAFTED["2"]}
    problems = verify_against_draft(recon.initial_rosters, wrong)
    assert len(problems) == 1 and problems[0].startswith("1:")


def test_same_day_round_trip_is_flagged_as_a_conflict():
    """The store keys events by (team, player, date), so an add+drop of the same player by
    the same team on one day cannot be represented — it must be reported."""
    txns = [Transaction("2025-11-03", "add", [Movement("p7", to_team="1")]),
            Transaction("2025-11-03", "drop", [Movement("p7", from_team="1")])]
    recon = reconstruct({"1": ["p1", "p2", "p3"]}, txns, DRAFT)
    assert recon.conflicts
    assert "same-day round trip" in recon.conflicts[0]
