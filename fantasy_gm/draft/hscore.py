"""H₀ — dynamic, roster-conditional pick valuation (design D1/D3).

A static ranking list answers "who is the best player available?". That is the wrong question
once you have drafted anybody, because the value of a category depends entirely on whether you
are already winning it. H₀ instead re-solves at every pick: for each candidate it projects the
nine category differentials against a representative opponent, converts them to win
probabilities, and scores the objective.

**The five groups.** At pick ``K+1`` of ``N``, each category's differential aggregates:

1. the deciding team's ``K`` already-drafted players — known
2. the candidate under evaluation — known
3. the deciding team's ``N−K−1`` future picks — *unknown*, and shaped by strategy
4. the opponent's known players — known
5. the opponent's future picks — unknown

Groups 3 and 5 carry two kinds of variance: week-to-week noise (τ²) and uncertainty about
*which players* those picks turn out to be (the pool's player-to-player spread σ²). The second
term is why early picks behave differently from late ones — with ten rounds left, almost
anything is still reachable, so no category is settled.

**Strategy weights.** Group 3 is not a fixed quantity: which players you take later depends on
what you are trying to win. H₀ carries a weight vector ``j`` over categories and models future
picks as a softmax-weighted draw from the available pool under ``j``. Optimising ``j`` jointly
with the candidate is what produces category concentration — **punting is never declared, it is
what the optimizer does** when conceding a category maximises the objective.

The optimisation is non-convex, so this finds a local optimum, warm-started from the previous
round (A-DRAFT-9 tracks the size of the gap). Gradients are numeric: the objective runs a
Poisson-binomial DP and a softmax over the pool, and a finite-difference gradient over ~9
dimensions is cheap enough while keeping the code readable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from fantasy_gm.draft.objective import category_probabilities, score_objective
from fantasy_gm.draft.settings import DraftSettings
from fantasy_gm.draft.xscore import XScoreBasis


@dataclass(frozen=True)
class Candidate:
    """One evaluated pick."""

    player_id: str
    value: float                      # objective attained
    delta: float                      # improvement over standing pat
    win_probs: dict[str, float]       # per-category P(win) if this pick is made
    weights: dict[str, float]         # strategy weights that achieved it
    survival: float | None = None     # P(available at our next pick), if an ADP model was given


@dataclass
class DraftState:
    """Who has been taken, by whom, and whose turn it is."""

    my_roster: list[str] = field(default_factory=list)
    opponent_rosters: list[list[str]] = field(default_factory=list)
    taken: set[str] = field(default_factory=set)

    def drafted(self) -> set[str]:
        out = set(self.taken) | set(self.my_roster)
        for r in self.opponent_rosters:
            out |= set(r)
        return out


class HScoreEngine:
    """Roster-conditional pick valuation over an :class:`XScoreBasis`.

    ``basis`` supplies each player's per-period mean and spread per category. Any source of
    those works — measured actuals for replay, or a forward projection for a live draft — which
    is what lets the optimizer be validated before a projection model exists (design D11).
    """

    def __init__(
        self,
        basis: XScoreBasis,
        settings: DraftSettings | None = None,
        *,
        softmax_temp: float = 2.0,
        steps: int = 24,
        lr: float = 0.15,
        tie_margin: float = 0.0,
    ):
        self.basis = basis
        self.settings = settings or DraftSettings()
        self.softmax_temp = softmax_temp
        self.steps = steps
        self.lr = lr
        self.tie_margin = tie_margin
        self._warm: list[float] | None = None

    # --- roster aggregation --------------------------------------------------

    def _totals(self, players: list[str]) -> tuple[dict[str, float], dict[str, float]]:
        """Summed per-period mean and variance for a set of players, per category."""
        mean = {c: 0.0 for c in self.settings.categories}
        var = {c: 0.0 for c in self.settings.categories}
        for pid in players:
            stats = self.basis.stats.get(pid)
            if not stats:
                continue
            for c in self.settings.categories:
                ps = stats.get(c)
                if ps is None:
                    continue
                mean[c] += ps.mean
                var[c] += ps.std**2
        return mean, var

    def _pool_profile(self, available: list[str]) -> tuple[dict[str, float], dict[str, float]]:
        """Mean and total variance of one *unknown* pick drawn from the available pool.

        Variance has both parts: not knowing which player you get (player-to-player spread of
        the means) and that player's own week-to-week noise (mean τ²). Omitting either would
        make an undrafted future look more certain than it is.
        """
        cats = self.settings.categories
        mean = {c: 0.0 for c in cats}
        var = {c: 0.0 for c in cats}
        means: dict[str, list[float]] = {c: [] for c in cats}
        taus: dict[str, list[float]] = {c: [] for c in cats}
        for pid in available:
            stats = self.basis.stats.get(pid)
            if not stats:
                continue
            for c in cats:
                ps = stats.get(c)
                means[c].append(ps.mean if ps else 0.0)
                taus[c].append(ps.std**2 if ps else 0.0)
        for c in cats:
            vals = means[c]
            if not vals:
                continue
            m = sum(vals) / len(vals)
            mean[c] = m
            spread = sum((v - m) ** 2 for v in vals) / len(vals)
            noise = sum(taus[c]) / len(taus[c])
            var[c] = spread + noise
        return mean, var

    def _weighted_future(
        self, available: list[str], weights: list[float]
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Expected profile of ONE future pick under strategy ``weights``.

        Softmax over the strategy-weighted standardised score, so raising a category's weight
        genuinely shifts which players you expect to end up with — and does so differentiably,
        which is what makes gradient descent on the weights meaningful.
        """
        cats = self.settings.categories
        if not available:
            return ({c: 0.0 for c in cats}, {c: 0.0 for c in cats})

        scores = []
        for pid in available:
            s = 0.0
            for w, c in zip(weights, cats, strict=True):
                s += w * self.basis.category_score(pid, c)
            scores.append(s)
        top = max(scores)
        exps = [math.exp(self.softmax_temp * (s - top)) for s in scores]
        z = sum(exps) or 1.0
        probs = [e / z for e in exps]

        mean = {c: 0.0 for c in cats}
        for pid, w in zip(available, probs, strict=True):
            stats = self.basis.stats.get(pid)
            if not stats:
                continue
            for c in cats:
                ps = stats.get(c)
                if ps:
                    mean[c] += w * ps.mean
        # Residual uncertainty about which player this pick lands on.
        var = {c: 0.0 for c in cats}
        for pid, w in zip(available, probs, strict=True):
            stats = self.basis.stats.get(pid)
            if not stats:
                continue
            for c in cats:
                ps = stats.get(c)
                m = ps.mean if ps else 0.0
                var[c] += w * (m - mean[c]) ** 2
                if ps:
                    var[c] += w * ps.std**2
        return mean, var

    # --- the objective -------------------------------------------------------

    def _evaluate(
        self,
        my_players: list[str],
        opp_players: list[str],
        available: list[str],
        my_future: int,
        opp_future: int,
        weights: list[float],
    ) -> tuple[float, list[float]]:
        cats = self.settings.categories
        my_mean, my_var = self._totals(my_players)
        op_mean, op_var = self._totals(opp_players)

        if my_future > 0:
            fm, fv = self._weighted_future(available, weights)
            for c in cats:
                my_mean[c] += my_future * fm[c]
                my_var[c] += my_future * fv[c]
        if opp_future > 0:
            # Opponents draft *competently but not adaptively*: best-available under neutral
            # category weights, via the same softmax machinery. Modelling them as the pool
            # average instead would hand us a free edge for every remaining round — the
            # optimizer would then believe that simply having picks left is an advantage,
            # which inflates early-draft confidence. The edge H₀ should claim is that it
            # *shapes* its picks, not that its opponents draft at random.
            om, ov = self._weighted_future(available, [1.0] * len(cats))
            for c in cats:
                op_mean[c] += opp_future * om[c]
                op_var[c] += opp_future * ov[c]

        mean_diffs = {c: my_mean[c] - op_mean[c] for c in cats}
        var_diffs = {c: max(my_var[c] + op_var[c], 1e-12) for c in cats}
        probs = category_probabilities(mean_diffs, var_diffs, self.settings, self.tie_margin)
        return score_objective(probs, self.settings), probs

    def _optimise_weights(
        self,
        my_players: list[str],
        opp_players: list[str],
        available: list[str],
        my_future: int,
        opp_future: int,
        start: list[float],
    ) -> tuple[float, list[float], list[float]]:
        """Adam on the strategy weights, numeric gradient."""
        n = len(self.settings.categories)
        w = list(start)
        m = [0.0] * n
        v = [0.0] * n
        b1, b2, eps, h = 0.9, 0.999, 1e-8, 1e-3

        best_val, best_probs = self._evaluate(
            my_players, opp_players, available, my_future, opp_future, w
        )
        best_w = list(w)

        if my_future <= 0 or not available:
            return best_val, best_probs, best_w

        for t in range(1, self.steps + 1):
            grad = []
            for i in range(n):
                up, dn = list(w), list(w)
                up[i] += h
                dn[i] -= h
                gu, _ = self._evaluate(
                    my_players, opp_players, available, my_future, opp_future, up
                )
                gd, _ = self._evaluate(
                    my_players, opp_players, available, my_future, opp_future, dn
                )
                grad.append((gu - gd) / (2 * h))
            for i in range(n):
                m[i] = b1 * m[i] + (1 - b1) * grad[i]
                v[i] = b2 * v[i] + (1 - b2) * grad[i] ** 2
                mhat = m[i] / (1 - b1**t)
                vhat = v[i] / (1 - b2**t)
                w[i] += self.lr * mhat / (math.sqrt(vhat) + eps)   # ascent
            # keep weights bounded so the softmax cannot saturate into a single player
            w = [max(-4.0, min(4.0, x)) for x in w]
            val, probs = self._evaluate(
                my_players, opp_players, available, my_future, opp_future, w
            )
            if val > best_val:
                best_val, best_probs, best_w = val, probs, list(w)
        return best_val, best_probs, best_w

    # --- public API ----------------------------------------------------------

    def evaluate_candidates(
        self,
        state: DraftState,
        available: list[str] | None = None,
        top_n: int = 40,
        shortlist: int = 40,
    ) -> list[Candidate]:
        """Rank available players for the deciding team's current pick.

        ``shortlist`` bounds how many players get the full optimisation — candidates are
        pre-filtered by static G-score, because evaluating 400 players under gradient descent
        is not compatible with a draft clock and the pick is never outside the top few dozen.
        """
        cats = self.settings.categories
        drafted = state.drafted()
        pool = available if available is not None else [
            p for p in self.basis.pool if p not in drafted
        ]
        pool = [p for p in pool if p not in drafted]
        if not pool:
            return []

        pool = sorted(pool, key=lambda p: -self.basis.total(p))[: max(shortlist, top_n)]

        n_rounds = self.settings.n_rounds
        my_future_after = max(0, n_rounds - len(state.my_roster) - 1)
        opp_players = [p for r in state.opponent_rosters for p in r]
        n_opp = max(1, len(state.opponent_rosters))
        # One representative opponent: the average opposing team, not the union of them all.
        opp_avg_size = len(opp_players) / n_opp if n_opp else 0
        opp_future = max(0, int(round(n_rounds - opp_avg_size)))
        representative_opp = _representative(state.opponent_rosters)

        start = self._warm or [1.0] * len(cats)

        # Baseline: the objective if we passed. Candidate value is reported as a delta from
        # this, so "how much does this pick actually move the matchup?" is legible.
        base_val, _ = self._evaluate(
            state.my_roster, representative_opp, pool,
            my_future_after + 1, opp_future, start,
        )

        out: list[Candidate] = []
        for pid in pool:
            rest = [p for p in pool if p != pid]
            val, probs, w = self._optimise_weights(
                [*state.my_roster, pid], representative_opp, rest,
                my_future_after, opp_future, start,
            )
            out.append(
                Candidate(
                    player_id=pid,
                    value=round(val, 6),
                    delta=round(val - base_val, 6),
                    win_probs={c: round(p, 4) for c, p in zip(cats, probs, strict=True)},
                    weights={c: round(x, 4) for c, x in zip(cats, w, strict=True)},
                )
            )
        out.sort(key=lambda c: -c.value)
        if out:
            self._warm = [out[0].weights[c] for c in cats]
        return out[:top_n]

    def best_pick(self, state: DraftState, available: list[str] | None = None) -> Candidate | None:
        ranked = self.evaluate_candidates(state, available, top_n=1)
        return ranked[0] if ranked else None

    def reset_warm_start(self) -> None:
        self._warm = None


def _representative(opponent_rosters: list[list[str]]) -> list[str]:
    """A single stand-in opponent: the roster of the strongest opposing team.

    Averaging every opponent into one composite would understate the difficulty of the
    matchup, because a category league is won against *specific* teams and the composite is
    smoother than any of them. Taking the deepest roster is a deliberate slight pessimism.
    """
    if not opponent_rosters:
        return []
    return max(opponent_rosters, key=len)
