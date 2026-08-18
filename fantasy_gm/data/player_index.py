"""Forward-season roster inputs from NBA.com's ``playerindex`` (one call, three tables).

The store's forward-season tables — `forward_roster`, `incoming_players`, `player_positions` —
had a schema and no way to fill it, so the minutes model's role mechanism was inert on real
data and the slot-assignment problem had no positions at all. This is the ingest that fixes
that, and it is deliberately one endpoint: `playerindex` returns every player with their
**current team**, **listed position**, and **draft slot** in a single batched response, the
same shape and cost as the existing `LeagueGameLog` backfill.

What comes out of it:

* **Positions** — not derivable from box scores, which carry no position field whatsoever.
  This is the input design D4's Jonker-Volgenant assignment needs, and it removes the reason
  A-DRAFT-10 exists.
* **Forward rosters** — where each player sits *going into* the season, which is how the
  projection reacts to an offseason move at all. `playerindex` gives the team; depth is a
  separate step (:func:`build_forward_roster`), because the endpoint has no depth chart and
  inventing one would be worse than deriving it.
* **Incoming players** — anyone drafted for the upcoming season who has no NBA game logs.
  The draft pool derives from logs, so without this rookies are simply undraftable (D9).

Parsing is a pure function so it is testable with no network, following `nba_source`. The
fetch runs on a machine that can reach `stats.nba.com` (datacenter and VPN IPs are blocked by
NBA's WAF) and is disk-cached, so it is resumable and idempotent.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from fantasy_gm.data.cache import RawCache
from fantasy_gm.models import ForwardRoster, IncomingPlayer, PlayerPosition

ENDPOINT = "playerindex"

# ROSTER_STATUS is 1 for a player currently on an NBA roster. Anyone else is a free agent or
# historical, and putting them on a forward roster would fabricate a job they do not have.
ROSTERED = 1


@dataclass(frozen=True)
class IndexEntry:
    """One player's row, before it is split across the store's tables."""

    player_id: str
    player_name: str
    team: str
    position: str
    roster_status: int
    draft_year: str = ""
    draft_round: str = ""
    draft_number: str = ""
    from_year: str = ""
    to_year: str = ""

    @property
    def is_rostered(self) -> bool:
        return self.roster_status == ROSTERED and bool(self.team)


def _clean(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _as_int(v: Any) -> int | None:
    """Integer-valued fields, whatever JSON type they arrive as.

    ``playerindex`` types these inconsistently: `ROSTER_STATUS` comes back as a JSON float
    (``1.0``), `DRAFT_NUMBER` as an int, and both as `null` for undrafted players. Going
    through `float` first means ``"1.0"`` reads as 1 rather than failing — the strict
    ``int(str(v))`` this replaces returned None for every real row, which silently made
    every player unrostered and left `forward_roster` empty.
    """
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def parse_player_index(rows: list[dict]) -> list[IndexEntry]:
    """Pure mapping: ``playerindex`` rows -> :class:`IndexEntry`.

    Rows without a person id are skipped; everything else is kept, including unrostered
    players, so the caller decides what to do with a free agent rather than this function
    deciding for them.
    """
    out: list[IndexEntry] = []
    for r in rows:
        pid = _clean(r.get("PERSON_ID"))
        if not pid:
            continue
        first, last = _clean(r.get("PLAYER_FIRST_NAME")), _clean(r.get("PLAYER_LAST_NAME"))
        out.append(IndexEntry(
            player_id=pid,
            player_name=" ".join(p for p in (first, last) if p),
            team=_clean(r.get("TEAM_ABBREVIATION")),
            position=_clean(r.get("POSITION")),
            roster_status=_as_int(r.get("ROSTER_STATUS")) or 0,
            draft_year=_clean(r.get("DRAFT_YEAR")),
            draft_round=_clean(r.get("DRAFT_ROUND")),
            draft_number=_clean(r.get("DRAFT_NUMBER")),
            from_year=_clean(r.get("FROM_YEAR")),
            to_year=_clean(r.get("TO_YEAR")),
        ))
    return out


def to_positions(entries: list[IndexEntry], known_from: str) -> list[PlayerPosition]:
    return [PlayerPosition(e.player_id, e.position, known_from)
            for e in entries if e.position]


def to_incoming_players(
    store, entries: list[IndexEntry], season: str, known_from: str
) -> list[IncomingPlayer]:
    """Entries with no NBA game logs in the store — the players the pool cannot see.

    Membership is decided by *absence from the logs*, not by draft year: a second-round pick
    from two years ago who has still never played is in exactly the same position as this
    year's number one, and both need the prior path (D9).
    """
    known = {
        r["player_id"]
        for r in store.conn.execute("SELECT DISTINCT player_id FROM player_logs")
    }
    out: list[IncomingPlayer] = []
    for e in entries:
        if e.player_id in known or not e.is_rostered:
            continue
        out.append(IncomingPlayer(
            player_id=e.player_id, season=season, player_name=e.player_name,
            known_from=known_from, draft_pick=_as_int(e.draft_number),
            draft_team=e.team,
        ))
    return out


def build_forward_roster(
    store, entries: list[IndexEntry], season: str, known_from: str, *,
    as_of: str | None = None, window: int = 82, min_games: int = 10,
) -> list[ForwardRoster]:
    """Turn team assignments into depth-chart positions by ranking each new roster.

    ``playerindex`` says *where* a player is, not *where they sit*. Rather than leave depth
    unknown or source an external depth chart, rank each team's incoming roster by the
    minutes each player earned in their own history: the projected depth chart is what the
    new roster's own track records imply.

    This is what makes an offseason move actually move a projection. A player who was third
    in a thin rotation and signs with a deep team ranks lower on the new roster, so the role
    curve pulls their minutes down — with no manual entry and no external source. The
    assumption it rests on (minutes ordering carries across a team change) is A-DRAFT-12.

    Players with no usable history rank last among their new teammates; the rookie prior
    (D9) projects them instead, so their rank here only affects the players above them.
    """
    from fantasy_gm.projections.minutes import _minutes_of, _player_windows

    cut = as_of or known_from
    history = _player_windows(store.player_game_stream_asof(cut), window)

    def _claim(pid: str) -> float:
        mins = _minutes_of(history.get(pid, []))
        return statistics.fmean(mins) if len(mins) >= min_games else -1.0

    by_team: dict[str, list[IndexEntry]] = {}
    for e in entries:
        if e.is_rostered:
            by_team.setdefault(e.team, []).append(e)

    out: list[ForwardRoster] = []
    for team, roster in by_team.items():
        roster.sort(key=lambda e: (-_claim(e.player_id), e.player_id))
        for rank, e in enumerate(roster, start=1):
            role = "returning" if _claim(e.player_id) >= 0 else "no-history"
            out.append(ForwardRoster(e.player_id, season, team, rank, known_from, role))
    return out


# --- fetch + ingest ----------------------------------------------------------


def _fetch_rows(season: str, cache: RawCache) -> list[dict]:
    params = {"season": season}
    if cache.has(ENDPOINT, params):
        raw = cache.get(ENDPOINT, params)
    else:  # pragma: no cover - network path, runs on the user's machine
        from nba_api.stats.endpoints import playerindex

        raw = playerindex.PlayerIndex(season=season, timeout=60).get_normalized_dict()
        cache.set(ENDPOINT, params, raw)
    return raw.get("PlayerIndex") or next(iter(raw.values()), [])


def ingest_player_index(
    store, season: str, cache: RawCache, known_from: str, *,
    dry_run: bool = False, as_of: str | None = None,
) -> dict[str, int]:
    """Fetch (or read from cache) and write positions, forward rosters, and incoming players.

    ``dry_run`` fetches and parses without writing — the same fast check on the parse mapping
    that the season backfill offers, worth running first because this writes three tables.
    """
    entries = parse_player_index(_fetch_rows(season, cache))
    positions = to_positions(entries, known_from)
    incoming = to_incoming_players(store, entries, season, known_from)
    rosters = build_forward_roster(store, entries, season, known_from, as_of=as_of)
    counts = {
        "rows": len(entries),
        "rostered": sum(1 for e in entries if e.is_rostered),
        "positions": len(positions),
        "forward_roster": len(rosters),
        "incoming": len(incoming),
        "teams": len({r.team for r in rosters}),
    }
    if dry_run:
        return counts
    store.add_player_positions(positions)
    store.add_incoming_players(incoming)
    store.add_forward_roster(rosters)
    return counts
