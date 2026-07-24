"""Read-only import of the user's own past Yahoo leagues — the *secondary*, real-world
validation source (D7).

Scope for this milestone: a **read-only** loader that takes an already-fetched, offline
league export and inserts it marked ``is_real=True``, recording provenance wherever
point-in-time roster history is unavailable (Yahoo exposes final standings, draft
results, and weekly matchup results per past season, but roster-as-of-date history is
partial). The live OAuth fetch itself is a Non-Goal here — no network, no write actions
against the user's league — so the actual API call is deliberately left for the later
league-sync change.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
            datetime.now(timezone.utc).date().isoformat(),
        )

    for m in export.get("matchups", []):
        store.add_matchup(
            Matchup(league_id, m["period"], m["start"], m["end"], m["team_a"], m["team_b"])
        )
    return league_id
