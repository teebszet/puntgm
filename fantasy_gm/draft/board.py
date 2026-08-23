"""The static G-score draft board — the free, shippable surface.

**Why this exists.** The dynamic H₀ optimizer is the differentiator, and on three seasons of
replay it currently *loses* to the static G-score reduction (see `results.md`). The board is
therefore not a placeholder for H₀; on present evidence it is the better product, and it is the
only draft surface that needs no OAuth, no forward projection, and no working optimizer. It is
what can face traffic inside the mid-September window.

**What it ranks on.** ``g_score_board`` sums the variance-aware basis over the scored
categories. That basis is the whole claim: z-score standardises by player-to-player spread
alone, which is the right unit for a season-long ranking and the wrong one for a category
decided over one week (Rosenof, arXiv 2307.02188). Measured on this repo's replay, G-score
beats z-score by **+5 to +9pp** on category win rate on a forward-honest basis (two seasons,
two seeds). The larger +13.4pp figure in `results.md` is the *realized-availability* arm and
is not reachable by anyone drafting in advance — see the availability note below, and
A-DRAFT-14 for the measurement.

**Punting is declared here, not emergent.** In H₀ concentration falls out of the optimisation
and is never named. A static board cannot do that, so a punt build is exactly what the market's
punt checkboxes are: the punted categories are dropped from the scored set and the board is
re-ranked over what remains. Stated plainly rather than dressed up — the honest claim is "the
same punt checkbox everyone ships, computed in a better metric", and the z-score delta column
is what makes that difference visible.

**Availability is separated from variance, deliberately (A-DRAFT-14).** Measured on 2025-26,
`corr(games played, rank change vs z-score) = +0.627` — most of the board's disagreement with
the market metric was not the variance correction at all, it was that G-score counts weeks the
player missed as zeros and z-score is availability-blind. Counting those zeros is *correct for
replay*, where the season already happened and a missed week really did lose the category. It
is **hindsight for a board published before a draft**: it ranks Giannis 118th because he missed
46 games last season. So the board measures production over active weeks only and reintroduces
availability as a separately projected, shrunk term (:mod:`fantasy_gm.projections.availability`,
the A13 model that is the in-season engine's single biggest win). Two claims, two columns, each
auditable on its own. See :class:`AvailabilityMode`.

**Provenance is part of the artifact.** Every board carries a :attr:`Board.basis` line saying
what it was measured from and how availability was handled. It is not a category-level forward
projection, and publishing it as one would be the exact failure the assumptions ledger exists
to prevent (A-DRAFT-5's gate on the projection backtest is still open). :func:`board_json` and
:func:`render_markdown` both emit that line, so it cannot be dropped by the rendering layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum

from fantasy_gm.config import DEFAULT_CATEGORIES
from fantasy_gm.draft.xscore import (
    DEFAULT_KAPPA,
    CategoryBasis,
    PeriodStats,
    VarianceMode,
    XScoreBasis,
    measure_period_stats,
)
from fantasy_gm.valuation import player_values


class AvailabilityMode(StrEnum):
    """How the board accounts for a player being on the floor.

    * :attr:`REALIZED` — weeks the player missed count as zero-production weeks. This is what
      the replay harness uses and what produced the published +13.4pp result. Correct when
      grading a season that already happened; hindsight when ranking one that has not.
    * :attr:`NEUTRAL` — only weeks the player actually played. Isolates the variance claim from
      the availability claim. Ranks a 30-game star as if durable, which is its own distortion —
      offered as the ablation, not as the product.
    * :attr:`PROJECTED` — neutral production, then availability reintroduced as a *forward*
      beta-binomial projection shrunk toward the pool rate. The default, and the only one of
      the three that is defensible on a board published before a draft.
    """

    REALIZED = "realized"
    NEUTRAL = "neutral"
    PROJECTED = "projected"


# Named builds, chosen for what a 9-cat drafter actually plays rather than for coverage of the
# combinatorial space. `punt_ft` and `punt_tov` are the two the measured findings support most
# directly: ft_pct is the category the in-season engine gates out as non-actionable (A15), and
# both are categories where a strong player is routinely dragged down by one number.
PUNT_BUILDS: dict[str, tuple[str, ...]] = {
    "balanced": (),
    "punt_ft": ("ft_pct",),
    "punt_fg": ("fg_pct",),
    "punt_tov": ("tov",),
    "punt_ast": ("ast",),
    "punt_pts": ("pts",),
    "punt_ft_tov": ("ft_pct", "tov"),
    "punt_fg_ft": ("fg_pct", "ft_pct"),
    "punt_ast_tov": ("ast", "tov"),
}


@dataclass(frozen=True)
class BoardRow:
    """One player's line on a board, with the z-score comparison that makes it interesting."""

    rank: int
    player_id: str
    player_name: str
    total: float
    categories: dict[str, float]
    expected_games: float | None = None
    availability_rate: float | None = None
    z_rank: int | None = None
    z_delta: int | None = None
    """``z_rank - rank``. Positive means G-score rates the player *higher* than z-score does —
    i.e. the market, which runs on z-score, is underrating them. Negative is the reverse. This
    column is the difference between the two metrics made legible, and it is the content."""


@dataclass(frozen=True)
class Board:
    """A complete ranked board for one build, carrying its own provenance."""

    season: str
    build: str
    punt: tuple[str, ...]
    categories: tuple[str, ...]
    pool_size: int
    kappa: float
    variance_mode: str
    availability: AvailabilityMode = AvailabilityMode.PROJECTED
    availability_as_of: str | None = None
    rows: list[BoardRow] = field(default_factory=list)

    @property
    def basis(self) -> str:
        """The provenance line. Published output must carry this verbatim."""
        head = (
            f"Per-week production measured from {self.season} game logs over the top "
            f"{self.pool_size} players by minutes per game."
        )
        if self.availability is AvailabilityMode.REALIZED:
            return (
                f"{head} Weeks missed count as zero-production weeks, so last season's "
                "realized availability is baked into the rank — replay-faithful, but "
                "hindsight for a board published before a draft."
            )
        if self.availability is AvailabilityMode.NEUTRAL:
            return (
                f"{head} Measured over active weeks only: no availability term at all, so a "
                "player who missed half the season is ranked as if durable. Ablation, not a "
                "recommendation."
            )
        return (
            f"{head} Measured over active weeks only, then scaled by expected games played "
            f"projected as of {self.availability_as_of} — a beta-binomial availability rate "
            "shrunk toward the pool, not last season's realized games. Category rates are "
            "measured, not projected forward."
        )

    @property
    def label(self) -> str:
        return "Balanced" if not self.punt else "Punt " + " + ".join(self.punt)


def _player_names(store, season: str, player_ids: list[str]) -> dict[str, str]:
    """Resolve display names in one query rather than one per row."""
    if not player_ids:
        return {}
    marks = ",".join("?" * len(player_ids))
    rows = store.conn.execute(
        f"SELECT player_id, player_name, MAX(game_date) FROM player_logs "  # noqa: S608
        f"WHERE season = ? AND player_id IN ({marks}) GROUP BY player_id",
        (season, *player_ids),
    ).fetchall()
    return {r["player_id"]: r["player_name"] for r in rows}


def project_availability(
    store, season: str, as_of: str, players: list[str] | None = None
) -> dict[str, object]:
    """``{player_id: GamesProjection}`` from history known at ``as_of``.

    Reads through :mod:`fantasy_gm.projections.availability` rather than counting last
    season's games, so the rate is shrunk toward the pool by a fitted prior and a player with
    one unlucky season is not condemned by it.

    ``players`` names everyone the board needs a rate for. Anyone in it with no games before
    ``as_of`` — a rookie, or a returnee the store has not seen — is projected from ``(0, 0)``,
    which the beta-binomial resolves to the fitted **pool rate**. Leaving them out instead
    would silently hand them a rate of 1.0, i.e. rank a player who has never appeared in the
    league as a nailed-on 82-game starter. That is not a conservative default; it put two
    rookies in the top eight of the first board this produced.
    """
    from fantasy_gm.projections.availability import GamesModel, fit_games

    fit = fit_games(store, as_of)
    model = GamesModel(fit)
    per_player: dict[str, list[dict]] = {}
    for row in store.player_game_stream_asof(as_of):
        per_player.setdefault(row["player_id"], []).append(row)

    out: dict[str, object] = {}
    for pid, games in per_player.items():
        team = games[-1]["team"]
        team_games = store.games_in_window_for_team(team, games[0]["game_date"], as_of)
        out[pid] = model.project(pid, len(games), team_games)
    for pid in players or []:
        if pid not in out:
            out[pid] = model.project(pid, 0, 0)
    return out


def _scale_for_availability(
    stats: dict[str, dict[str, PeriodStats]],
    rates: dict[str, float],
    games_per_week: dict[str, float],
) -> dict[str, dict[str, PeriodStats]]:
    """Reintroduce availability into active-week stats as a *binomial* mixture over games.

    Availability applies per game, not per week — players miss individual nights, not whole
    weeks. With ``n`` scheduled games in a week and each played with probability ``r``, the
    games played are Binomial(n, r) and the weekly total is roughly that count times the
    per-game rate ``μ/n``. The law of total variance then gives::

        μ' = r · μ
        τ'² = r · τ² + r(1 − r) · μ² / n

    That ``/n`` matters enormously and is the whole reason this is written out rather than
    assumed. Modelling availability as a Bernoulli coin flip on the *week* (no ``/n``) inflates
    the penalty by a factor of n ≈ 3.5, and because the term scales with μ² it lands almost
    entirely on high-production players — it ranked durable role players above every star, which
    is a modelling artifact and not a finding. Setting ``r = 1`` recovers the stats unchanged.
    """
    scaled: dict[str, dict[str, PeriodStats]] = {}
    for pid, by_cat in stats.items():
        r = rates.get(pid, 1.0)
        n = max(games_per_week.get(pid, 3.5), 1.0)
        scaled[pid] = {
            c: PeriodStats(
                mean=r * ps.mean,
                std=(r * ps.std**2 + r * (1.0 - r) * ps.mean**2 / n) ** 0.5,
                periods=ps.periods,
            )
            for c, ps in by_cat.items()
        }
    return scaled


def _basis(
    store,
    season: str,
    categories: list[str],
    pool_size: int,
    kappa: float,
    mode: VarianceMode,
    availability: AvailabilityMode,
    as_of: str | None,
) -> tuple[XScoreBasis, dict[str, object]]:
    """Build the standardisation basis under one availability treatment."""
    from statistics import fmean, median, pstdev

    include_idle = availability is AvailabilityMode.REALIZED
    stats, pool = measure_period_stats(
        store, season, categories, pool_size, include_idle_weeks=include_idle
    )
    projections: dict[str, object] = {}
    if availability is AvailabilityMode.PROJECTED:
        if not as_of:
            raise ValueError("projected availability needs an --as-of date")
        projections = project_availability(store, season, as_of, players=pool)
        rates = {p: getattr(g, "availability_rate", 1.0) for p, g in projections.items()}
        # Games per *active* week, measured per player rather than assumed: a week's scheduled
        # load varies by team and by point in the calendar, and the variance term divides by it.
        played = {
            r["player_id"]: r["n"]
            for r in store.conn.execute(
                "SELECT player_id, COUNT(*) n FROM player_logs WHERE season = ? "
                "GROUP BY player_id",
                (season,),
            )
        }
        weeks = {
            p: max((next(iter(by_cat.values())).periods if by_cat else 0), 1)
            for p, by_cat in stats.items()
        }
        games_per_week = {p: played.get(p, 0) / weeks[p] for p in stats}
        stats = _scale_for_availability(stats, rates, games_per_week)

    scored_players = [p for p in pool if p in stats]
    bases: dict[str, CategoryBasis] = {}
    for c in categories:
        means = [stats[p][c].mean for p in scored_players]
        taus = [stats[p][c].std for p in scored_players]
        bases[c] = CategoryBasis(
            category=c,
            pool_mean=fmean(means) if means else 0.0,
            pool_std=(pstdev(means) if len(means) > 1 else 0.0) or 1e-9,
            typical_tau=median(taus) if taus else 0.0,
        )
    basis = XScoreBasis(
        categories=categories, bases=bases, stats=stats, pool=scored_players,
        kappa=kappa, mode=mode,
    )
    return basis, projections


def build_board(
    store,
    season: str,
    punt: tuple[str, ...] | list[str] = (),
    build: str | None = None,
    pool_size: int = 156,
    kappa: float = DEFAULT_KAPPA,
    mode: VarianceMode = VarianceMode.MEASURED,
    availability: AvailabilityMode = AvailabilityMode.PROJECTED,
    as_of: str | None = None,
    limit: int | None = None,
    with_zscore: bool = True,
) -> Board:
    """Rank the pool by G-score over the categories left after ``punt``.

    ``punt`` drops categories from the scored set entirely — it does not reweight them — which
    is what a punt build means and what makes the result comparable to the punt checkbox in
    every commercial tool. The z-score comparison, when requested, is computed over the *same*
    reduced category set and the same pool, so the delta isolates the metric and nothing else.
    """
    punt = tuple(punt)
    unknown = [c for c in punt if c not in DEFAULT_CATEGORIES]
    if unknown:
        raise ValueError(f"not 9-cat categories: {unknown} (known: {DEFAULT_CATEGORIES})")
    scored = [c for c in DEFAULT_CATEGORIES if c not in punt]
    if not scored:
        raise ValueError("cannot punt every category")

    basis, projections = _basis(
        store, season, scored, pool_size, kappa, mode, availability, as_of
    )
    ranked = sorted(
        (
            (p, round(basis.total(p), 4),
             {c: round(basis.category_score(p, c), 4) for c in basis.categories})
            for p in basis.pool
        ),
        key=lambda r: -r[1],
    )

    z_rank: dict[str, int] = {}
    if with_zscore:
        # Same pool, same categories, season-long z-score: the market's metric, so the delta
        # measures the metric change alone rather than a difference in who was considered.
        zvals = player_values(store, season, pool_size=pool_size, categories=scored)
        ordered = sorted(zvals.items(), key=lambda kv: (-kv[1], kv[0]))
        z_rank = {pid: i for i, (pid, _) in enumerate(ordered, start=1)}

    ids = [pid for pid, _, _ in ranked]
    names = _player_names(store, season, ids)
    rows = [
        BoardRow(
            rank=i,
            player_id=pid,
            player_name=names.get(pid, pid),
            total=total,
            categories=cats,
            expected_games=(
                round(getattr(projections[pid], "expected_games", 0.0), 1)
                if pid in projections else None
            ),
            availability_rate=(
                round(getattr(projections[pid], "availability_rate", 0.0), 3)
                if pid in projections else None
            ),
            z_rank=z_rank.get(pid),
            z_delta=(z_rank[pid] - i) if pid in z_rank else None,
        )
        for i, (pid, total, cats) in enumerate(ranked, start=1)
    ]
    return Board(
        season=season,
        build=build or _build_name(punt),
        punt=punt,
        categories=tuple(scored),
        pool_size=pool_size,
        kappa=kappa,
        variance_mode=str(mode),
        availability=availability,
        availability_as_of=as_of,
        rows=rows[:limit] if limit else rows,
    )


def _build_name(punt: tuple[str, ...]) -> str:
    for name, cats in PUNT_BUILDS.items():
        if cats == punt:
            return name
    return "punt_" + "_".join(punt) if punt else "balanced"


def all_builds(store, season: str, builds: list[str] | None = None, **kwargs) -> list[Board]:
    """Every named build for one season, for a single export pass."""
    wanted = builds or list(PUNT_BUILDS)
    unknown = [b for b in wanted if b not in PUNT_BUILDS]
    if unknown:
        raise ValueError(f"unknown builds: {unknown} (known: {list(PUNT_BUILDS)})")
    return [build_board(store, season, PUNT_BUILDS[b], build=b, **kwargs) for b in wanted]


def biggest_movers(board: Board, n: int = 10) -> tuple[list[BoardRow], list[BoardRow]]:
    """``(underrated, overrated)`` by z-score delta — the players the two metrics disagree on.

    Restricted to rows that hold a z-rank; a player the z-score pool ranked but the G-score
    pool did not (or vice versa) has no meaningful delta and is left out rather than sorted
    against a null.
    """
    rated = [r for r in board.rows if r.z_delta is not None]
    by_delta = sorted(rated, key=lambda r: (-(r.z_delta or 0), r.rank))
    return by_delta[:n], list(reversed(by_delta[-n:]))


def board_json(board: Board, top: int | None = None) -> dict:
    """Serialisable form. ``basis`` is included deliberately — see the module docstring."""
    rows = board.rows[:top] if top else board.rows
    return {
        "season": board.season,
        "build": board.build,
        "label": board.label,
        "punt": list(board.punt),
        "categories": list(board.categories),
        "pool_size": board.pool_size,
        "kappa": board.kappa,
        "variance_mode": board.variance_mode,
        "availability": str(board.availability),
        "availability_as_of": board.availability_as_of,
        "basis": board.basis,
        "rows": [
            {
                "rank": r.rank,
                "player_id": r.player_id,
                "player_name": r.player_name,
                "g_score": r.total,
                "expected_games": r.expected_games,
                "availability_rate": r.availability_rate,
                "z_rank": r.z_rank,
                "z_delta": r.z_delta,
                "categories": r.categories,
            }
            for r in rows
        ],
    }


def render_table(board: Board, top: int = 30) -> str:
    """Plain-text board for the terminal."""
    lines = [
        f"{board.label} board — {board.season}  "
        f"({len(board.categories)} cats, pool {board.pool_size}, κ={board.kappa})",
        board.basis,
        "",
        f"{'#':>3}  {'player':<26} {'G':>7}  {'vs z':>6}  {'gp':>5}  top categories",
    ]
    for r in board.rows[:top]:
        delta = "—" if r.z_delta is None else f"{r.z_delta:+d}"
        gp = "—" if r.expected_games is None else f"{r.expected_games:.0f}"
        best = sorted(r.categories.items(), key=lambda kv: -kv[1])[:3]
        cats = " ".join(f"{c}{v:+.2f}" for c, v in best)
        lines.append(
            f"{r.rank:>3}  {r.player_name:<26} {r.total:>+7.3f}  {delta:>6}  {gp:>5}  {cats}"
        )
    return "\n".join(lines)


def render_markdown(board: Board, top: int = 150) -> str:
    """Publishable Markdown — the form the eventual page and any thread renders from."""
    lines = [
        f"# {board.label} — {board.season} 9-cat draft board",
        "",
        board.basis,
        "",
        f"Ranked by **G-score** over {len(board.categories)} categories "
        f"(`{'`, `'.join(board.categories)}`), κ={board.kappa}, "
        f"per-player variance `{board.variance_mode}`.",
        "",
        "`vs z` is the player's rank under season-long z-score minus their rank here. "
        "**Positive means z-score underrates them.** `exp GP` is projected games played — "
        "shown as its own column precisely so the availability effect can be read off "
        "separately from the variance effect rather than being conflated with it.",
        "",
        "| # | Player | G-score | vs z | exp GP |"
        + "".join(f" {c} |" for c in board.categories),
        "|--:|---|--:|--:|--:|" + "--:|" * len(board.categories),
    ]
    for r in board.rows[:top]:
        delta = "—" if r.z_delta is None else f"{r.z_delta:+d}"
        gp = "—" if r.expected_games is None else f"{r.expected_games:.0f}"
        cells = "".join(f" {r.categories.get(c, 0.0):+.2f} |" for c in board.categories)
        lines.append(
            f"| {r.rank} | {r.player_name} | {r.total:+.3f} | {delta} | {gp} |{cells}"
        )
    return "\n".join(lines) + "\n"


def export(boards: list[Board], out_dir, top: int | None = None) -> list[str]:
    """Write ``<build>.json`` and ``<build>.md`` per board, plus an ``index.json`` manifest.

    The manifest is what a page reads to discover the builds without hard-coding the list, so
    adding a build to :data:`PUNT_BUILDS` is enough to publish it.
    """
    from pathlib import Path

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for b in boards:
        payload = board_json(b, top=top)
        (out / f"{b.build}.json").write_text(json.dumps(payload, indent=2) + "\n")
        (out / f"{b.build}.md").write_text(render_markdown(b, top=top or 150))
        written += [str(out / f"{b.build}.json"), str(out / f"{b.build}.md")]
    manifest = {
        "season": boards[0].season if boards else None,
        "basis": boards[0].basis if boards else None,
        "builds": [
            {"build": b.build, "label": b.label, "punt": list(b.punt), "players": len(b.rows)}
            for b in boards
        ],
    }
    (out / "index.json").write_text(json.dumps(manifest, indent=2) + "\n")
    written.append(str(out / "index.json"))
    return written
