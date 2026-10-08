"""The draft-night surface: live ingestion, manual fallback, on-the-clock output.

Draft night is one person watching a clock, so the design rule here is: **never
leave the person on the clock without an answer.** Three surfaces, in order of
preference:

* **Live poll** (4.1) — Yahoo's ``draft_results`` every few seconds. The only source
  whose divergence from reality is the platform's rather than ours.
* **Manual entry** (4.2) — type-ahead name resolution through the store's player
  index. The safety net: usable with no network at all, and usable after a live poll
  failed mid-draft against whatever state was already ingested (4.3).
* **On-the-clock output** (4.4) — ranked candidates with pick value, category impact
  and survival probability. When the clock forces a degrade to the static board, the
  output says so (4.5).

Discrepancies between ingested and platform state are surfaced, never silently
reconciled (4.3).
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from fantasy_gm.config import FORWARD_SEASON
from fantasy_gm.draft.opponents import survival_probability

PICK_SOURCE_LIVE = "live"
PICK_SOURCE_MANUAL = "manual"


def _seat_of(pick_number: int, n_teams: int) -> int:
    """Which team (1-indexed) makes ``pick_number`` in a snake draft."""
    rnd, off = divmod(pick_number - 1, n_teams)
    return off + 1 if rnd % 2 == 0 else n_teams - off


@dataclass
class Pick:
    """One pick as recorded. ``source`` is what to distrust if reality disagrees."""

    number: int
    player_id: str
    team_seat: int
    source: str
    name: str = ""
    at: str = ""


@dataclass
class DraftState:
    """The draft as we believe it to be. ``picks`` is the ground truth we hold."""

    league_key: str
    n_teams: int = 12
    n_rounds: int = 13
    my_seat: int = 1  # 1-indexed
    picks: list[Pick] = field(default_factory=list)

    # --- draft mechanics ---------------------------------------------------------

    @property
    def pick_number(self) -> int:
        """The pick we are on (next to be made), 1-indexed."""
        return len(self.picks) + 1

    @property
    def on_the_clock(self) -> int:
        return _seat_of(self.pick_number, self.n_teams)

    def picks_until_my_next(self) -> int:
        """Picks by others before our next selection (survival horizon).

        The pick currently being made counts as an other-pick unless it is ours: when
        we are on the clock our current pick is ours, so the horizon starts after it.
        """
        n = self.n_teams
        pos = self.pick_number
        on_the_clock = _seat_of(pos, n) == self.my_seat
        start = pos + 1 if on_the_clock else pos
        for k in range(start, start + 2 * n + 2):
            if _seat_of(k, n) == self.my_seat:
                return k - pos - (1 if on_the_clock else 0)
        return 0

    def is_my_pick(self) -> bool:
        return self.on_the_clock == self.my_seat

    def my_next_pick(self) -> int | None:
        """The next pick number belonging to my_seat; None once the draft is over."""
        n = self.n_teams
        for k in range(self.pick_number, n * self.n_rounds + 1):
            if _seat_of(k, n) == self.my_seat:
                return k
        return None

    def roster_of(self, seat: int) -> list[str]:
        return [p.player_id for p in self.picks if p.team_seat == seat]

    @property
    def taken(self) -> set[str]:
        return {p.player_id for p in self.picks}

    def available(self, pool: list[str]) -> list[str]:
        gone = self.taken
        return [p for p in pool if p not in gone]

    @classmethod
    def from_parts(
        cls, my_roster: list[str], opponent_rosters: list[list],
        taken: set, n_teams: int, my_seat: int,
    ) -> DraftState:
        """Build state from the replay harness's view (rosters + taken set).

        Pick numbering is reconstructed only as far as the recommendation math needs:
        pick_number is len(taken)+1, our picks are marked with our seat, opponents get
        a placeholder seat. The full order is not recoverable and not needed — survival
        depends on pick number and seat, not on who was picked when.
        """
        state = cls(league_key="harness", n_teams=n_teams, my_seat=my_seat)
        num = 1
        for pid in my_roster:
            state.picks.append(Pick(number=num, player_id=pid, team_seat=my_seat,
                                    source=PICK_SOURCE_LIVE))
            num += 1
        for roster in opponent_rosters:
            for pid in roster:
                state.picks.append(Pick(number=num, player_id=pid, team_seat=0,
                                        source=PICK_SOURCE_LIVE))
                num += 1
        return state

    def add_pick(self, player_id: str, name: str, source: str) -> Pick:
        """Record the next pick. Seats are derived from pick order, never supplied."""
        if player_id in self.taken:
            raise ValueError(f"player {player_id} is already drafted")
        number = self.pick_number
        pick = Pick(number=number, player_id=player_id, team_seat=_seat_of(number, self.n_teams),
                    source=source, name=name,
                    at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self.picks.append(pick)
        return pick


# --- Yahoo live ingestion -----------------------------------------------------

_PICKER_RE = re.compile(r"^\d+\.\s*")


def fetch_draft_results(league_key: str, access_token: str) -> list[dict]:
    """Yahoo draft_results as a plain list of pick dicts (player_id, team, pick number).

    Raises RuntimeError with an actionable message on the failure modes that matter on
    draft night: 401 (token expired), 403 (app not authorized), anything else non-200.
    """
    import requests

    url = (f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/draftresults"
           f";out=players?format=json")
    resp = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    if resp.status_code == 401:
        raise RuntimeError("401 from Yahoo — token expired; the poller will refresh and retry")
    if resp.status_code == 403:
        raise RuntimeError(
            "403 from Yahoo — the app is not authorized for the Fantasy API "
            "(https://sports.yahoo.com/developer/access/)"
        )
    resp.raise_for_status()
    return parse_draft_results(resp.json())


def _embedded_name(node) -> str:
    """First ``name.full`` anywhere under a parsed node.

    With ``;out=players`` Yahoo embeds the full player node inside each draft_result;
    tolerate nesting changes the same way the pick walk does.
    """
    if isinstance(node, dict):
        nm = node.get("name")
        if isinstance(nm, dict) and nm.get("full"):
            return str(nm["full"])
        for v in node.values():
            got = _embedded_name(v)
            if got:
                return got
    elif isinstance(node, list):
        for v in node:
            got = _embedded_name(v)
            if got:
                return got
    return ""


def parse_draft_results(payload) -> list[dict]:
    """Extract picks from a draft_results payload, tolerant of nesting changes.

    Yahoo represents each pick as ``{"draft_result": {...}}``-ish nodes; the parser walks
    every dict looking for the pick fields so a shape change costs the rows it broke, not
    the whole poll. Missing fields are kept as None rather than dropped — the caller sees
    the gap.
    """
    out: list[dict] = []
    found: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if ("pick" in node and "player_key" in node) or "draft_result" in node:
                found.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    seen: set[int] = set()
    for node in found:
        d = node.get("draft_result", node)
        pkey = str(d.get("player_key") or "")
        pid = pkey.rsplit(".", 1)[-1] if pkey else None
        num = int(d["pick"]) if d.get("pick") is not None else None
        if num is None or num in seen:
            continue
        seen.add(num)
        out.append({
            "pick": num,
            "team_key": d.get("team_key"),
            "player_id": pid,
            "name": _embedded_name(d) or None,
        })
    return out


def poll_draft_results(
    league_key: str, token_path: str | Path = "data/yahoo_access_token.txt",
    pkce_path: str | Path = "data/yahoo_pkce.json",
) -> list[dict]:
    """One poll cycle: fetch draft_results with a self-refreshing token.

    On 401 the token is refreshed once from disk and the request retried; a second 401
    propagates — that is a real failure draft night must see, not hide.
    """
    from fantasy_gm.data.yahoo_fetch import refresh_token_from_files

    token = Path(token_path).read_text().strip()
    try:
        picks = fetch_draft_results(league_key, token)
    except RuntimeError as exc:
        if "401" not in str(exc):
            raise
        token = refresh_token_from_files(token_path, pkce_path)
        picks = fetch_draft_results(league_key, token)
    return picks


# --- manual entry (4.2) --------------------------------------------------------

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    """Fold a display name to a comparison key (accents, punctuation, suffixes removed)."""
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = re.sub(r"[.'’]", "", folded.lower())
    folded = re.sub(r"[^a-z ]", " ", folded)
    parts = [p for p in folded.split() if p and p not in _SUFFIXES]
    return " ".join(parts)


def build_player_directory(store, season: str) -> tuple[dict[str, str], dict[str, str]]:
    """Player id -> display name and normalized key -> ids, over the whole known universe.

    Uses the 2025-26 player logs plus the incoming (no-history) players, so every draftable
    name is resolvable — a rookie who has never played an NBA minute still has to be enterable
    by hand on draft night.
    """
    by_id: dict[str, str] = {}
    for r in store.conn.execute(
        "SELECT DISTINCT player_id, player_name FROM player_logs"
    ):
        by_id[str(r["player_id"])] = r["player_name"]
    for r in store.conn.execute(
        "SELECT player_id, player_name FROM incoming_players"
    ):
        by_id[str(r["player_id"])] = r["player_name"]
    by_key: dict[str, list[str]] = {}
    for pid, name in by_id.items():
        by_key.setdefault(normalize_name(name), []).append(pid)
    return by_id, by_key


def resolve_player(
    query: str, by_id: dict[str, str], by_key: dict[str, str | list]
) -> tuple[str | None, list[str]]:
    """Resolve a typed name to one player id, or report the candidates.

    Returns (player_id, candidates). Exactly one match resolves; several return
    (None, matches) so the operator picks; zero returns (None, []). Matching is
    prefix-aware: a unique prefix ("wembany") resolves.
    """
    q = normalize_name(query)
    if not q:
        return None, []
    if q in by_key:
        ids = by_key[q]
        if len(ids) == 1:
            return ids[0], []
        return None, sorted(ids)
    # prefix match on full names: every name that starts with the typed prefix
    matches = sorted({pid for key, ids in by_key.items() if key.startswith(q) for pid in ids})
    if len(matches) == 1:
        return matches[0], []
    if matches:
        return None, matches[:10]
    # substring fallback for mid-name fragments
    matches = sorted({
        pid for key, ids in by_key.items() if q in key for pid in ids
    })
    if len(matches) == 1:
        return matches[0], []
    return None, matches[:10]


def apply_manual_pick(
    state: DraftState, query: str, directory: tuple[dict[str, str], dict]
) -> tuple[str | None, list[str], str | None]:
    """Record one manual pick, or return what the operator needs to disambiguate.

    Returns (player_id, candidates, error). The state is updated only when player_id is
    not None. Known players resolve to their stored name; unknown ids are still recordable
    (the id goes in verbatim, flagged by an empty name) because a draft cannot be halted
    by our directory's gaps.
    """
    by_id, by_key = directory
    pid, cands = resolve_player(query, by_id, by_key)
    if pid is not None:
        try:
            state.add_pick(pid, by_id.get(pid, ""), PICK_SOURCE_MANUAL)
            return pid, [], None
        except ValueError as exc:
            return None, [], str(exc)
    return None, cands, None


# --- platform reconciliation (4.3) ---------------------------------------------


def reconcile(
    state: DraftState, platform_picks: list[dict], by_id: dict[str, str],
    by_key: dict[str, list[str]] | None = None,
) -> list[str]:
    """Merge platform picks into the state, reporting every discrepancy it finds.

    Yahoo is the authority for pick order and team assignment; manual entries are the
    authority until Yahoo shows the pick. Discrepancies are returned as human-readable
    lines — never silently reconciled, never raised: a discrepancy list is the normal
    mid-draft condition, not an error.

    Id spaces: the store/board use one id space (NBA.com ids); Yahoo draft feeds use
    Yahoo game ids. When ``by_key`` (the player directory's normalized-name index) is
    given, a foreign id is resolved through the pick's name; ambiguity is reported, not
    guessed, and an unresolvable pick is recorded under its platform id with an issue
    line so the clock never stalls. Without ``by_key`` ids pass through unchanged (the
    caller asserts the spaces match). Yahoo mock feeds also pre-fill every remaining
    slot with the drawn draft order and no player — those rows are placeholders, not
    picks, and are skipped without an issue line.
    """
    issues: list[str] = []
    for p in platform_picks:
        number, pid = p.get("pick"), p.get("player_id")
        if number is None:
            issues.append(f"platform pick missing fields: {p}")
            continue
        if pid is None:
            continue  # placeholder slot: draft order pre-drawn, no player yet
        number = int(number)
        pid = str(pid)
        name = p.get("name") or ""
        store_pid = pid if pid in by_id else None
        if store_pid is None and by_key is not None and name:
            cands = by_key.get(normalize_name(name)) or []
            if len(cands) == 1:
                store_pid = cands[0]
            elif len(cands) > 1:
                shown = ", ".join(f"{c} ({by_id.get(c, c)})" for c in cands[:6])
                issues.append(f"pick {number}: \"{name}\" matches several players "
                              f"[{shown}] — resolve manually, pick not recorded")
                continue
        if store_pid is None and by_key is not None:
            issues.append(
                f"pick {number}: {name or 'player'} ({pid}) not in our directory; "
                f"recorded under platform id — add it manually if it should count"
            )
            store_pid = pid
        if store_pid is None:
            store_pid = pid  # no directory given: ids pass through unchanged
        display = by_id.get(store_pid) or name
        existing = {pk.number: pk for pk in state.picks}
        if number in existing:
            if existing[number].player_id != store_pid:
                ours = (existing[number].name
                        or by_id.get(existing[number].player_id, existing[number].player_id))
                issues.append(
                    f"pick {number}: we have {ours}"
                    f" ({existing[number].player_id}, {existing[number].source}), "
                    f"platform has {display or store_pid} ({store_pid})"
                )
            continue
        if number > state.pick_number:
            issues.append(f"platform shows pick {number}, we are at {state.pick_number} — gap?")
            continue
        state.picks.append(Pick(
            number=number, player_id=store_pid, team_seat=_seat_of(number, state.n_teams),
            source=PICK_SOURCE_LIVE, name=display,
        ))
        # keep pick order intact after inserting a retroactive pick
        state.picks.sort(key=lambda pk: pk.number)
    return issues


# --- on-the-clock recommendation (4.4, 4.5) ------------------------------------

MODE_ENGINE = "engine"
MODE_BOARD = "board"


@dataclass
class Candidate:
    """One candidate as presented to the person on the clock."""

    player_id: str
    name: str
    board_rank: int
    total: float                       # G-score total (board units)
    value_over_safe: float             # what taking them now gains vs a safe alternative
    survival: float                    # P(still there at our next pick)
    categories: dict[str, float]       # per-category contribution in basis units
    engine_value: float | None = None  # H0 objective, when the engine fit the clock
    engine_delta: float | None = None


@dataclass
class Recommendation:
    """The full answer to 'who do I take?', including how it was produced."""

    pick_number: int
    on_the_clock: int
    my_seat: int
    mode: str
    elapsed_s: float
    degraded: bool
    note: str
    candidates: list[Candidate] = field(default_factory=list)


def _adp_ranks(adp_order: list[str] | None) -> dict[str, int]:
    return {pid: i for i, pid in enumerate(adp_order)} if adp_order else {}


def _board_candidates(
    board, available: list[str], ranks: dict[str, int], names: dict[str, str],
    picks_until_next: int, top_n: int,
) -> list[Candidate]:
    """The static-board fallback: ranked candidates with survival and opportunity cost.

    ``value_over_safe`` is what taking this player now gains over settling for the best
    player who would likely still be there at our next pick (survival ≥ 75%). Two passes,
    because the safe baseline is by definition *below* the players it is compared against.
    """
    rows = [r for r in board.rows if r.player_id in set(available)]
    rows.sort(key=lambda r: r.rank)
    survivals = [
        survival_probability(
            float(ranks[r.player_id]) if r.player_id in ranks else None, picks_until_next
        )
        for r in rows
    ]
    safe_total = next(
        (r.total for r, s in zip(rows, survivals, strict=True) if s >= 0.75), None
    )
    out: list[Candidate] = []
    for r, surv in zip(rows, survivals, strict=True):
        out.append(Candidate(
            player_id=r.player_id, name=names.get(r.player_id, r.player_id),
            board_rank=r.rank, total=r.total,
            value_over_safe=(r.total - safe_total) if safe_total is not None else 0.0,
            survival=surv, categories=dict(r.categories),
        ))
        if len(out) >= top_n:
            break
    return out


def recommend(
    store,
    season: str,
    state: DraftState,
    pool: list[str],
    *,
    board=None,
    engine=None,
    adp_order: list[str] | None = None,
    names: dict[str, str] | None = None,
    as_of: str | None = None,
    build: str = "balanced",
    budget_s: float = 8.0,
    top_n: int = 8,
) -> Recommendation:
    """Ranked candidates for the current pick: the whole 4.4 surface.

    Two modes. The H0 engine runs first if supplied; if it overruns ``budget_s`` the
    recommendation is marked degraded and the static board answers instead — the clock
    is never exceeded by more than one overrun, and the degrade is reported, not silent
    (4.5). Board mode still carries pick value (G-score vs the best player likely to
    survive to our next pick), category contributions, and survival probability.

    ``board``/``engine`` are built once per draft session by :func:`build_gm` and passed
    in; building them here would spend the clock on work that does not depend on it.
    """
    started = time.perf_counter()
    available = state.available(pool)
    picks_until_next = state.picks_until_my_next()
    ranks = _adp_ranks(adp_order)
    names = names or {}

    note = ""
    candidates: list[Candidate] = []
    mode = MODE_BOARD
    degraded = False
    if engine is not None and available:
        from fantasy_gm.draft.hscore import DraftState as EngineState

        engine_state = EngineState(
            my_roster=state.roster_of(state.my_seat),
            opponent_rosters=[state.roster_of(s) for s in range(1, state.n_teams + 1)
                              if s != state.my_seat],
            taken=set(state.taken),
        )
        try:
            ranked = engine.evaluate_candidates(engine_state, available, top_n=top_n)
        except Exception as exc:  # noqa: BLE001 - a broken engine must not end a draft
            note = f"engine failed ({exc.__class__.__name__}: {exc}); static board below"
            ranked = []
        elapsed_engine = time.perf_counter() - started
        if ranked and elapsed_engine <= budget_s:
            mode = MODE_ENGINE
            by_id = {c.player_id: c for c in ranked}
            board_candidates = _board_candidates(
                board, available, ranks, names, picks_until_next, top_n=top_n
            )
            for c in board_candidates:
                e = by_id.get(c.player_id)
                if e is not None:
                    c.engine_value, c.engine_delta = e.value, e.delta
            candidates = board_candidates
            note = f"H0 engine ranked {len(ranked)} candidates in {elapsed_engine:.1f}s"
        else:
            degraded = True
            note = (
                f"degraded to static board (4.5): H0 engine took {elapsed_engine:.1f}s "
                f"> budget {budget_s:.1f}s"
                if ranked else (note or "engine returned no candidates; static board below")
            )
    if mode == MODE_BOARD:
        candidates = _board_candidates(
            board, available, ranks, names, picks_until_next, top_n=top_n
        )
        if not candidates:
            note = note or "no candidates available — check the pool and ingested picks"

    return Recommendation(
        pick_number=state.pick_number, on_the_clock=state.on_the_clock,
        my_seat=state.my_seat, mode=mode, elapsed_s=time.perf_counter() - started,
        degraded=degraded, note=note, candidates=candidates,
    )


def render_recommendation(rec: Recommendation, *, full_categories: bool = False) -> str:
    """A terminal-sized answer for the person on the clock."""
    lines = []
    clock = rec.on_the_clock
    who = ("YOU ARE ON THE CLOCK" if clock == rec.my_seat
           else f"seat {clock} is on the clock")
    mode = {"engine": "H0 engine + board", "board": "static board"}[rec.mode]
    flag = " — DEGRADED" if rec.degraded else ""
    lines.append(f"Pick {rec.pick_number} ({who}) — {mode}{flag} — {rec.elapsed_s:.1f}s")
    if rec.note:
        lines.append(f"  note: {rec.note}")
    if not rec.candidates:
        lines.append("  no candidates")
        return "\n".join(lines)
    header = f"  {'rk':>3} {'player':<24} {'value':>7} {'vs safe':>8} {'surv':>5}"
    lines.append(header)
    for c in rec.candidates:
        engine = ""
        if c.engine_value is not None:
            engine = f"  H0 {c.engine_value:+.3f} (Δ{c.engine_delta:+.3f})"
        cats = sorted(c.categories.items(), key=lambda kv: -kv[1])[:3]
        cat_str = " ".join(f"{k.split('_')[0]}{v:+.2f}" for k, v in cats)
        lines.append(
            f"  {c.board_rank:>3} {c.name[:24]:<24} {c.total:>7.2f} "
            f"{c.value_over_safe:>+8.2f} {c.survival:>4.0%}  {cat_str}{engine}"
        )
    return "\n".join(lines)


# --- session builder -----------------------------------------------------------


def build_gm(
    store, season: str, as_of: str, *, market_season: str = "2026-27",
    market_source: str = "yahoo", pool_size: int = 156,
    forward_season: str | None = FORWARD_SEASON,
):
    """Everything a draft session needs, built once, before the first pick.

    The board (G-score, availability projected to ``as_of``), the market ordering for
    survival probability, and the player directory for manual entry are all independent
    of the draft clock; the per-pick path in :func:`recommend` only consumes them. The
    H0 engine is deliberately not built: the replay verdict (results.md) measured it
    behind the static board 48/48 cells, so the board is what ships; :func:`recommend`
    accepts an engine when one is ever cleared to run under the clock.

    The board is built on the ``forward_season`` roster (default: config's FORWARD_SEASON):
    players the ranked season cannot place — a season lost to injury, a rookie — enter the
    pool by derived depth and price per-game from their last healthy season (R2/R3).
    """
    from fantasy_gm.draft.board import AvailabilityMode, build_board
    from fantasy_gm.draft.opponents import adp_order_from_market

    board = build_board(store, season, availability=AvailabilityMode.PROJECTED, as_of=as_of,
                        forward_season=forward_season)
    pool = [r.player_id for r in board.rows]
    market = adp_order_from_market(store, market_season, source=market_source,
                                   restrict_to=pool) or []
    directory = build_player_directory(store, market_season)
    names = {r.player_id: r.player_name for r in board.rows}
    return {"board": board, "pool": pool, "adp_order": market,
            "names": names, "directory": directory}


# --- persistence ---------------------------------------------------------------


def save_state(state: DraftState, path: str | Path) -> None:
    """Persist the draft state after every change — draft night must survive a crash."""
    Path(path).write_text(json.dumps({
        "league_key": state.league_key,
        "n_teams": state.n_teams,
        "n_rounds": state.n_rounds,
        "my_seat": state.my_seat,
        "picks": [vars(p) for p in state.picks],
    }, indent=2))


def load_state(path: str | Path) -> DraftState:
    d = json.loads(Path(path).read_text())
    state = DraftState(
        league_key=d["league_key"], n_teams=d["n_teams"], n_rounds=d["n_rounds"],
        my_seat=d["my_seat"],
    )
    state.picks = [Pick(**p) for p in d["picks"]]
    return state
