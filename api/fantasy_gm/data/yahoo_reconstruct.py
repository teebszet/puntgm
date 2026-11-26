"""Point-in-time roster reconstruction from a final roster + a transaction log.

Yahoo exposes ``team/roster;date=YYYY-MM-DD`` (roster as of a date) and
``league/transactions`` (adds, drops, trades). Snapshotting a roster per team per decision
date would cost ``teams × decision_dates`` API calls; reconstructing instead costs
``teams + 1`` — fetch each final roster once plus the transaction log, then walk the log
**backwards** to recover the draft-day roster and **forwards** to emit the add/drop events
the store's as-of layer already knows how to read.

The reason this is safe to rely on is that it is *checkable*: the backward walk ends at a
reconstructed draft-day roster, and Yahoo separately exposes ``draft_results``. If the two
disagree, the transaction log was incomplete (Yahoo paginates, and the default page is
small) — so the reconstruction reports the mismatch instead of silently producing a roster
history that drifts further from the truth every week. Never fabricate; record provenance.

**That check is mandatory, not advisory.** The rewind's own consistency warnings catch only
*some* corruption: they fire when the log tells it to undo an add for a player who isn't
there. A log simply truncated at the oldest end — exactly what pagination produces — leaves
every remaining undo internally consistent and yields a wrong draft roster with no warning
at all. Only comparing against ``draft_results`` detects that, which is why
``reconstruct`` takes them and refuses to mark itself ``verified`` without them.

This module is deliberately network-free and provider-agnostic: it takes already-normalised
dicts, so it is fully testable offline and the credentialed fetch stays a thin shell around
it (``yahoo_fetch.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Movement:
    """One player moving between a team and (another team | waivers/FA).

    ``from_team``/``to_team`` are ``None`` when the other side is the free-agent pool.
    """

    player_id: str
    from_team: str | None = None
    to_team: str | None = None


@dataclass
class Transaction:
    date: str                 # ISO date the transaction took effect
    type: str                 # "add" | "drop" | "add/drop" | "trade"
    movements: list[Movement] = field(default_factory=list)


@dataclass
class RosterEvent:
    team_id: str
    player_id: str
    action: str               # "add" | "drop"
    known_from: str


@dataclass
class Reconstruction:
    initial_rosters: dict[str, list[str]]      # reconstructed as of draft date
    events: list[RosterEvent]
    conflicts: list[str]                       # same (team, player, date) add+drop collisions
    warnings: list[str]
    verified: bool = False                     # checked against Yahoo's own draft_results

    @property
    def ok(self) -> bool:
        """Trustworthy only when the draft-day rosters were independently verified — an
        unverified reconstruction can be silently wrong (see module docstring)."""
        return self.verified and not self.conflicts and not self.warnings


def rewind_to_draft(
    final_rosters: dict[str, list[str]], transactions: list[Transaction]
) -> tuple[dict[str, set[str]], list[str]]:
    """Undo every transaction, newest first, to recover the draft-day rosters.

    Returns the rewound rosters and any warnings where the log disagreed with the roster
    state it claimed to act on (a sign of an incomplete or out-of-order log).
    """
    rosters: dict[str, set[str]] = {t: set(p) for t, p in final_rosters.items()}
    warnings: list[str] = []

    for tx in sorted(transactions, key=lambda t: t.date, reverse=True):
        for mv in tx.movements:
            # undo "player arrived at to_team"
            if mv.to_team is not None:
                if mv.to_team not in rosters:
                    warnings.append(f"{tx.date}: unknown team {mv.to_team!r} in transaction")
                elif mv.player_id not in rosters[mv.to_team]:
                    warnings.append(
                        f"{tx.date}: {mv.player_id} not on {mv.to_team} to undo an add "
                        "(log likely incomplete or paginated)"
                    )
                else:
                    rosters[mv.to_team].discard(mv.player_id)
            # undo "player left from_team"
            if mv.from_team is not None:
                if mv.from_team not in rosters:
                    warnings.append(f"{tx.date}: unknown team {mv.from_team!r} in transaction")
                else:
                    rosters[mv.from_team].add(mv.player_id)
    return rosters, warnings


def reconstruct(
    final_rosters: dict[str, list[str]],
    transactions: list[Transaction],
    draft_date: str,
    draft_results: dict[str, list[str]] | None = None,
) -> Reconstruction:
    """Full point-in-time history: rewind to the draft, then replay forward as events.

    Pass ``draft_results`` (from Yahoo's ``draft_results`` endpoint) to verify the rewind.
    Without it the result is left ``verified=False`` and must not be treated as exact —
    a paginated transaction log produces a wrong history with no internal symptom.
    """
    initial, warnings = rewind_to_draft(final_rosters, transactions)

    events: list[RosterEvent] = [
        RosterEvent(team, pid, "add", draft_date)
        for team in sorted(initial)
        for pid in sorted(initial[team])
    ]

    # Replay forward. The store keys roster events by (league, team, player, known_from),
    # so an add and a drop of the same player by the same team on the same day would
    # overwrite each other — surface it rather than silently lose one.
    seen: dict[tuple[str, str, str], str] = {}
    conflicts: list[str] = []
    for tx in sorted(transactions, key=lambda t: t.date):
        for mv in tx.movements:
            for team, action in ((mv.from_team, "drop"), (mv.to_team, "add")):
                if team is None:
                    continue
                key = (team, mv.player_id, tx.date)
                if key in seen and seen[key] != action:
                    conflicts.append(
                        f"{tx.date}: {mv.player_id} both added and dropped by {team} "
                        "on the same date — same-day round trip is not representable"
                    )
                seen[key] = action
                events.append(RosterEvent(team, mv.player_id, action, tx.date))

    initial_sorted = {t: sorted(p) for t, p in initial.items()}
    verified = False
    if draft_results is not None:
        problems = verify_against_draft(initial_sorted, draft_results)
        warnings = warnings + problems
        verified = not problems

    return Reconstruction(initial_sorted, events, conflicts, warnings, verified)


def verify_against_draft(
    initial_rosters: dict[str, list[str]], draft_results: dict[str, list[str]]
) -> list[str]:
    """Compare the rewound draft-day rosters against Yahoo's own draft results.

    This is the integrity check that makes the whole reconstruction trustworthy: a clean
    match means the transaction log was complete, so every intermediate roster is exact.
    Any discrepancy is reported per team rather than averaged away.
    """
    problems: list[str] = []
    for team in sorted(set(initial_rosters) | set(draft_results)):
        got = set(initial_rosters.get(team, []))
        want = set(draft_results.get(team, []))
        if got == want:
            continue
        missing, extra = sorted(want - got), sorted(got - want)
        problems.append(
            f"{team}: reconstructed draft roster differs from draft_results "
            f"(missing {missing or '-'}, unexpected {extra or '-'})"
        )
    return problems


def apply_to_store(store, league_id: str, recon: Reconstruction) -> int:
    """Write reconstructed events into the store. Returns the event count written."""
    for ev in recon.events:
        store.add_roster_event(league_id, ev.team_id, ev.player_id, ev.action, ev.known_from)
    return len(recon.events)
