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
from collections.abc import Callable
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
    adp: int | None = None
    """Yahoo ADP as a 1-based pick number; ``None`` is explicit absence (R5) — the market
    never priced this player, and a zero would read as a real ADP of pick 0."""
    adp_dev: int | None = None
    """ADP minus board rank: negative means the market expects them earlier than the board
    does — the injured-four gap made visible (D4). ``None`` with ``adp``."""
    neg_cats: tuple[tuple[str, float], ...] = ()
    """The categories this candidate most *negatively* contributes to, worst first, at most
    two, only genuinely negative contributions (R5). A player who drags nothing renders none."""


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


def _neg_cats_of(categories: dict[str, float], n: int = 2) -> tuple[tuple[str, float], ...]:
    """The candidate's worst category contributions, worst first — only genuinely negative
    ones (a positive contribution is not a drag, however small)."""
    negs = sorted(((k, v) for k, v in categories.items() if v < 0), key=lambda kv: kv[1])
    return tuple(negs[:n])


# --- the column model (R5/4.2) ---------------------------------------------------
#
# One ordered spec both renderers consume (4.3): the terminal builds its fixed-width row
# from it, and the watch page renders from the same keys and headers (the watcher embeds
# column_spec_json() in state.json; the page's picker persists the chosen keys in
# localStorage and validates them against this spec every render).


@dataclass(frozen=True)
class ColumnSpec:
    """One column of the recommendation card."""

    key: str
    header: str
    cell: Callable[[Candidate], str]
    width: int | None = None   # terminal cell width; None = trailing column, no padding
    align: str = ">"           # terminal alignment for the header and the cell alike


COLUMN_SPECS: tuple[ColumnSpec, ...] = (
    ColumnSpec("rk", "rk", width=4, cell=lambda c: str(c.board_rank)),
    ColumnSpec("player", "player", width=24, align="<", cell=lambda c: c.name[:24]),
    ColumnSpec("value", "value", width=7, cell=lambda c: f"{c.total:.2f}"),
    ColumnSpec("vs_safe", "vs safe", width=8, cell=lambda c: f"{c.value_over_safe:+.2f}"),
    ColumnSpec("surv", "surv", width=5, cell=lambda c: f"{c.survival:.0%}"),
    ColumnSpec("adp", "adp", width=5, cell=lambda c: "—" if c.adp is None else str(c.adp)),
    ColumnSpec("adp_dev", "adp−rk", width=8,
               cell=lambda c: "—" if c.adp_dev is None else f"{c.adp_dev:+d}"),
    ColumnSpec("top_cats", "top cats", align="<",
               cell=lambda c: " ".join(f"{k.split('_')[0]}{v:+.2f}" for k, v
                                       in sorted(c.categories.items(),
                                                 key=lambda kv: -kv[1])[:3])),
    ColumnSpec("neg_cats", "neg cats", align="<",
               cell=lambda c: " ".join(f"{k.split('_')[0]}{v:+.2f}" for k, v in c.neg_cats)),
)
DEFAULT_COLUMNS = ("rk", "player", "value", "vs_safe", "surv", "adp", "top_cats", "neg_cats")

COLUMN_BY_KEY: dict[str, ColumnSpec] = {s.key: s for s in COLUMN_SPECS}


def parse_columns(spec: str | None) -> tuple[str, ...]:
    """The requested column keys, validated. ``None`` is the default set; unknown names are
    reported (4.4) — an unrecognised column is an error, never a silently dropped one, and
    duplicates collapse to their first occurrence."""
    if spec is None:
        return DEFAULT_COLUMNS
    keys = [s.strip() for s in spec.split(",") if s.strip()]
    if not keys:
        raise ValueError("no columns requested")
    known = [s.key for s in COLUMN_SPECS]
    unknown = [k for k in keys if k not in known]
    if unknown:
        raise ValueError(f"unknown column(s): {', '.join(unknown)} (known: {', '.join(known)})")
    return tuple(dict.fromkeys(keys))


def column_spec_json() -> list[dict]:
    """The full picker menu for the page: every column, its header, and the default flag."""
    return [{"key": s.key, "header": s.header, "default": s.key in DEFAULT_COLUMNS}
            for s in COLUMN_SPECS]


def candidate_json(c: Candidate) -> dict:
    """The state.json form of one candidate — additive over the 4.4 schema (R5): ``adp`` /
    ``adp_dev`` stay ``null`` when the market never priced the player, and ``neg_cats`` is
    a list of ``[category, contribution]`` pairs, worst first."""
    return {
        "name": c.name,
        "board_rank": c.board_rank,
        "total": round(c.total, 2),
        "value_over_safe": round(c.value_over_safe, 2),
        "survival": round(c.survival, 3),
        "categories": {k.split("_")[0]: round(v, 2) for k, v in c.categories.items()},
        "engine_value": None if c.engine_value is None else round(c.engine_value, 3),
        "adp": c.adp,
        "adp_dev": c.adp_dev,
        "neg_cats": [[k, round(v, 2)] for k, v in c.neg_cats],
    }


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
        adp_i = ranks.get(r.player_id)
        adp = adp_i + 1 if adp_i is not None else None  # ADP is a 1-based pick number
        out.append(Candidate(
            player_id=r.player_id, name=names.get(r.player_id, r.player_id),
            board_rank=r.rank, total=r.total,
            value_over_safe=(r.total - safe_total) if safe_total is not None else 0.0,
            survival=surv, categories=dict(r.categories),
            adp=adp,
            adp_dev=(adp - r.rank) if adp is not None else None,
            neg_cats=_neg_cats_of(r.categories),
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


def _fmt_cell(text: str, spec: ColumnSpec) -> str:
    """A cell fixed to the spec's width, aligned per the spec. A trailing column
    (``width=None``) is not truncated or padded."""
    if not spec.width:
        return text
    text = text[:spec.width - 1]
    return text.rjust(spec.width - 1) if spec.align == ">" else text.ljust(spec.width - 1)


def render_recommendation(
    rec: Recommendation, columns: tuple[str, ...] = DEFAULT_COLUMNS,
) -> str:
    """A terminal-sized answer for the person on the clock, rendered from the column
    spec (4.3): the same keys and headers the watch page consumes."""
    specs = [COLUMN_BY_KEY[k] for k in columns]
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
    lines.append("  " + " ".join(_fmt_cell(s.header, s) for s in specs))
    for c in rec.candidates:
        suffix = ""
        if c.engine_value is not None:
            suffix = f"  H0 {c.engine_value:+.3f} (\u0394{c.engine_delta:+.3f})"
        cells = " ".join(_fmt_cell(s.cell(c), s) for s in specs).rstrip()
        lines.append("  " + cells + suffix)
    return "\n".join(lines)



# --- session builder -----------------------------------------------------------


def build_gm(
    store, season: str, as_of: str, *, market_season: str = "2026-27",
    market_source: str = "yahoo", pool_size: int = 156,
    forward_season: str | None = FORWARD_SEASON,
    rate_basis: str = "measured",
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

    ``rate_basis`` passes through to the board (R4/D3): ``measured`` — the ranked season's
    rates, the default; ``projected`` — the derived minutes/role model carries usage onto
    the forward roster, labeled with its projection date and unproven-edge caveat on every
    render.
    """
    from fantasy_gm.draft.board import AvailabilityMode, build_board
    from fantasy_gm.draft.opponents import adp_order_from_market

    board = build_board(store, season, availability=AvailabilityMode.PROJECTED, as_of=as_of,
                        forward_season=forward_season, rate_basis=rate_basis)
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
