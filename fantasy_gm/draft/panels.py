"""Per-pick display panels: the category differential (R6) and the punt panel (R7/R8).

Both answer the question the person on the clock is actually asking — *given the draft as
it stands, how does my roster look against the eleven others?* — and both are DISPLAY by
spec: neither feeds candidate ordering. The static board's ranking stands (R6), and the
engine hook in :func:`fantasy_gm.draft.live.recommend` stays unused until one is cleared.
The optimiser that measured behind the board in replay (48/48) is deliberately not invoked
here — the differential reuses the 3.1 model's *inputs* (per-roster weekly category
profiles) without its objective.

Shared inputs: the :class:`~fantasy_gm.draft.live.DraftState` and the session's
:class:`~fantasy_gm.draft.xscore.XScoreBasis` (built once by ``build_gm``; ``build_board``'s
``capture`` hands the fitted basis out so no per-pick work re-measures the store). Re-ranking
a punt build is then a sum of ``category_score`` over that build's scored categories — which
is exactly what ``build_board(punt=...)`` ranks on: standardisation is per-category over the
same pool, so dropping categories from the scored set changes neither the pool nor the other
categories' constants. That equivalence is pinned by test, not asserted.

**Differential (R6/D5).** Our roster's projected weekly profile against the field's average
roster and the field's strongest roster — drafted players only, recomputed per pick. This is
the honest version of "which categories do I need to win": a display the manager reads.

**Punt panel (R7/R8/D6).** The remaining pool re-ranked under every named build
(:data:`~fantasy_gm.draft.board.PUNT_BUILDS`) plus a manager-specified category set, each
with top candidates and an outlook on the roster-so-far under that build's scored categories.
All builds compute within ``budget_s``; beyond it the panel degrades to the last complete
computation, shown with the pick it was computed at and marked stale (the 4.5 degrade
pattern). The cache is keyed by (pick number, custom set) and invalidated on the next pick
or a changed custom set.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

from fantasy_gm.config import CATEGORY_DIRECTION
from fantasy_gm.draft.board import PUNT_BUILDS
from fantasy_gm.draft.live import DraftState
from fantasy_gm.draft.opponents import survival_probability

# Terminal display labels; the watch page keeps its own map (a static page cannot import
# this module, and the two renderers must each degrade gracefully on their own).
CAT_LABEL: dict[str, str] = {
    "pts": "PTS", "reb": "REB", "ast": "AST", "stl": "STL", "blk": "BLK",
    "fg3m": "3PM", "fg_pct": "FG%", "ft_pct": "FT%", "tov": "TOV",
}


def cat_label(cat: str) -> str:
    return CAT_LABEL.get(cat, cat)


# --- category names a manager types (R7: "e.g. ft%,pts") -------------------------

_CAT_ALIASES: dict[str, str] = {
    "pts": "pts", "pt": "pts", "points": "pts",
    "reb": "reb", "rebound": "reb", "rebounds": "reb", "board": "reb", "boards": "reb",
    "ast": "ast", "assist": "ast", "assists": "ast",
    "stl": "stl", "steal": "stl", "steals": "stl",
    "blk": "blk", "block": "blk", "blocks": "blk",
    "fg3m": "fg3m", "3pm": "fg3m", "3pt": "fg3m", "3ptm": "fg3m", "threes": "fg3m",
    "fg_pct": "fg_pct", "fg": "fg_pct", "fgpct": "fg_pct", "fg%": "fg_pct",
    "ft_pct": "ft_pct", "ft": "ft_pct", "ftpct": "ft_pct", "ft%": "ft_pct",
    "tov": "tov", "to": "tov", "turnover": "tov", "turnovers": "tov",
}


def parse_punt(spec: str) -> tuple[str, ...]:
    """The categories to punt, from whatever a manager types: ``ft%,pts`` and
    ``ft_pct,tov`` are the same set. Unknown names are an error naming the known set (the
    4.4 pattern — an unrecognised category is never silently dropped), duplicates collapse
    to their first occurrence, and punting every category is an error (build_board's rule)."""
    from fantasy_gm.config import DEFAULT_CATEGORIES

    tokens = [t.strip() for t in spec.replace(";", ",").split(",") if t.strip()]
    if not tokens:
        raise ValueError("no categories requested")
    out: list[str] = []
    unknown: list[str] = []
    for tok in tokens:
        cat = _CAT_ALIASES.get(tok.lower())
        if cat is None:
            unknown.append(tok)
            continue
        if cat not in out:
            out.append(cat)
    if unknown:
        raise ValueError(
            f"unknown category(ies): {', '.join(unknown)} (known: "
            f"{', '.join(CAT_LABEL.get(c, c) for c in DEFAULT_CATEGORIES)})"
        )
    if len(out) >= len(DEFAULT_CATEGORIES):
        raise ValueError("cannot punt every category")
    return tuple(out)


# --- weekly profiles (the 3.1 model's inputs, without its objective) --------------


def roster_profile(players: list[str], basis) -> tuple[dict[str, float], int]:
    """Summed weekly mean per category over one roster, from the basis's compounded stats.

    ``basis.stats`` holds *weekly* PeriodStats (per-game means already compounded by the
    availability rate), so the sum is the roster's projected weekly total. A rostered player
    the basis cannot price contributes nothing — and is COUNTED in the returned second
    element, because a silent zero would flatter exactly the players the pool cannot see.

    Percentage categories arrive in impact form ``(rate − league%) × attempts`` (weekly
    volume-weighted impact, A8), so a higher value is always better and the raw sums compare
    directly across rosters.
    """
    mean: dict[str, float] = dict.fromkeys(basis.categories, 0.0)
    unpriced = 0
    for pid in players:
        stats = basis.stats.get(pid)
        if not stats:
            unpriced += 1
            continue
        for c in basis.categories:
            ps = stats.get(c)
            if ps is not None:
                mean[c] += ps.mean
    return mean, unpriced


def _standing(our: float, avg: float, cat: str) -> str:
    """ahead / behind / even, in category terms: ``tov`` lower is better (direction −1);
    percentage cats are stored as impact, where higher is already better (+1)."""
    d = (our - avg) * CATEGORY_DIRECTION.get(cat, 1)
    if d > 1e-9:
        return "ahead"
    if d < -1e-9:
        return "behind"
    return "even"


# --- the category differential (R6/D5) --------------------------------------------


@dataclass
class Differential:
    """The roster's weekly category standing against the field, from the current draft state."""

    pick_number: int
    my_seat: int
    our: dict[str, float]
    field_avg: dict[str, float]
    field_strong: dict[str, float]
    strongest_seat: int | None
    unpriced_rostered: int
    """Rostered players the basis cannot price — they scored zero above, so the display says
    so rather than flattering the roster with their absence."""


def compute_differential(state: DraftState, basis) -> Differential:
    """Our roster vs the field's average and strongest rosters, per category.

    The field is the other ``n_teams − 1`` teams (a category league is won against specific
    teams, never against your own roster). "Strongest" is the opposing roster with the highest
    summed balanced G-score — the same convention the engine's ``STRONGEST`` opponent model
    uses, so the display and any future engine agree on what strongest means.
    """
    cats = basis.categories
    our, my_unpriced = roster_profile(state.roster_of(state.my_seat), basis)
    opponents: dict[int, list[str]] = {
        s: state.roster_of(s) for s in range(1, state.n_teams + 1) if s != state.my_seat
    }
    profiles: dict[int, dict[str, float]] = {}
    unpriced = my_unpriced
    totals: dict[int, float] = {}
    for seat, roster in opponents.items():
        prof, n_unpriced = roster_profile(roster, basis)
        profiles[seat] = prof
        unpriced += n_unpriced
        totals[seat] = sum(basis.total(p) for p in roster if p in basis.stats)
    strongest_seat = max(totals, key=lambda s: (totals[s], -s)) if totals else None
    if totals and all(v == 0.0 for v in totals.values()):
        strongest_seat = None  # no opposing roster has a priced player yet
    n = len(profiles) or 1
    field_avg = {c: sum(p[c] for p in profiles.values()) / n for c in cats}
    field_strong = dict(profiles.get(strongest_seat, dict.fromkeys(cats, 0.0)))
    return Differential(
        pick_number=state.pick_number, my_seat=state.my_seat, our=our,
        field_avg=field_avg, field_strong=field_strong, strongest_seat=strongest_seat,
        unpriced_rostered=unpriced,
    )


def differential_json(d: Differential) -> dict:
    """The state.json form — additive over the 4.4 schema; the page draws bars from it."""
    return {
        "pick_number": d.pick_number,
        "my_seat": d.my_seat,
        "strongest_seat": d.strongest_seat,
        "unpriced_rostered": d.unpriced_rostered,
        "categories": [
            {
                "cat": c,
                "label": cat_label(c),
                "our": round(d.our.get(c, 0.0), 2),
                "avg": round(d.field_avg.get(c, 0.0), 2),
                "strong": round(d.field_strong.get(c, 0.0), 2),
                "standing": _standing(d.our.get(c, 0.0), d.field_avg.get(c, 0.0), c),
            }
            for c in d.our
        ],
    }


def render_differential(d: Differential) -> str:
    """Terminal block. Headed 'context' — the spec pins this view display-only (R6)."""
    lines = [
        f"Category differential — pick {d.pick_number} (context, display only — "
        "never a ranking input)"
    ]
    if d.unpriced_rostered:
        lines.append(f"  note: {d.unpriced_rostered} rostered player(s) unpriced — "
                     "they count as zero here")
    lines.append(f"  {'cat':<5} {'ours':>7} {'avg':>7} {'strong':>7}  vs avg")
    for c in d.our:
        st = _standing(d.our.get(c, 0.0), d.field_avg.get(c, 0.0), c)
        mark = {"ahead": "ahead", "behind": "behind", "even": "even"}[st]
        lines.append(
            f"  {cat_label(c):<5} {d.our.get(c, 0.0):>7.1f} "
            f"{d.field_avg.get(c, 0.0):>7.1f} {d.field_strong.get(c, 0.0):>7.1f}  {mark}"
        )
    if d.strongest_seat is not None:
        lines.append(f"  strongest opposing roster: seat {d.strongest_seat}")
    return "\n".join(lines)


# --- the punt panel (R7/R8/D6) ----------------------------------------------------


@dataclass
class PuntCandidate:
    """One candidate as a punt build ranks them (over the remaining pool)."""

    player_id: str
    name: str
    rank: int
    total: float
    adp: int | None
    survival: float


@dataclass
class PuntBuildView:
    """One build's answer: its top candidates and the roster-so-far outlook under its cats."""

    build: str
    label: str
    punt: tuple[str, ...]
    scored: tuple[str, ...]
    rows: list[PuntCandidate] = field(default_factory=list)
    outlook_up: int = 0
    outlook_down: int = 0
    outlook_even: int = 0
    elapsed_s: float = 0.0


@dataclass
class PuntPanel:
    """The full panel: every build's view, or the last complete one marked stale (D6)."""

    pick_number: int
    """The pick this computation is faithful to (stale panels keep the OLD pick number)."""
    current_pick: int
    builds: list[PuntBuildView] = field(default_factory=list)
    elapsed_s: float = 0.0
    complete: bool = True
    stale: bool = False
    note: str = ""


def compute_punt_panel(
    state: DraftState,
    basis,
    adp_order: list[str] | None,
    names: dict[str, str],
    *,
    custom: tuple[str, ...] = (),
    budget_s: float = 8.0,
    top_n: int = 6,
) -> PuntPanel:
    """Rank the remaining pool under every named build + the custom set, within ``budget_s``.

    Each build re-ranks ``state.available(basis.pool)`` by G-score over its scored
    categories — the same sums ``build_board(punt=...)`` ranks on, minus everything the
    clock cannot afford to re-measure per pick (the pool and the standardisation are pick-
    independent, so they are computed once per session instead). Drafted players are
    excluded by construction and survival is projected to our next pick, so the ranking is
    conditioned on the roster already drafted (R7).

    Budget (R8/D6): builds compute in sequence; when the budget runs out the computation is
    marked incomplete — the caller (:class:`PuntPanelCache`) then shows the last complete
    panel marked stale, never a partial set dressed as current.
    """
    started = time.perf_counter()
    cats = basis.categories
    available = state.available(basis.pool)
    ranks = {pid: i for i, pid in enumerate(adp_order)} if adp_order else {}
    picks_until_next = state.picks_until_my_next()

    def build_view(build: str, punt: tuple[str, ...]) -> PuntBuildView:
        t0 = time.perf_counter()
        scored = tuple(c for c in cats if c not in punt)
        totals = sorted(
            ((sum(basis.category_score(p, c) for c in scored), p) for p in available),
            key=lambda t: (-t[0], t[1]),
        )
        rows = []
        for i, (total, pid) in enumerate(totals[:top_n], start=1):
            adp = ranks[pid] + 1 if pid in ranks else None
            rows.append(PuntCandidate(
                player_id=pid, name=names.get(pid, pid), rank=i, total=round(total, 4),
                adp=adp, survival=survival_probability(ranks.get(pid), picks_until_next),
            ))
        # The outlook reads the roster-so-far under this build's scored categories — the
        # same profile primitive the differential uses, restricted to what this build plays.
        our, _ = roster_profile(state.roster_of(state.my_seat), basis)
        opps = [state.roster_of(s) for s in range(1, state.n_teams + 1)
                if s != state.my_seat]
        n_opp = len(opps) or 1
        up = down = even = 0
        for c in scored:
            avg = sum(roster_profile(r, basis)[0][c] for r in opps) / n_opp
            st = _standing(our.get(c, 0.0), avg, c)
            up += st == "ahead"
            down += st == "behind"
            even += st == "even"
        label = "balanced" if not punt else "Punt " + " + ".join(cat_label(c) for c in punt)
        return PuntBuildView(
            build=build, label=label, punt=punt, scored=scored, rows=rows,
            outlook_up=up, outlook_down=down, outlook_even=even,
            elapsed_s=time.perf_counter() - t0,
        )

    builds: list[PuntBuildView] = []
    wanted: list[tuple[str, tuple[str, ...]]] = list(PUNT_BUILDS.items())
    if custom:
        wanted.append(("custom", custom))
    complete = True
    for build, punt in wanted:
        if time.perf_counter() - started >= budget_s:
            complete = False
            break
        builds.append(build_view(build, punt))
    elapsed = time.perf_counter() - started
    note = "" if complete else (
        f"budget {budget_s:.1f}s exceeded after {len(builds)}/{len(wanted)} builds — "
        "showing the last complete set instead"
    )
    return PuntPanel(
        pick_number=state.pick_number, current_pick=state.pick_number, builds=builds,
        elapsed_s=elapsed, complete=complete, note=note,
    )


class PuntPanelCache:
    """The last complete panel per (pick, custom set); invalidated on the next pick (D6).

    ``get`` recomputes when the pick moved or the custom set changed; when a recompute
    overruns the budget, the last complete panel is returned with ``stale=True`` and its
    own pick number — the panel never costs the clock more than one overrun, and what it
    shows is always labelled with how old it is.
    """

    def __init__(self) -> None:
        self._key: tuple[int, tuple[str, ...]] | None = None
        self._panel: PuntPanel | None = None

    def get(
        self, state: DraftState, basis, adp_order: list[str] | None, names: dict[str, str],
        *, custom: tuple[str, ...] = (), budget_s: float = 8.0, top_n: int = 6,
    ) -> PuntPanel:
        key = (state.pick_number, custom)
        if self._panel is not None and self._panel.complete and key == self._key:
            return self._panel
        panel = compute_punt_panel(state, basis, adp_order, names, custom=custom,
                                   budget_s=budget_s, top_n=top_n)
        if panel.complete:
            self._panel, self._key = panel, key
            return panel
        if self._panel is not None and self._panel.complete:
            return replace(
                self._panel, stale=True, current_pick=state.pick_number,
                note=f"as of pick {self._panel.pick_number} — {panel.note}",
            )
        return panel


def punt_panel_json(p: PuntPanel) -> dict:
    """The state.json form — additive; the page renders tabs from ``builds``."""
    return {
        "pick_number": p.pick_number,
        "current_pick": p.current_pick,
        "stale": p.stale,
        "complete": p.complete,
        "note": p.note,
        "elapsed_s": round(p.elapsed_s, 3),
        "builds": [
            {
                "build": b.build,
                "label": b.label,
                "punt": list(b.punt),
                "scored": list(b.scored),
                "outlook": {"up": b.outlook_up, "down": b.outlook_down, "even": b.outlook_even},
                "rows": [
                    {
                        "player_id": c.player_id,
                        "name": c.name,
                        "rank": c.rank,
                        "total": c.total,
                        "adp": c.adp,
                        "survival": round(c.survival, 3),
                    }
                    for c in b.rows
                ],
            }
            for b in p.builds
        ],
    }


def render_punt_panel(p: PuntPanel, detail: str | None = None) -> str:
    """Terminal form. Summary: one line per build. ``detail`` names one build (or 'custom')
    for its full table."""
    head = (
        f"Punt panel — computed at pick {p.pick_number} in {p.elapsed_s:.2f}s "
        "(context, display only — never a ranking input)"
    )
    if p.stale:
        head += f" — STALE (now pick {p.current_pick})"
    lines = [head]
    if p.note:
        lines.append(f"  note: {p.note}")
    if not p.builds:
        lines.append("  no complete computation yet — widen the budget or retry")
        return "\n".join(lines)

    def outlook(b: PuntBuildView) -> str:
        counts = (f"ahead {b.outlook_up} · behind {b.outlook_down} · even {b.outlook_even}"
                  if b.scored else "—")
        return f"roster-so-far: {counts}"

    if detail is None:
        for b in p.builds:
            top = ", ".join(c.name for c in b.rows[:3]) or "—"
            lines.append(f"  {b.build:<12} {top:<46} {outlook(b)}")
        return "\n".join(lines)

    b = next((x for x in p.builds if x.build == detail), None)
    if b is None:
        lines.append(f"  no build named {detail!r} in the last complete set")
        return "\n".join(lines)
    lines.append(f"  {b.label} — scored cats: {', '.join(b.scored)}")
    lines.append(f"    {'rk':>3} {'player':<24} {'value':>7} {'adp':>5} {'surv':>5}")
    for c in b.rows:
        adp = "—" if c.adp is None else str(c.adp)
        lines.append(
            f"    {c.rank:>3} {c.name[:24]:<24} {c.total:>7.2f} {adp:>5} {c.survival:>5.0%}"
        )
    lines.append(f"    {outlook(b)}")
    return "\n".join(lines)
