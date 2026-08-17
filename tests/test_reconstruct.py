"""Opening-night rosters reconstructed from a completed season's own box scores.

This is a backtest instrument with a deliberate lookahead compromise, so the properties worth
pinning are the ones that keep the compromise bounded and visible: it refuses a season it would
be reading its own answer out of, it excludes midseason arrivals rather than backdating them,
it reports the players it structurally cannot see, and re-running it replaces its snapshot
instead of accumulating one.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from fantasy_gm.data.reconstruct import (
    ROLE_PREFIX,
    ReconstructionError,
    opening_night_entries,
    reconstruct_forward_roster,
)
from fantasy_gm.data.store import Store
from fantasy_gm.models import PlayerGameLog, UsageRole
from tests.test_projection_model import PRIOR, START, _line, _seed_league

# The prior season is what the fixture seeds, so it is the one there is a season to rebuild.
CUT = (START - timedelta(days=1)).isoformat()


@pytest.fixture
def store() -> Store:
    s = Store(":memory:")
    _seed_league(s)
    return s


def _log(store: Store, pid: str, team: str, day: int, minutes: float = 25.0) -> None:
    """One box line plus its usage row — minutes live in `usage_role`, not in the box stats."""
    d = (START + timedelta(days=day)).isoformat()
    store.upsert_player_logs([PlayerGameLog(
        f"g-{pid}-{day}", PRIOR, d, pid, f"Player {pid}", team, _line(minutes))])
    store.add_usage_role([UsageRole(pid, d, minutes, minutes * 0.45, minutes >= 24, 3)])


# --- what it can and cannot see ----------------------------------------------


def test_a_player_is_placed_on_the_team_they_opened_the_season_with(store):
    _log(store, "mover", "DDD", day=0)
    entries, _ = opening_night_entries(store, PRIOR)
    assert {e.player_id: e.team for e in entries}["mover"] == "DDD"
    assert all(e.is_rostered for e in entries)


def test_a_midseason_arrival_is_excluded_rather_than_backdated(store):
    """Reading a December signing's team as an opening-night assignment would be lookahead
    into a transaction that had not happened at the cut."""
    _log(store, "late", "DDD", day=90)
    entries, counts = opening_night_entries(store, PRIOR)
    assert "late" not in {e.player_id for e in entries}
    assert counts["late_debut_excluded"] >= 1


def test_the_window_is_reported_not_absorbed(store):
    """The players it cannot see are the honesty of the whole instrument, so the counts have
    to carry them out to the caller."""
    _log(store, "late", "DDD", day=90)
    _, counts = opening_night_entries(store, PRIOR)
    assert counts["opening_window"] + counts["late_debut_excluded"] == counts["players_with_logs"]
    assert counts["season_start"] and counts["cutoff"] > counts["season_start"]


def test_widening_the_window_admits_more_players(store):
    _log(store, "late", "DDD", day=40)
    narrow, _ = opening_night_entries(store, PRIOR, window_days=14)
    wide, _ = opening_night_entries(store, PRIOR, window_days=60)
    assert len(wide) > len(narrow)


def test_a_season_the_store_does_not_hold_is_an_error(store):
    with pytest.raises(ReconstructionError):
        opening_night_entries(store, "1999-00")


# --- the guards --------------------------------------------------------------


def test_a_cut_inside_the_season_is_refused(store):
    """Deriving the depth chart from a cut inside the season being scored would let it read
    its own answer."""
    inside = (START + timedelta(days=30)).isoformat()
    with pytest.raises(ReconstructionError, match="inside season"):
        reconstruct_forward_roster(store, PRIOR, inside)


def test_every_written_row_is_marked_as_reconstructed(store):
    """A reconstructed roster must never be mistakable for an ingested one."""
    reconstruct_forward_roster(store, PRIOR, CUT)
    roles = [r["role"] for r in store.conn.execute("SELECT role FROM forward_roster")]
    assert roles and all(r.startswith(f"{ROLE_PREFIX}:") for r in roles)


def test_dry_run_writes_nothing(store):
    counts = reconstruct_forward_roster(store, PRIOR, CUT, dry_run=True)
    assert counts["forward_roster"] > 0
    assert store.conn.execute("SELECT COUNT(*) c FROM forward_roster").fetchone()["c"] == 0


# --- idempotency -------------------------------------------------------------


def test_rerunning_narrower_replaces_the_snapshot_rather_than_leaving_a_wider_one(store):
    """Writes replace by (player_id, season, known_from), so without an explicit clear a
    narrower re-run would leave the wider run's extra players behind and report a roster
    thinner than the one actually stored."""
    _log(store, "late", "DDD", day=40)
    wide = reconstruct_forward_roster(store, PRIOR, CUT, window_days=60)
    narrow = reconstruct_forward_roster(store, PRIOR, CUT, window_days=14)
    assert narrow["forward_roster"] < wide["forward_roster"]

    stored = store.conn.execute("SELECT COUNT(*) c FROM forward_roster").fetchone()["c"]
    assert stored == narrow["forward_roster"]


def test_the_reconstruction_lands_where_the_projection_reads_its_stated_rank(store):
    """The whole point of the instrument. `minutes.stated_rank` is read from exactly this
    lookup, and with `forward_roster` empty for the season being scored it returns None for
    every player — which is how the A-DRAFT-5 gate came to measure the model with its role
    mechanism inert.

    Asserted at the lookup rather than through a full projection because this fixture seeds
    only one season, so there is no pre-cut history for the model to fit against; the
    end-to-end effect is recorded against real data in A-DRAFT-5.
    """
    pid = next(e.player_id for e in opening_night_entries(store, PRIOR)[0])
    assert store.forward_roster_asof(pid, PRIOR, CUT) is None

    reconstruct_forward_roster(store, PRIOR, CUT)
    fwd = store.forward_roster_asof(pid, PRIOR, CUT)
    assert fwd is not None and fwd.depth_chart_pos >= 1
