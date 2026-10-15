"""Multi-source news ingest and event-driven surfacing (R10, section 9).

The board is priced once at arm; this module is what lets injury and roster news
*move* it during the draft without ever silently re-ranking it. The source roles were
fixed by probing what this machine can actually reach (2026-10-15):

* **The Yahoo fantasy API metadata is the pricing authority** — its designations carry
  the dated note and the certainty marker :func:`fantasy_gm.projections.rate_factor`
  reads. The public **injuries page** (`sports.yahoo.com/nba/injuries/`) carries the
  same designations with dates and no token: it ingests as availability rows with
  *plain* factors (it cannot claim a season-ending certainty the API did not — that
  marker is API-only by construction) and as ``designation`` news events. The
  **Yahoo NBA news RSS** is a beat-reporting wire: headlines name-matched against the
  pool directory, stored as ``news`` events.
* **NBA.com** publishes no reachable machine-readable injury or depth feed (the CDN
  denies this host, the official site 404s). Its one official signal is the
  ``playerindex`` roster-status refresh: a fresh fetch diffed against the stored
  forward roster yields ``roster-move`` events (:func:`roster_move_events`).
* **X** has no free, reliable pipe (the official API is paid; scraping is login-walled)
  so the adapter is a loud stub (:func:`fetch_x_events`) — a cost decision for Tim,
  never a silent absence.

Discipline, following :mod:`fantasy_gm.projections.status`: parsing is a pure function
over an already-fetched payload; identity resolution reuses the store-name bridge and
every unresolved or ambiguous name is returned rather than guessed; raw captures are
saved so an ingest can be re-run and audited offline. An event that names no pool
player can flag nothing — unresolved items are reported, never dropped silently.
"""

from __future__ import annotations

import html as _html
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from fantasy_gm.models import Availability, ForwardRoster, NewsEvent
from fantasy_gm.projections.adp import build_name_resolver, normalize_name
from fantasy_gm.projections.status import (
    OUT,
    QUESTIONABLE,
    STATUS_MAP,
    season_out_ahead,
    status_for_pool,
)

SOURCE_WEB = "yahoo-web"
SOURCE_NEWS = "yahoo-news"
SOURCE_NBA = "nba"

KIND_DESIGNATION = "designation"
KIND_NEWS = "news"
KIND_ROSTER_MOVE = "roster-move"

INJURIES_URL = "https://sports.yahoo.com/nba/injuries/"
RSS_URL = "https://sports.yahoo.com/nba/rss/"


# --- parsing (pure) -----------------------------------------------------------


@dataclass(frozen=True)
class InjuryPageRow:
    """One row of Yahoo's public injuries page, before identity resolution."""

    name: str
    pos: str
    status_full: str  # as published, e.g. "Out (ACL)"
    date_md: str  # "M/D" as published; resolved to a real date at ingest
    web_id: str = ""  # Yahoo's own id (/nba/players/<id>/) — kept for audit only


@dataclass(frozen=True)
class NewsItem:
    """One wire item (RSS entry), before identity resolution."""

    title: str
    published_on: str  # ISO date
    link: str = ""
    summary: str = ""


def _cell_text(cell: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()


def parse_yahoo_injuries_page(page: str) -> list[InjuryPageRow]:
    """Extract the injury rows from a saved injuries-page capture.

    The page is plain tables of ``Player | Pos | Status | Date`` rows (repeated header
    rows between sections). Parsing is structural, not CSS-class-based — Yahoo's class
    hashes change per deploy, the table shape does not. Rows without a player link are
    skipped (headers); a row with a link but missing cells is kept only if the status
    and date are both readable, otherwise skipped — a half-row would ingest as a
    designation without a date, which effective-dating cannot price.
    """
    out: list[InjuryPageRow] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
        if "<th" in row.lower():
            continue
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)
        link = re.search(r'href="/nba/players/(\d+)/"[^>]*>([^<]+)<', row)
        if not link or len(cells) < 4:
            continue
        name = _cell_text(link.group(2))
        pos, status_full, date_md = (_cell_text(c) for c in cells[1:4])
        if not (name and status_full and date_md):
            continue
        out.append(InjuryPageRow(name, pos, status_full, date_md, link.group(1)))
    return out


def page_date_to_iso(date_md: str, today: date) -> str:
    """Resolve the page's ``M/D`` to an ISO date: the most recent occurrence at or
    before ``today`` — a listing published "7/15" and read on 10-15 is this July,
    not a date seven years in the future."""
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", date_md.strip())
    if not m:
        return today.isoformat()
    month, day = int(m.group(1)), int(m.group(2))
    if m.group(3):
        return date(int(m.group(3)), month, day).isoformat()
    candidate = date(today.year, month, day)
    if candidate > today:
        candidate = date(today.year - 1, month, day)
    return candidate.isoformat()


def normalize_web_status(status_full: str) -> str:
    """Fold the page's wording to a :data:`STATUS_MAP` key.

    The page annotates every designation with the body part or reason ("Out (ACL)",
    "Questionable (Rest)"); the fold strips the parenthetical and keeps the base
    wording, so an unannotated form and an annotated one map the same. Anything that
    survives folding with no mapping is reported at ingest, never guessed.
    """
    folded = status_full.strip().lower()
    base = re.sub(r"\(.*?\)", "", folded).strip()
    return base or folded


def parse_yahoo_rss(xml_text: str) -> list[NewsItem]:
    """Parse the Yahoo NBA news RSS into dated items.

    Tolerant by design: an item missing a title or date is skipped (it can neither be
    displayed nor effective-dated), a malformed document yields what parsed before the
    error plus a raised exception — the fetch wrapper reports it, never swallows it.
    """
    root = ET.fromstring(xml_text)
    out: list[NewsItem] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if not title or not pub:
            continue
        try:
            published_on = parsedate_to_datetime(pub).date().isoformat()
        except (TypeError, ValueError):
            continue
        out.append(
            NewsItem(
                title=title,
                published_on=published_on,
                link=(item.findtext("link") or "").strip(),
                summary=_cell_text(item.findtext("description") or ""),
            )
        )
    return out


def roster_move_events(
    before: Mapping[str, ForwardRoster],
    entries: Iterable[Any],
    known_from: str,
) -> list[NewsEvent]:
    """Diff a fresh ``playerindex`` fetch against the stored forward roster.

    ``before`` is the stored map (:meth:`Store.forward_roster_map_asof`) read at a date
    *before* the rows being compared were written; ``entries`` are the fetched
    :class:`~fantasy_gm.data.player_index.IndexEntry` rows. A player whose current team
    differs from his stored row — or who has left NBA rosters entirely — becomes a
    ``roster-move`` event dated the refresh day. Pure: the store is read by the caller.
    """
    out: list[NewsEvent] = []
    for e in entries:
        prev = before.get(e.player_id)
        if prev is None:
            continue
        if e.is_rostered and e.team and e.team != prev.team:
            out.append(
                NewsEvent(
                    source=SOURCE_NBA,
                    published_on=known_from,
                    kind=KIND_ROSTER_MOVE,
                    headline=f"{e.player_name}: {prev.team} -> {e.team}",
                    player_id=e.player_id,
                    name=e.player_name,
                )
            )
        elif not e.is_rostered:
            out.append(
                NewsEvent(
                    source=SOURCE_NBA,
                    published_on=known_from,
                    kind=KIND_ROSTER_MOVE,
                    headline=f"{e.player_name}: no longer on an NBA roster (was {prev.team})",
                    player_id=e.player_id,
                    name=e.player_name,
                )
            )
    return out


# --- name matching -------------------------------------------------------------


def build_headline_matcher(store) -> tuple[dict[str, str], Callable[[str], tuple[str | None, str]]]:
    """A headline -> player matcher over the whole known directory.

    Returns ``(by_id, match)``. ``match(text)`` returns ``(player_id, name)`` when the
    text names exactly one directory player (full normalized name, word-bounded — a
    bare surname would false-match across teams), ``(None, "")`` when it names none,
    and ``(None, "<ambiguous>")`` listing the ambiguity in the name so the caller can
    report rather than guess. Rebuilds the same directory manual entry uses, so news
    and hand-typing resolve against one universe.
    """
    by_id: dict[str, str] = {}
    for r in store.conn.execute("SELECT DISTINCT player_id, player_name FROM player_logs"):
        by_id[str(r["player_id"])] = r["player_name"]
    for r in store.conn.execute("SELECT player_id, player_name FROM incoming_players"):
        by_id[str(r["player_id"])] = r["player_name"]
    by_key: dict[str, list[str]] = {}
    for pid, name in by_id.items():
        by_key.setdefault(normalize_name(name), []).append(pid)

    def match(text: str) -> tuple[str | None, str]:
        norm = f" {normalize_name(text)} "
        hits: dict[str, str] = {}
        for key, ids in by_key.items():
            if key and f" {key} " in norm:
                for pid in ids:
                    hits[pid] = by_id[pid]
        if not hits:
            return None, ""
        if len(hits) == 1:
            pid, name = next(iter(hits.items()))
            return pid, name
        return None, "ambiguous: " + ", ".join(sorted(hits.values())[:4])

    return by_id, match


# --- ingestion -----------------------------------------------------------------


@dataclass(frozen=True)
class NewsIngestResult:
    """What one ingest pass did, including everything it could not do."""

    rows: int
    stored_designations: int
    stored_events: int  # rows actually NEW (re-ingests are no-ops)
    unresolved: tuple[str, ...] = ()
    unmapped: tuple[tuple[str, str], ...] = ()
    ambiguous: tuple[str, ...] = ()


def ingest_yahoo_injuries(
    store,
    rows: Iterable[InjuryPageRow],
    known_from: str,
    *,
    resolver: Callable[[str], str | None] | None = None,
    today: str | None = None,
) -> NewsIngestResult:
    """Store injuries-page rows as availability designations AND designation events.

    Effective date is the day the page listed the designation (resolved from ``M/D``
    against ``today``), not the fetch day — the same contract the API rows keep. The
    availability source is ``yahoo-web``, which :func:`rate_factor` prices at the plain
    factors: a page row can cap a rate but never zero it, because page wording ("Out
    (ACL)") does not carry the API's season-ending certainty. Re-ingesting the same
    page is a no-op on both tables (each keyed by its own identity).
    """
    resolve = resolver or build_name_resolver(store, known_from)
    today_d = date.fromisoformat(today) if today else datetime.now(UTC).date()
    avail: list[Availability] = []
    events: list[NewsEvent] = []
    unresolved: list[str] = []
    unmapped: list[tuple[str, str]] = []
    n = 0
    for row in rows:
        n += 1
        mapped = STATUS_MAP.get(normalize_web_status(row.status_full))
        if mapped is None:
            unmapped.append((row.name, row.status_full))
            continue
        pid = resolve(row.name) if row.name else None
        if pid is None:
            unresolved.append(row.name)
            continue
        published = page_date_to_iso(row.date_md, today_d)
        avail.append(
            Availability(
                pid,
                mapped,
                published,
                SOURCE_WEB,
                0.85,
                f"{row.pos} — {row.status_full} (yahoo.com injuries page)",
            )
        )
        events.append(
            NewsEvent(
                source=SOURCE_WEB,
                published_on=published,
                kind=KIND_DESIGNATION,
                headline=f"{row.name}: {row.status_full}",
                player_id=pid,
                name=row.name,
                note=row.pos,
                url=INJURIES_URL,
            )
        )
    if avail:
        store.add_availability(avail)
    new_events = store.add_news_events(events) if events else 0
    return NewsIngestResult(n, len(avail), new_events, tuple(unresolved), tuple(unmapped))


def ingest_yahoo_news(
    store,
    items: Iterable[NewsItem],
    known_from: str,
    *,
    matcher: Callable[[str], tuple[str | None, str]] | None = None,
) -> NewsIngestResult:
    """Store RSS items as ``news`` events, matched to pool players by full name.

    Only items that name exactly one directory player are stored — the RSS wire covers
    the whole league, and an event no rec card can ever read is store noise. Unmatched
    and ambiguous items are counted in the result (the CLI prints them), never silent.
    """
    if matcher is None:
        _, matcher = build_headline_matcher(store)
    events: list[NewsEvent] = []
    misses: list[str] = []
    ambiguous: list[str] = []
    n = 0
    for item in items:
        n += 1
        pid, name = matcher(item.title)
        if pid is None:
            if name.startswith("ambiguous:"):
                ambiguous.append(name)
            else:
                misses.append(item.title)
            continue
        events.append(
            NewsEvent(
                source=SOURCE_NEWS,
                published_on=item.published_on,
                kind=KIND_NEWS,
                headline=item.title,
                player_id=pid,
                name=name,
                note=item.summary[:200],
                url=item.link,
            )
        )
    new_events = store.add_news_events(events) if events else 0
    return NewsIngestResult(n, 0, new_events, tuple(misses), (), tuple(ambiguous))


def fetch_x_events(handle: str) -> list[NewsEvent]:
    """The X adapter stub (task 9.5): loud, configured-only, never scraped.

    There is no free, reliable pipe to X (the official API is paid; scraping is
    login-walled and against terms). Until Tim decides the cost is worth paying and a
    paid key is configured, this raises rather than returning an empty list — an
    absent source must be visible, not silently missing from the mix.
    """
    raise RuntimeError(
        f"X adapter is a stub (source 'x:{handle}'): no free pipe exists — the official "
        "API is paid and scraping is login-walled. Configure a paid API key (a cost "
        "decision for Tim) before this source can ingest."
    )


# --- surfacing -----------------------------------------------------------------


def flags_for_pool(
    store,
    player_ids: Iterable[str],
    as_of: str,
    *,
    forward_season: str | None = None,
) -> dict[str, str]:
    """The compact per-player flag the rec card shows: designation + news + depth.

    ``OUT``/``QUE`` from the effective-dated designation; ``Nn`` — the count of stored
    news events that are not the designation itself (wire items, roster moves);
    ``+Nd`` — the season-OUT promotion count (task 9.6), i.e. season-OUT teammates
    listed ahead of him on the stated roster. ``forward_season`` keys that roster read:
    the season being drafted into — the same roster the board prices from — never the
    ranked-data season a caller happens to pass. Surfacing only: nothing here re-ranks —
    under the measured basis this flag is the *entire* effect of the news, by design.
    """
    ids = list(dict.fromkeys(player_ids))
    if not ids:
        return {}
    statuses = status_for_pool(store, ids, as_of)
    news = store.news_asof(ids, as_of, per_player=5)
    out: dict[str, str] = {}
    for pid in ids:
        parts: list[str] = []
        s = statuses.get(pid)
        if s is not None and s.status == OUT:
            parts.append("OUT")
        elif s is not None and s.status == QUESTIONABLE:
            parts.append("QUE")
        n_news = sum(1 for e in news.get(pid, ()) if e.kind != KIND_DESIGNATION)
        if n_news:
            parts.append(f"{n_news}n")
        if forward_season is not None:
            ahead, _ = season_out_ahead(store, pid, forward_season, as_of)
            if ahead:
                parts.append(f"+{ahead}d")
        out[pid] = " ".join(parts)
    return out


def source_age(store, as_of: str) -> list[dict[str, Any]]:
    """Per-source coverage strip: newest event date + row count known at ``as_of``.

    The watch page's source-age strip: a stale wire must be visible as stale, not
    merely absent from the flags.
    """
    rows = store.conn.execute(
        """SELECT source, COUNT(*) AS n, MAX(published_on) AS latest
           FROM news_events WHERE known_from <= ? GROUP BY source ORDER BY source""",
        (as_of,),
    ).fetchall()
    return [{"source": r["source"], "events": r["n"], "latest": r["latest"] or ""} for r in rows]


# --- file + network ------------------------------------------------------------


def load_injuries_file(path: str | Path) -> list[InjuryPageRow]:
    """Parse a saved injuries-page capture."""
    return parse_yahoo_injuries_page(Path(path).read_text(encoding="utf-8"))


def load_rss_file(path: str | Path) -> list[NewsItem]:
    """Parse a saved RSS capture."""
    return parse_yahoo_rss(Path(path).read_text(encoding="utf-8"))


def fetch_yahoo_injuries_page(
    *,
    url: str = INJURIES_URL,
    save_path: str | Path | None = None,
) -> list[InjuryPageRow]:
    """Live fetch of the public injuries page (no token), raw capture saved when given."""
    import requests

    resp = requests.get(url, timeout=30, headers={"User-Agent": "fantasy-gm/1.0"})
    resp.raise_for_status()
    if save_path is not None:
        Path(save_path).write_text(resp.text, encoding="utf-8")
    return parse_yahoo_injuries_page(resp.text)


def fetch_yahoo_rss(
    *,
    url: str = RSS_URL,
    save_path: str | Path | None = None,
) -> list[NewsItem]:
    """Live fetch of the Yahoo NBA news RSS (no token), raw capture saved when given."""
    import requests

    resp = requests.get(url, timeout=30, headers={"User-Agent": "fantasy-gm/1.0"})
    resp.raise_for_status()
    if save_path is not None:
        Path(save_path).write_text(resp.text, encoding="utf-8")
    return parse_yahoo_rss(resp.text)
