"""Average draft position — a market observation, not a projection (design D10).

ADP is what the opponent model drafts from: other teams pick from an ADP distribution with
noise, which matches the published simulations, needs no per-league setup, and generalizes.
It comes from Yahoo's ``draft_analysis`` endpoint, which is free with the OAuth token the
live draft sync already requires, so there is no third-party ADP dependency and no licensing
question.

Three things this module keeps separate:

* **Parsing** (:func:`parse_draft_analysis`) is a pure function over an already-fetched
  payload, so it is unit-testable offline — the same discipline ``nba_source`` follows.
* **Resolution** (:func:`build_name_resolver`) maps Yahoo's player identity onto the store's
  player ids. Yahoo keys by its own id, the store keys by NBA's, and nothing bridges them yet
  (the crosswalk is in flight on the parallel Yahoo branch). Until it lands, names are the
  bridge — and **every name that fails to resolve is returned, not dropped**, because a
  silently missing star is a mispriced draft.
* **Absence** (:func:`adp_for_pool`) is explicit. A player with no ADP gets ``None``, never a
  default position — the caller has to decide what an undrafted-in-the-market player is
  worth rather than inheriting a number that looks measured.

The network fetch itself is deliberately absent: it needs the OAuth flow from task 4.1, which
lives on the draft-surface track. :func:`fetch_draft_analysis` says so rather than pretending.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fantasy_gm.models import ADP

YAHOO_SOURCE = "yahoo"


@dataclass(frozen=True)
class DraftAnalysisRow:
    """One player's raw draft-analysis line, before identity resolution."""

    yahoo_id: str
    name: str
    average_pick: float | None
    percent_drafted: float | None
    average_round: float | None = None


@dataclass(frozen=True)
class AdpIngestResult:
    """What ingestion did — including, explicitly, what it could not do."""

    stored: int
    rows: int
    unresolved: tuple[str, ...] = ()      # names with no store player id
    missing_adp: tuple[str, ...] = ()     # rows carrying no average pick at all

    @property
    def resolved(self) -> int:
        return self.rows - len(self.unresolved)


# --- parsing (pure) ----------------------------------------------------------


def _as_float(v: Any) -> float | None:
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace("%", ""))
    except ValueError:
        return None


def _flatten(node: Any) -> dict[str, Any]:
    """Collapse Yahoo's list-of-dicts-and-lists player blob into one mapping.

    Yahoo returns a player as a nested mix of lists and dicts rather than a flat object;
    the fields we want (``player_id``, ``name``, ``draft_analysis``) are scattered through it.
    """
    out: dict[str, Any] = {}
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if isinstance(v, (dict, list)):
                    stack.append(v)
                    if k in ("name", "draft_analysis"):
                        out[k] = v
                else:
                    out.setdefault(k, v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return out


def parse_draft_analysis(payload: Mapping[str, Any] | list) -> list[DraftAnalysisRow]:
    """Extract draft-analysis rows from a Yahoo ``players;out=draft_analysis`` response.

    Tolerant by design: Yahoo's JSON shape varies between the collection and single-player
    forms, and a shape change should cost us the rows it broke, not the whole ingest.
    """
    players = _collect_players(payload)
    rows: list[DraftAnalysisRow] = []
    for p in players:
        flat = _flatten(p)
        analysis = _flatten(flat.get("draft_analysis") or {})
        name = flat.get("name")
        if isinstance(name, dict):
            name = name.get("full")
        elif isinstance(name, list):
            name = _flatten(name).get("full")
        pid = str(flat.get("player_id") or flat.get("player_key") or "")
        if not pid and not name:
            continue
        rows.append(DraftAnalysisRow(
            yahoo_id=pid,
            name=str(name or ""),
            average_pick=_as_float(analysis.get("average_pick")),
            percent_drafted=_as_float(analysis.get("percent_drafted")),
            average_round=_as_float(analysis.get("average_round")),
        ))
    return rows


def _collect_players(payload: Any) -> list[Any]:
    """Find every player node in a Yahoo response, whatever level it is nested at."""
    found: list[Any] = []
    stack = [payload]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "player" in cur:
                found.append(cur["player"])
            for v in cur.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(x for x in cur if isinstance(x, (dict, list)))
    return found


# --- identity resolution -----------------------------------------------------


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    """Fold a display name to a comparison key: accents, punctuation, and suffixes removed."""
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    # Apostrophes and periods are elided, not split on: "De'Aaron" is one name, and turning
    # it into "de aaron" would fail to match the same player written "DeAaron".
    folded = re.sub(r"[.'’]", "", folded.lower())
    folded = re.sub(r"[^a-z ]", " ", folded)
    parts = [p for p in folded.split() if p and p not in _SUFFIXES]
    return " ".join(parts)


def build_name_resolver(store, as_of: str = "9999-12-31") -> Callable[[str], str | None]:
    """Resolve a display name to a store player id, using names seen in the store.

    A stopgap until the Yahoo id crosswalk lands. Ambiguous names (two players folding to the
    same key) resolve to ``None`` rather than to a coin flip — a wrong id is worse than a
    reported gap.
    """
    by_key: dict[str, set[str]] = {}
    for r in store.conn.execute(
        "SELECT DISTINCT player_id, player_name FROM player_logs WHERE game_date <= ?", (as_of,)
    ):
        by_key.setdefault(normalize_name(r["player_name"]), set()).add(r["player_id"])
    for r in store.conn.execute(
        "SELECT player_id, player_name FROM incoming_players WHERE known_from <= ?", (as_of,)
    ):
        by_key.setdefault(normalize_name(r["player_name"]), set()).add(r["player_id"])

    def resolve(name: str) -> str | None:
        ids = by_key.get(normalize_name(name))
        return next(iter(ids)) if ids and len(ids) == 1 else None

    return resolve


# --- ingestion ---------------------------------------------------------------


def ingest_adp(
    store,
    rows: Iterable[DraftAnalysisRow],
    season: str,
    known_from: str,
    *,
    resolver: Callable[[str], str | None] | None = None,
    source: str = YAHOO_SOURCE,
) -> AdpIngestResult:
    """Resolve and store draft-analysis rows as effective-dated ADP.

    Rows without an average pick are reported in ``missing_adp`` and not stored: a market
    that has not priced a player is information, and inventing a position would destroy it.
    """
    resolve = resolver or build_name_resolver(store, known_from)
    records: list[ADP] = []
    unresolved: list[str] = []
    missing: list[str] = []
    n = 0
    for row in rows:
        n += 1
        if row.average_pick is None:
            missing.append(row.name or row.yahoo_id)
            continue
        pid = resolve(row.name) if row.name else None
        if pid is None:
            unresolved.append(row.name or row.yahoo_id)
            continue
        pct = row.percent_drafted
        if pct is not None and pct > 1.0:  # Yahoo reports either 0-1 or 0-100
            pct /= 100.0
        records.append(ADP(pid, season, row.average_pick, source, known_from,
                           adp_std=None, pct_drafted=pct))
    if records:
        store.add_adp(records)
    return AdpIngestResult(len(records), n, tuple(unresolved), tuple(missing))


def ingest_adp_file(
    store, path: str | Path, season: str, known_from: str, *,
    source: str = YAHOO_SOURCE, resolver: Callable[[str], str | None] | None = None,
) -> AdpIngestResult:
    """Ingest a saved ``draft_analysis`` payload from disk.

    The manual path, and the one the tests use: the live fetch needs OAuth (task 4.1), but a
    payload saved from a browser session is the same JSON and is enough to draft against.
    """
    payload = json.loads(Path(path).read_text())
    return ingest_adp(store, parse_draft_analysis(payload), season, known_from,
                      resolver=resolver, source=source)


def fetch_draft_analysis(league_key: str) -> list[DraftAnalysisRow]:  # pragma: no cover
    """Live Yahoo fetch — not available until the OAuth flow lands (task 4.1).

    Kept as an explicit, named gap rather than a silent one: the draft-surface track owns the
    token, and this module will call through it when it exists. Use
    :func:`ingest_adp_file` with a saved payload in the meantime.
    """
    raise NotImplementedError(
        "Yahoo draft_analysis needs the OAuth flow from task 4.1 (draft-surface). "
        f"Save the payload for {league_key} and use ingest_adp_file() until it lands."
    )


# --- explicit absence --------------------------------------------------------


def adp_for_pool(
    store, season: str, as_of: str, pool: Iterable[str], *, source: str | None = None
) -> dict[str, ADP | None]:
    """ADP for every player in ``pool``, with ``None`` where the market has not priced them.

    The explicit-absence view the ``historical-data-pipeline`` requirement asks for: the
    caller sees the gap and decides, instead of inheriting a default draft position.
    """
    known = store.adp_asof(season, as_of, source=source)
    return {pid: known.get(pid) for pid in pool}
