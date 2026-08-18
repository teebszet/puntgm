"""Score the projection method against a season that already happened (A-DRAFT-5).

This is the gate on the highest-risk assumption in the change: *own-built projections are
good enough*. A superior optimizer fed bad means loses to a commodity ranker fed good means,
so the method has to be measured against the baseline it claims to beat, and the baseline has
to be the strong one — last season's per-game production, carried forward. That is what every
casual drafter already has for free, and it is a much harder target than it sounds.

**The gate: if the model cannot beat naive carry-forward on minutes MAE, it is not ready, and
the honest move is to report that rather than ship it as a model.** The report carries that
verdict as a field.

Two modes, because the store does not yet hold two seasons:

* ``cross-season`` — the real test. Project a completed season from an ``as_of`` before it
  started, using only prior seasons. Needs a prior-season backfill (task 2.10).
* ``split-season`` — the proxy available today. Cut a season part-way, project the rest from
  the games before the cut. It exercises the same machinery against the same baseline, but it
  is **not the same test**: both sides see the same season's team context, the role model has
  no forward depth chart to react to, and the gap the model has to forecast across is months
  rather than an offseason. Read it as a lower bound on difficulty, not as the gate.

Both modes are structurally lookahead-free: the source is fit from
``store.player_game_stream_asof(as_of)``, so nothing after the cut is reachable.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from fantasy_gm.config import DEFAULT_CATEGORIES, PERCENTAGE_CATEGORIES
from fantasy_gm.projections.derived import DerivedProjectionSource
from fantasy_gm.projections.source import projected_stat_keys

CROSS_SEASON = "cross-season"
SPLIT_SEASON = "split-season"


@dataclass(frozen=True)
class ErrorReport:
    """Mean absolute error of one method over the evaluated players."""

    label: str
    minutes_mae: float
    minutes_bias: float                   # signed: positive = over-projected
    games_mae: float
    category_mae: dict[str, float] = field(default_factory=dict)

    def line(self) -> str:
        return f"{self.label:<16} minutes MAE {self.minutes_mae:6.2f}  " \
               f"bias {self.minutes_bias:+6.2f}  games MAE {self.games_mae:6.2f}"


@dataclass(frozen=True)
class BacktestReport:
    mode: str
    season: str
    as_of: str
    eval_start: str
    eval_end: str
    n_players: int
    model: ErrorReport
    naive: ErrorReport
    # Paired model-minus-naive minutes error, per player: the two methods are scored on the
    # same players, so the paired difference is far tighter than comparing two MAEs, and it
    # is the only way to tell a real edge from a rounding difference.
    minutes_win_rate: float = 0.0          # share of players the model is closer on
    minutes_paired_stderr: float = 0.0     # standard error of the mean paired improvement
    notes: dict[str, str] = field(default_factory=dict)

    @property
    def beats_naive_minutes(self) -> bool:
        """The gate from A-DRAFT-5, stated as a property so it cannot be quietly skipped."""
        return self.model.minutes_mae < self.naive.minutes_mae

    @property
    def minutes_edge_sigmas(self) -> float:
        """Paired improvement in standard errors. Below ~2 the edge is inside the noise."""
        gain = self.naive.minutes_mae - self.model.minutes_mae
        return gain / self.minutes_paired_stderr if self.minutes_paired_stderr > 0 else 0.0

    @property
    def minutes_improvement(self) -> float:
        """Fraction of the naive minutes error removed (negative = worse than naive)."""
        if self.naive.minutes_mae <= 0:
            return 0.0
        return (self.naive.minutes_mae - self.model.minutes_mae) / self.naive.minutes_mae

    def categories_beaten(self) -> list[str]:
        return sorted(c for c, e in self.model.category_mae.items()
                      if e < self.naive.category_mae.get(c, float("inf")))

    def verdict(self) -> str:
        head = (f"model minutes MAE {self.model.minutes_mae:.2f} vs naive "
                f"{self.naive.minutes_mae:.2f} ({self.minutes_improvement:+.1%}, "
                f"{self.minutes_edge_sigmas:.1f}σ paired, "
                f"closer on {self.minutes_win_rate:.0%} of players)")
        if not self.beats_naive_minutes:
            return f"FAIL — {head}; do not ship this as a model (A-DRAFT-5)"
        if self.minutes_edge_sigmas < 2.0:
            return (f"INCONCLUSIVE — {head}; the edge is inside the noise, so this is not "
                    f"evidence the model beats carry-forward (A-DRAFT-5)")
        return f"PASS — {head}"


# --- realized truth ----------------------------------------------------------


def _season_bounds(store, season: str) -> tuple[str, str] | None:
    row = store.conn.execute(
        "SELECT MIN(game_date) a, MAX(game_date) b FROM player_logs WHERE season = ?", (season,)
    ).fetchone()
    return (row["a"], row["b"]) if row and row["a"] else None


def _realized(store, season: str, start: str, end: str, keys: list[str]
              ) -> dict[str, dict[str, float]]:
    """Per-player realized per-game means (plus minutes and games) over a window."""
    rows = store.player_game_stream_asof(end, since=start, season=season)
    per: dict[str, list[dict]] = {}
    for r in rows:
        per.setdefault(r["player_id"], []).append(r)
    out: dict[str, dict[str, float]] = {}
    for pid, games in per.items():
        mins = [g["minutes"] for g in games if g["minutes"] is not None]
        rec = {k: statistics.fmean([float(g["stats"].get(k, 0.0)) for g in games]) for k in keys}
        rec["minutes"] = statistics.fmean(mins) if mins else 0.0
        rec["games"] = float(len(games))
        out[pid] = rec
    return out


def _naive_carry_forward(store, as_of: str, keys: list[str], window: int
                         ) -> dict[str, dict[str, float]]:
    """The baseline: each player's own per-game production over the games before ``as_of``.

    This is what a drafter with last season's stat page has, and it is the target the model
    has to beat before it is worth calling a model. Availability carries forward the same
    way — games played over the games their team played — so the games-played comparison is
    like-for-like against the model's shrunk rate.
    """
    from fantasy_gm.projections.minutes import _player_windows

    history = _player_windows(store.player_game_stream_asof(as_of), window)
    out: dict[str, dict[str, float]] = {}
    for pid, games in history.items():
        mins = [g["minutes"] for g in games if g["minutes"] is not None]
        rec = {k: statistics.fmean([float(g["stats"].get(k, 0.0)) for g in games]) for k in keys}
        rec["minutes"] = statistics.fmean(mins) if mins else 0.0
        rec["observed_games"] = float(len(games))
        team_games = store.games_in_window_for_team(
            games[-1]["team"], games[0]["game_date"], as_of
        )
        rec["availability"] = min(len(games) / team_games, 1.0) if team_games else 1.0
        out[pid] = rec
    return out


def _pct(rec: dict[str, float], category: str) -> float | None:
    makes, attempts = PERCENTAGE_CATEGORIES[category]
    a = rec.get(attempts, 0.0)
    return (rec.get(makes, 0.0) / a) if a > 0 else None


def _mae(pairs: list[tuple[float, float]]) -> float:
    return statistics.fmean([abs(a - b) for a, b in pairs]) if pairs else 0.0


# --- the harness -------------------------------------------------------------


def backtest_projection(
    store,
    season: str,
    *,
    as_of: str | None = None,
    mode: str | None = None,
    categories: list[str] | None = None,
    min_train_games: int = 20,
    min_eval_games: int = 20,
    window: int = 82,
    season_games: int = 82,
) -> BacktestReport | None:
    """Backtest the derived projection against realized production for ``season``.

    ``mode`` defaults to ``cross-season`` when the store holds games before ``season`` and
    ``split-season`` otherwise. Returns None if the season is not in the store at all.
    """
    bounds = _season_bounds(store, season)
    if bounds is None:
        return None
    season_start, season_end = bounds
    cats = list(categories or DEFAULT_CATEGORIES)
    keys = projected_stat_keys(cats)

    prior_start = (date.fromisoformat(season_start) - timedelta(days=1)).isoformat()
    has_prior = bool(store.conn.execute(
        "SELECT 1 FROM player_logs WHERE game_date <= ? LIMIT 1", (prior_start,)
    ).fetchone())
    if mode is None:
        mode = CROSS_SEASON if has_prior else SPLIT_SEASON

    notes: dict[str, str] = {}
    if mode == CROSS_SEASON:
        if not has_prior:
            notes["blocked"] = (
                "no games before the season start — cross-season backtest needs a prior-season "
                "backfill (task 2.10)"
            )
            return BacktestReport(mode, season, prior_start, season_start, season_end, 0,
                                  ErrorReport("model", 0, 0, 0), ErrorReport("naive", 0, 0, 0),
                                  notes=notes)
        cut = as_of or prior_start
        eval_start, eval_end = season_start, season_end
    else:
        cut = as_of or _midpoint(season_start, season_end)
        eval_start = (date.fromisoformat(cut) + timedelta(days=1)).isoformat()
        eval_end = season_end
        notes["proxy"] = (
            "split-season proxy, not the A-DRAFT-5 gate: same-season context on both sides, "
            "no forward depth chart for the role model to react to, and a months-long rather "
            "than offseason-long forecast gap"
        )

    truth = _realized(store, season, eval_start, eval_end, keys)
    naive = _naive_carry_forward(store, cut, keys, window)
    source = DerivedProjectionSource(store, categories=cats, window=window,
                                     season_games=season_games)

    evaluated = [
        pid for pid in naive
        if naive[pid]["observed_games"] >= min_train_games
        and truth.get(pid, {}).get("games", 0.0) >= min_eval_games
    ]
    projections = source.project(season, cut, evaluated)

    minutes_pairs: list[tuple[float, float]] = []
    naive_minutes_pairs: list[tuple[float, float]] = []
    games_pairs: list[tuple[float, float]] = []
    naive_games_pairs: list[tuple[float, float]] = []
    cat_pairs: dict[str, list[tuple[float, float]]] = {c: [] for c in cats}
    naive_cat_pairs: dict[str, list[tuple[float, float]]] = {c: [] for c in cats}
    scored: list[str] = []

    eval_games_available = _team_games_in_eval(store, season, eval_start, eval_end)
    for pid in evaluated:
        proj = projections.get(pid)
        mins = source.minutes_projection(season, cut, pid)
        if proj is None or mins is None:
            continue
        scored.append(pid)
        actual, base = truth[pid], naive[pid]
        minutes_pairs.append((mins.minutes, actual["minutes"]))
        naive_minutes_pairs.append((base["minutes"], actual["minutes"]))

        # Games played is scored on the same footing for both: scale the projection from a
        # full season down to the games actually available in the evaluation window.
        scale = eval_games_available / float(season_games) if season_games else 1.0
        games_pairs.append((proj.expected_games * scale, actual["games"]))
        naive_games_pairs.append((base["availability"] * eval_games_available, actual["games"]))

        for c in cats:
            if c in PERCENTAGE_CATEGORIES:
                truth_pct = _pct(actual, c)
                if truth_pct is None:
                    continue
                model_pct = proj.percentage(c)
                base_pct = _pct(base, c)
                if model_pct is not None:
                    cat_pairs[c].append((model_pct, truth_pct))
                if base_pct is not None:
                    naive_cat_pairs[c].append((base_pct, truth_pct))
            else:
                cat_pairs[c].append((proj.estimate(c).per_game_mean, actual[c]))
                naive_cat_pairs[c].append((base[c], actual[c]))

    model = ErrorReport(
        "model", _mae(minutes_pairs), _bias(minutes_pairs), _mae(games_pairs),
        {c: round(_mae(v), 4) for c, v in cat_pairs.items()},
    )
    naive_report = ErrorReport(
        "naive carry-fwd", _mae(naive_minutes_pairs), _bias(naive_minutes_pairs),
        _mae(naive_games_pairs), {c: round(_mae(v), 4) for c, v in naive_cat_pairs.items()},
    )
    notes["eval_games_available"] = str(eval_games_available)
    win_rate, paired_stderr = _paired_minutes(minutes_pairs, naive_minutes_pairs)
    return BacktestReport(mode, season, cut, eval_start, eval_end, len(scored),
                          model, naive_report, win_rate, paired_stderr, notes)


def _paired_minutes(
    model_pairs: list[tuple[float, float]], naive_pairs: list[tuple[float, float]]
) -> tuple[float, float]:
    """Per-player (naive error − model error): win rate and the standard error of its mean."""
    diffs = [abs(nb - truth) - abs(mb - truth)
             for (mb, truth), (nb, _t) in zip(model_pairs, naive_pairs, strict=True)]
    if len(diffs) < 2:
        return 0.0, 0.0
    wins = sum(1 for d in diffs if d > 0) / len(diffs)
    return wins, statistics.stdev(diffs) / (len(diffs) ** 0.5)


def _bias(pairs: list[tuple[float, float]]) -> float:
    return statistics.fmean([a - b for a, b in pairs]) if pairs else 0.0


def _midpoint(start: str, end: str) -> str:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return (a + (b - a) / 2).isoformat()


def _team_games_in_eval(store, season: str, start: str, end: str) -> float:
    """Average games a team plays in the evaluation window — the ceiling on games played."""
    rows = store.conn.execute(
        """SELECT home_team t FROM games WHERE season = ? AND game_date >= ? AND game_date <= ?
           UNION ALL
           SELECT away_team t FROM games WHERE season = ? AND game_date >= ? AND game_date <= ?""",
        (season, start, end, season, start, end),
    ).fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["t"]] = counts.get(r["t"], 0) + 1
    return statistics.fmean(counts.values()) if counts else 0.0
