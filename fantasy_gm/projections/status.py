"""Platform per-player status, ingested as effective-dated availability rows (R3, task 2.1).

Yahoo's ``draft_analysis.status`` field is fantasy-roster status ("Not Active" = not on any
fantasy roster), not injury state — pricing on it would zero half the league for going
undrafted. The injury state lives on the players collection's ``metadata`` subresource:
``status_full`` ("Questionable", "Out (short-term injury)", "Out For Season"), the dated
``injury_note``, and ``player_notes_last_timestamp`` (epoch of the last note, which doubles
as the effective date the designation was published).

This module follows adp.py's discipline: parsing is a pure function over an already-fetched
payload, identity resolution reuses the store-name bridge with every unresolved name
returned rather than dropped, and an unknown status string is reported loudly rather than
mapped to a guess — a silently mis-mapped status prices the wrong reality. Known non-health
roster states (:data:`NON_HEALTH_STATUS`) are excluded from the map entirely: ignoring them
silently would hide the next Yahoo roster state we have not priced, so they are returned as
``ignored`` in the ingest result and printed by the CLI.

**Status can only lower the availability rate, never raise it** — that is the rule the board
applies (:mod:`fantasy_gm.draft.board.project_availability`). An ACTIVE row is a statement
about tonight; the beta-binomial already prices a season rate conservatively, so raising a
measured rate back to 1.0 because "healthy" was published would rank a player fresh off a
long absence as durable. OUT and QUESTIONABLE are material downside information the
measured rate cannot see yet, so they cap it:

* ``OUT`` in season-ending wording (Out For Season / Out indefinitely) → rate 0.0
* other ``OUT`` (short-term injury, suspended) → rate x0.5 — misses material time, not the season
* ``QUESTIONABLE`` / Game Time Decision / day-to-day → rate x0.75 — misses some nights
* ``ACTIVE`` or no designation → the measured rate stands

The factors are a documented convention, not a measurement (0.5 mirrors the engine's
QUESTIONABLE production scale in ``engine/projection.py``); they live in :data:`RATE_FACTORS`
so draft-night experience can tune them in one place.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fantasy_gm.models import Availability
from fantasy_gm.projections.adp import (
    YAHOO_SOURCE,
    _collect_players,
    _flatten,
    build_name_resolver,
)

ACTIVE = "ACTIVE"
QUESTIONABLE = "QUESTIONABLE"
OUT = "OUT"

# status_full (folded) -> the model's three statuses. Anything not listed here is reported
# as unmapped at ingest and stored as nothing: a guessed status is a wrong status.
STATUS_MAP: dict[str, str] = {
    "": ACTIVE,
    "healthy": ACTIVE,
    "questionable": QUESTIONABLE,
    "game time decision": QUESTIONABLE,
    "gtd": QUESTIONABLE,
    "day-to-day": QUESTIONABLE,
    "day-to-day (not injury related)": QUESTIONABLE,
    "day-to-day (short-term injury)": QUESTIONABLE,
    "probable": ACTIVE,
    "out": OUT,
    "out (short-term injury)": OUT,
    "out for season": OUT,
    "out indefinitely": OUT,
    "suspended": OUT,
    "suspended by team": OUT,
    "injured reserve": OUT,
}

# The wording that says the season itself is gone, not a stretch of it.
SEASON_OUT = frozenset({"out for season", "out indefinitely"})

# status_full values that are roster states, not health designations ("Not Active" = not
# on an NBA roster — a G-League stash or an unsigned player). Ignored on purpose: pricing
# on them would zero players for roster reasons the availability model does not own (that
# is the depth/minutes model's job). They are reported as ``ignored`` so the ingest stays
# auditable without pretending they were unreadable.
NON_HEALTH_STATUS: frozenset[str] = frozenset({"not active"})

# Availability-rate factors the board applies to the measured (beta-binomial) rate, by
# status. ACTIVE is deliberately absent: no row may raise the rate, so ACTIVE means "no
# override". The 0.5 for OUT mirrors the engine's QUESTIONABLE production scale
# (engine/projection.py) — a shared convention, tunable here.
RATE_FACTORS: dict[str, float] = {
    QUESTIONABLE: 0.75,
    OUT: 0.5,
}


def rate_factor(avail: Availability) -> float:
    """The multiplier this designation puts on a measured availability rate.

    The ingest marks Yahoo's season-ending wording at certainty (confidence 1.0, see
    :func:`ingest_status`) and everything else at 0.9; :func:`rate_factor` reads that
    confidence back as the out-for-season marker. Both ends of the convention live in this
    module so the encoding cannot drift apart. Only rows this module ingests (``source`` =
    yahoo) are read that way — a hand-entered or beat-writer row keeps the plain status
    factor.
    """
    if (avail.status == OUT and avail.source == YAHOO_SOURCE
            and avail.confidence >= 1.0):
        return 0.0
    return RATE_FACTORS.get(avail.status, 1.0)


@dataclass(frozen=True)
class StatusRow:
    """One player's raw metadata line, before identity resolution."""

    yahoo_id: str
    name: str
    status_full: str
    injury_note: str = ""
    notes_ts: int | None = None


@dataclass(frozen=True)
class StatusIngestResult:
    """What ingestion did — including, explicitly, what it could not do."""

    stored: int
    rows: int
    unresolved: tuple[str, ...] = ()                  # names with no store player id
    unmapped: tuple[tuple[str, str], ...] = ()        # (name, raw status_full) with no mapping
    noted: tuple[str, ...] = ()                       # dated note but no designation to map
    ignored: tuple[str, ...] = ()                     # known non-health roster states
    designations: dict[str, int] = field(default_factory=dict)   # count by mapped status


# --- parsing (pure) ----------------------------------------------------------


def _as_int(v: Any) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(str(v))
    except ValueError:
        return None


def parse_player_metadata(payload: Mapping[str, Any] | list) -> list[StatusRow]:
    """Extract per-player metadata rows from a Yahoo ``players;out=metadata`` response.

    Tolerant by design, like :func:`parse_draft_analysis`: Yahoo's JSON shape varies between
    the collection and single-player forms, and a shape change should cost us the rows it
    broke, not the whole ingest. A player with no designation still yields a row (empty
    ``status_full``) — "the league is healthy" is coverage information too.
    """
    players = _collect_players(payload)
    rows: list[StatusRow] = []
    for p in players:
        flat = _flatten(p)
        name = flat.get("name")
        if isinstance(name, dict):
            name = name.get("full")
        elif isinstance(name, list):
            name = _flatten(name).get("full")
        pid = str(flat.get("player_id") or flat.get("player_key") or "")
        if not pid and not name:
            continue
        rows.append(StatusRow(
            yahoo_id=pid,
            name=str(name or ""),
            status_full=str(flat.get("status_full") or ""),
            injury_note=str(flat.get("injury_note") or ""),
            notes_ts=_as_int(flat.get("player_notes_last_timestamp")),
        ))
    return rows


def effective_date(row: StatusRow, fallback: str) -> str:
    """The date the designation is effective from: the note timestamp when Yahoo carried
    one (epoch, read as UTC), else the ingest date."""
    if row.notes_ts and row.notes_ts > 0:
        return datetime.fromtimestamp(row.notes_ts, tz=UTC).date().isoformat()
    return fallback


# --- ingestion ---------------------------------------------------------------


def ingest_status(
    store,
    rows: Iterable[StatusRow],
    known_from: str,
    *,
    resolver: Callable[[str], str | None] | None = None,
    source: str = YAHOO_SOURCE,
) -> StatusIngestResult:
    """Resolve and store metadata rows as effective-dated Availability designations.

    Only designations are stored: a player Yahoo carries no status for reads as "no
    designation" by the absence of a row, which the board treats as "the measured rate
    stands" — never as a silent gap. Unmapped status strings and unresolved names are
    returned, not dropped. Known non-health roster states (:data:`NON_HEALTH_STATUS`) are
    ignored and reported as ``ignored``. Re-ingesting the same designation is a no-op (the
    table's key is player_id + known_from + source).
    """
    resolve = resolver or build_name_resolver(store, known_from)
    records: list[Availability] = []
    unresolved: list[str] = []
    unmapped: list[tuple[str, str]] = []
    noted: list[str] = []
    ignored: list[str] = []
    designations: dict[str, int] = {}
    n = 0
    for row in rows:
        n += 1
        if not row.status_full.strip():
            if row.injury_note.strip():
                noted.append(row.name or row.yahoo_id)
            continue
        folded = row.status_full.strip().lower()
        if folded in NON_HEALTH_STATUS:
            ignored.append(row.name or row.yahoo_id)
            continue
        mapped = STATUS_MAP.get(folded)
        if mapped is None:
            unmapped.append((row.name or row.yahoo_id, row.status_full))
            continue
        pid = resolve(row.name) if row.name else None
        if pid is None:
            unresolved.append(row.name or row.yahoo_id)
            continue
        # Season-ending wording is a certainty (1.0); every other designation is the
        # platform's best current read (0.9). rate_factor reads this back — see its comment.
        confidence = (
            1.0
            if mapped == OUT and folded in SEASON_OUT
            else 0.9
        )
        records.append(Availability(
            pid, mapped, effective_date(row, known_from), source, confidence,
            row.injury_note,
        ))
        designations[mapped] = designations.get(mapped, 0) + 1
    if records:
        store.add_availability(records)
    return StatusIngestResult(len(records), n, tuple(unresolved), tuple(unmapped),
                              tuple(noted), tuple(ignored), designations)


def status_for_pool(
    store, pids: Iterable[str], as_of: str
) -> dict[str, Availability | None]:
    """Latest effective-dated designation per player known on or before ``as_of``.

    The batch form of ``store.availability_asof`` (one query rather than one per player);
    a player with no row reads as ``None`` — no designation, the measured rate stands.
    """
    ids = list(dict.fromkeys(pids))
    if not ids:
        return {}
    out: dict[str, Availability | None] = {pid: None for pid in ids}
    marks = ",".join("?" * len(ids))
    rows = store.conn.execute(
        f"SELECT * FROM availability WHERE player_id IN ({marks}) AND known_from <= ? "  # noqa: S608
        "ORDER BY known_from, confidence",
        (*ids, as_of),
    )
    for r in rows:
        out[r["player_id"]] = Availability(r["player_id"], r["status"], r["known_from"],
                                           r["source"], r["confidence"], r["note"])
    return out


# --- file + network ----------------------------------------------------------


def load_status_file(path: str | Path) -> list[StatusRow]:
    """Parse a saved metadata capture — either one raw payload or a paged fetch.

    The live fetch writes ``{"pages": [...]}``; a browser-saved single response is the
    payload itself. Both are the same rows to the caller.
    """
    payload = json.loads(Path(path).read_text())
    if isinstance(payload, dict) and "pages" in payload:
        rows: list[StatusRow] = []
        for page in payload["pages"]:
            rows.extend(parse_player_metadata(page))
        return rows
    return parse_player_metadata(payload)


def fetch_league_player_metadata(
    league_key: str,
    access_token: str,
    *,
    count: int = 25,
    limit: int = 500,
    sort: str = "AR",
    save_path: str | Path | None = None,
) -> list[StatusRow]:
    """Live paginated fetch of every league-relevant player's metadata (status + dated note).

    Same pagination discipline as :func:`fetch_draft_analysis`: ``start`` advances by
    ``count``, the walk stops at the first page carrying no parseable players, and
    ``limit`` bounds the loop. Sorted by AR (average rank) so page boundaries fall in ADP
    order and captures stay comparable across ingests. Raw pages are written to
    ``save_path`` when given, so the ingest can be re-run and audited offline.
    """
    import time as _time

    import requests

    url = (
        "https://fantasysports.yahooapis.com/fantasy/v2/league/"
        f"{league_key}/players"
        ";start={start};count={count};sort="
        f"{sort};out=metadata?format=json"
    )
    headers = {"Authorization": f"Bearer {access_token}"}
    pages: list = []
    start = 0
    while start < limit:
        resp = requests.get(url.format(start=start, count=count), headers=headers, timeout=30)
        if resp.status_code == 401:
            raise RuntimeError(
                "401 from Yahoo — token expired. Re-run scripts/yahoo_author.py "
                "and retry; the fetch is resumable from the saved pages."
            )
        resp.raise_for_status()
        batch = parse_player_metadata(resp.json())
        if not batch:
            break
        pages.append(resp.json())
        start += count
    if save_path is not None:
        Path(save_path).write_text(json.dumps({
            "league_key": league_key,
            "fetched_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            "out": "metadata",
            "pages": pages,
        }))
    if save_path is not None:
        return load_status_file(save_path)
    return [row for page in pages for row in parse_player_metadata(page)]
