"""Opening-night rosters reconstructed from the first games of a completed season.

`playerindex` is the clean source for "where did each player sit going into season X", and it
only answers for the season you can currently reach. For a *past* season it is unavailable
here (`stats.nba.com` is blocked), which left the A-DRAFT-5 backtest scoring the minutes model
with its role mechanism inert: `forward_roster` held 2026-27 only, so every player in the
2025-26 pool came back with `stated_rank = None` and the gate compared history-only shrinkage
against carry-forward — two nearly identical estimators.

This module reconstructs the missing input from the one record of opening-night rosters the
store does hold: the box scores themselves. A player's team in their first game of the season
is where they were rostered when it started.

**This is a backtest instrument and it carries a lookahead compromise. Do not use it live.**

Two things to be honest about, both of which the caller is made to see:

1. **Membership is read from rows dated after the cut.** Team identity on opening night was
   genuinely known on draft day, so the *content* is not lookahead — but the mechanism reads
   it out of post-cut rows, so the no-lookahead guarantee here is a documented argument
   rather than the structural one `player_game_stream_asof` provides everywhere else.
2. **It cannot see a player who never played.** Someone who missed the entire season to injury
   was on an opening-night roster and does not appear in any box score, so the reconstructed
   pool is biased toward players who stayed healthy. Every count this module returns reports
   that gap rather than absorbing it.

Depth is not reconstructed — it is derived by
:func:`~fantasy_gm.data.player_index.build_forward_roster` from history strictly before the
cut, exactly as it is for a real ingest, so no production from the season being scored
reaches the projection.
"""

from __future__ import annotations

from datetime import date, timedelta

from fantasy_gm.data.player_index import ROSTERED, IndexEntry, build_forward_roster
from fantasy_gm.models import ForwardRoster

# A player whose first appearance is months into the season was signed, promoted, or returned
# from injury — reading their eventual team as an opening-night assignment would be lookahead
# into transactions that had not happened yet. Two weeks is wide enough to cover a staggered
# opening week and the usual early rest, and narrow enough to exclude midseason arrivals.
OPENING_WINDOW_DAYS = 14

# Marks every row this module writes, so a reconstructed roster can never be mistaken for an
# ingested one in the store or in a report.
ROLE_PREFIX = "reconstructed"


class ReconstructionError(RuntimeError):
    """Raised when a season cannot be reconstructed honestly."""


def _season_bounds(store, season: str) -> tuple[str, str] | None:
    row = store.conn.execute(
        "SELECT MIN(game_date) a, MAX(game_date) b FROM player_logs WHERE season = ?", (season,)
    ).fetchone()
    return (row["a"], row["b"]) if row and row["a"] else None


def opening_night_entries(
    store, season: str, *, window_days: int = OPENING_WINDOW_DAYS
) -> tuple[list[IndexEntry], dict[str, int]]:
    """Each player's team in their first game of ``season``, within the opening window.

    Returns the entries and the counts that describe what the reconstruction could and could
    not see — the second half of that tuple is the point, not an afterthought.
    """
    bounds = _season_bounds(store, season)
    if bounds is None:
        raise ReconstructionError(f"season {season} is not in the store")
    start, end = bounds
    cutoff = (date.fromisoformat(start) + timedelta(days=window_days)).isoformat()

    rows = store.conn.execute(
        """SELECT player_id, team, MIN(game_date) AS first_game
             FROM player_logs WHERE season = ?
            GROUP BY player_id ORDER BY player_id""",
        (season,),
    ).fetchall()

    entries, late = [], 0
    for r in rows:
        if r["first_game"] > cutoff:
            late += 1
            continue
        entries.append(IndexEntry(
            player_id=r["player_id"], player_name="", team=r["team"],
            position="", roster_status=ROSTERED,
        ))
    counts = {
        "players_with_logs": len(rows),
        "opening_window": len(entries),
        "late_debut_excluded": late,
        "teams": len({e.team for e in entries}),
        "window_days": window_days,
        "season_start": start,
        "season_end": end,
        "cutoff": cutoff,
    }
    return entries, counts


def reconstruct_forward_roster(
    store, season: str, as_of: str, *,
    window_days: int = OPENING_WINDOW_DAYS, dry_run: bool = False,
) -> dict:
    """Write reconstructed opening-night rosters for ``season``, effective-dated ``as_of``.

    ``as_of`` is both the `known_from` stamped on the rows and the history cut the derived
    depth chart is built from, so a backtest that reads at ``as_of`` sees these rows and reads
    no production from ``season`` itself.

    Refuses a season that is still in progress: reconstructing rosters from a season you are
    also still projecting is the lookahead this whole module is trying to stay honest about.
    """
    entries, counts = opening_night_entries(store, season, window_days=window_days)
    if as_of >= counts["season_start"]:
        raise ReconstructionError(
            f"as_of={as_of} is inside season {season} (starts {counts['season_start']}); "
            "reconstruct from a cut before the season, or the depth chart reads its own answer"
        )

    rosters = build_forward_roster(store, entries, season, as_of, as_of=as_of)
    rosters = [ForwardRoster(r.player_id, r.season, r.team, r.depth_chart_pos, r.known_from,
                             f"{ROLE_PREFIX}:{r.role}") for r in rosters]
    counts["forward_roster"] = len(rosters)
    counts["movers"] = _count_movers(store, season, rosters)
    if not dry_run:
        # A reconstruction is a whole-snapshot write, not an increment. `add_forward_roster`
        # replaces by (player_id, season, known_from), so re-running with a *narrower* window
        # would leave the previous run's extra players behind and silently report a roster
        # thinner than the one actually in the store. Clear this snapshot's own rows first —
        # matched on the prefix, so a hand-entered override at the same date survives.
        counts["replaced"] = store.conn.execute(
            """DELETE FROM forward_roster
                WHERE season = ? AND known_from = ? AND role LIKE ?""",
            (season, as_of, f"{ROLE_PREFIX}:%"),
        ).rowcount
        store.conn.commit()
        store.add_forward_roster(rosters)
    return counts


def _count_movers(store, season: str, rosters: list) -> int:
    """How many reconstructed rows put a player somewhere other than where they last played.

    This is the number that decides whether the reconstruction is worth anything: a roster set
    with no movers gives the role model nothing that carry-forward does not already have.
    """
    prior = {
        r["player_id"]: r["team"]
        for r in store.conn.execute(
            """SELECT player_id, team, MAX(game_date) FROM player_logs
                WHERE season < ? GROUP BY player_id""", (season,))
    }
    return sum(1 for r in rosters if r.player_id in prior and prior[r.player_id] != r.team)
