"""Read-only import of the user's own past Yahoo leagues — the *secondary*, real-world
validation source (D7), and the same code path the BYOT product runs on.

Two entry points:

* ``import_league_export`` — the original hand-rolled export shape, kept for fixtures.
* ``import_snapshot`` — the real path: takes a snapshot from ``yahoo_fetch``, joins Yahoo
  player ids to NBA ids (``player_crosswalk``), reconstructs point-in-time roster history
  from the transaction log (``yahoo_reconstruct``), and loads it as ``is_real=True``.

The earlier note here said roster-as-of-date history was only partially recoverable. That
was too pessimistic: Yahoo exposes ``team/roster;date=`` directly, and the transaction log
plus a final roster reconstructs the whole season exactly — *provided* the reconstruction
is verified against ``draft_results``, which is why the import refuses to load an
unverified one unless explicitly forced.

Read-only throughout: no writes are ever issued against the user's league.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fantasy_gm.config import DEFAULT_CATEGORIES
from fantasy_gm.models import Matchup


def import_league_export(store, export: dict[str, Any]) -> str:
    """Load an offline, read-only Yahoo league export into the store as a real league.

    ``export`` shape (all point-in-time where available)::

        {
          "league_id": "yahoo-431.l.12345",
          "name": "My Dynasty",
          "season": "2024-25",
          "cadence": "daily-change",
          "categories": [...],                # optional; defaults to 9-cat
          "teams": [{"team_id": "1", "name": "..."}, ...],
          "roster_events": [                  # if omitted -> provenance recorded
             {"team_id": "1", "player_id": "nba-123", "action": "add", "known_from": "2024-10-22"}
          ],
          "matchups": [
             {"period": 1, "start": "2024-10-21", "end": "2024-10-27",
              "team_a": "1", "team_b": "2"}
          ]
        }

    Performs no network I/O and never mutates the user's Yahoo league.
    """
    league_id = export["league_id"]
    categories = export.get("categories") or list(DEFAULT_CATEGORIES)
    store.create_league(
        league_id,
        export.get("name", league_id),
        export["season"],
        export.get("cadence", "daily-change"),
        categories,
        is_real=True,
        seed=None,
    )
    for t in export.get("teams", []):
        store.add_team(league_id, t["team_id"], t.get("name", t["team_id"]))

    roster_events = export.get("roster_events")
    if roster_events:
        for e in roster_events:
            store.add_roster_event(
                league_id, e["team_id"], e["player_id"], e["action"], e["known_from"]
            )
    else:
        store.record_provenance(
            f"league:{league_id}",
            "no point-in-time roster history in export; roster-as-of-date unavailable "
            "for this real league (Yahoo limitation) — not fabricated",
            datetime.now(UTC).date().isoformat(),
        )

    for m in export.get("matchups", []):
        store.add_matchup(
            Matchup(league_id, m["period"], m["start"], m["end"], m["team_a"], m["team_b"])
        )
    return league_id


class ImportRefused(Exception):
    """Raised when a snapshot cannot be loaded without fabricating history."""


def import_snapshot(
    store, snapshot: dict[str, Any], overrides: dict[str, str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Load a ``yahoo_fetch`` snapshot as a real league. Returns an import report.

    Refuses (rather than degrades) on two conditions, because both would silently corrupt
    the replay rather than merely reduce coverage:

    * **unresolved players** — an unmatched or ambiguous Yahoo id has no NBA box scores, so
      any roster containing them projects as if the slot were empty;
    * **unverified roster history** — a paginated transaction log reconstructs a wrong
      season with no internal symptom (see ``yahoo_reconstruct``).

    ``force=True`` loads anyway and records provenance describing exactly what is wrong, for
    the case where a degraded league is still worth eyeballing.
    """
    from fantasy_gm.data.player_crosswalk import build_crosswalk
    from fantasy_gm.data.yahoo_reconstruct import (
        Movement,
        Transaction,
        apply_to_store,
        reconstruct,
    )

    league_id = snapshot["league_id"]
    season = snapshot["season"]

    cw = build_crosswalk(store, season, snapshot.get("player_names", {}), overrides)
    if not cw.ok and not force:
        raise ImportRefused(
            f"player id join incomplete ({cw.report()}). Resolve with overrides= or pass "
            "force=True to load a league with missing players."
        )

    def nba(pid: str) -> str | None:
        return cw.mapping.get(pid)

    transactions = [
        Transaction(
            t["date"], t["type"],
            [Movement(nba(m["player_id"]), m["from_team"], m["to_team"])
             for m in t["movements"] if nba(m["player_id"])],
        )
        for t in snapshot.get("transactions", [])
    ]
    transactions = [t for t in transactions if t.movements]

    final_rosters = {tid: [nba(p) for p in roster if nba(p)]
                     for tid, roster in snapshot["final_rosters"].items()}
    draft_results = {tid: [nba(p) for p in picks if nba(p)]
                     for tid, picks in snapshot.get("draft_results", {}).items()}

    matchups = snapshot.get("matchups", [])
    draft_date = min((m["start"] for m in matchups), default=snapshot.get("draft_date", ""))

    recon = reconstruct(final_rosters, transactions, draft_date,
                        draft_results=draft_results or None)
    if not recon.ok and not force:
        raise ImportRefused(
            "roster history could not be verified against draft results: "
            + "; ".join(recon.warnings + recon.conflicts)
        )

    store.clear_league(league_id)
    store.create_league(league_id, snapshot.get("name", league_id), season,
                        snapshot.get("cadence", "daily-change"),
                        snapshot.get("categories") or list(DEFAULT_CATEGORIES),
                        is_real=True, seed=None)
    for t in snapshot.get("teams", []):
        store.add_team(league_id, t["team_id"], t.get("name", t["team_id"]))
    n_events = apply_to_store(store, league_id, recon)
    for m in matchups:
        store.add_matchup(
            Matchup(league_id, m["period"], m["start"], m["end"], m["team_a"], m["team_b"])
        )

    if not recon.ok or not cw.ok:
        store.record_provenance(
            f"league:{league_id}",
            "imported with known gaps — " + "; ".join(
                filter(None, [cw.report() if not cw.ok else "",
                              "; ".join(recon.warnings + recon.conflicts)])),
            datetime.now(UTC).date().isoformat(),
        )

    return {
        "league_id": league_id,
        "teams": len(snapshot.get("teams", [])),
        "roster_events": n_events,
        "matchups": len(matchups),
        "players_matched": len(cw.mapping),
        "players_unresolved": len(cw.unmatched) + len(cw.ambiguous),
        "verified": recon.verified,
        "warnings": recon.warnings + recon.conflicts,
    }
