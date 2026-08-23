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
from enum import StrEnum

from fantasy_gm.draft.objective import category_probabilities, score_objective
from fantasy_gm.draft.settings import DraftSettings
from fantasy_gm.draft.xscore import XScoreBasis


class OpponentModel(StrEnum):
    """Who the objective is computed against (task 3.12).

    The published formulation optimises against a single representative opponent, but the
    replay grades against the whole field — and those are different problems. Conceding a
    category costs one opponent's worth of probability against a stand-in, and *every*
    opponent's worth against a league. If the objective is cheap where the grading is
    expensive, the optimizer will happily buy the thing it is later punished for.

    Three arms, so the two halves of that hypothesis can be told apart:

    * ``REPRESENTATIVE`` — the shipped behaviour, kept bit-identical so the numbers already in
      `results.md` remain the baseline. Selects by roster *length*, which in a snake draft is
      near-constant across teams, so the choice is in practice an arbitrary fixed seat.
    * ``STRONGEST`` — a genuine single opponent: the opposing roster with the highest total
      basis value. Isolates "the stand-in was arbitrary" from "a stand-in is the wrong model".
    * ``FIELD`` — the objective averaged over every opponent, which is the quantity all-play-all
      grading actually measures.
    """

    REPRESENTATIVE = "representative"
    STRONGEST = "strongest"
    FIELD = "field"


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
        opponent_model: OpponentModel = OpponentModel.REPRESENTATIVE,
        future_from_shortlist: bool = True,
        normalise_weights: bool = False,
        future_slices: bool = False,
    ):
        self.basis = basis
        self.settings = settings or DraftSettings()
        self.softmax_temp = softmax_temp
        self.steps = steps
        self.lr = lr
        self.tie_margin = tie_margin
        self.opponent_model = OpponentModel(opponent_model)
        # All three default to the shipped behaviour, so every number already in `results.md`
        # reproduces bit-for-bit; task 3.8 switches them one at a time to measure what each is
        # worth. `future_slices` is the one that matters — see `_future_block`.
        self.future_from_shortlist = future_from_shortlist
        self.normalise_weights = normalise_weights
        self.future_slices = future_slices
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

    def _future_block(
        self, available: list[str], weights: list[float], n_picks: int, stride: int
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Total mean and variance of ALL ``n_picks`` remaining picks, each drawn from the pool
        that will actually still be there when that pick comes round.

        The shipped model values one future pick and multiplies by the number of them, so a
        thirteenth-round pick is priced identically to a third-round pick. It is not close: by
        round thirteen the top ``12 x 12`` players are gone. Measured, the shipped engine
        expects *every* remaining round — its own and its opponent's — to land on roughly the
        25th-best player on the board. A team whose future is that good has little reason to
        care which player it takes now, which blunts exactly the marginal comparison H₀ exists
        to make.

        (Widening the pool alone does not fix this, and we checked before assuming: at the
        shipped softmax temperature almost all the probability mass sits on the top of the
        board whatever the pool contains, so drawing from all 144 remaining players instead of
        the 40-man shortlist moves the expected pick by about 1%. The defect is *where in the
        board the pick sits*, not how many players are nominally in the bag.)

        ``available`` is ranked, so the pool at future pick ``j`` is the suffix starting at
        ``(j-1) * stride`` — one player per team per round, on average, taken ahead of us. That
        every slice is a *suffix* is what makes this affordable: one backward accumulation over
        the pool serves all the slices at once, so the whole block costs the same as the single
        softmax it replaces.
        """
        cats = self.settings.categories
        zero = ({c: 0.0 for c in cats}, {c: 0.0 for c in cats})
        if n_picks <= 0 or not available:
            return zero

        scores = []
        for pid in available:
            s = 0.0
            for w, c in zip(weights, cats, strict=True):
                s += w * self.basis.category_score(pid, c)
            scores.append(s)
        top = max(scores)
        exps = [math.exp(self.softmax_temp * (v - top)) for v in scores]

        n = len(available)
        # Suffix accumulators, built once: sum of weight, of weight*mean, of weight*mean^2 and
        # of weight*tau^2, per category, from each index to the end of the board.
        suf_z = [0.0] * (n + 1)
        suf_a = {c: [0.0] * (n + 1) for c in cats}
        suf_b = {c: [0.0] * (n + 1) for c in cats}
        suf_t = {c: [0.0] * (n + 1) for c in cats}
        for i in range(n - 1, -1, -1):
            e = exps[i]
            stats = self.basis.stats.get(available[i])
            suf_z[i] = suf_z[i + 1] + e
            for c in cats:
                ps = stats.get(c) if stats else None
                m = ps.mean if ps else 0.0
                t2 = ps.std**2 if ps else 0.0
                suf_a[c][i] = suf_a[c][i + 1] + e * m
                suf_b[c][i] = suf_b[c][i + 1] + e * m * m
                suf_t[c][i] = suf_t[c][i + 1] + e * t2

        mean = {c: 0.0 for c in cats}
        var = {c: 0.0 for c in cats}
        for j in range(n_picks):
            start = min(j * stride, n - 1)
            z = suf_z[start]
            if z <= 0.0:
                continue
            for c in cats:
                mu = suf_a[c][start] / z
                mean[c] += mu
                var[c] += max(suf_b[c][start] / z - mu * mu, 0.0) + suf_t[c][start] / z
        return mean, var

    # --- the objective -------------------------------------------------------

    def _opponent_totals(
        self, state: DraftState, opp_future: int
    ) -> list[tuple[dict[str, float], dict[str, float], int]]:
        """The opposing side of the differential: ``(mean, var, future_picks)`` per opponent.

        Weight-independent, so this is built once per pick rather than once per gradient step.
        The stand-in models return a single entry and keep the shipped ``opp_future`` (derived
        from the *average* opponent's roster size); ``FIELD`` returns every opponent with its
        own exact remaining-pick count, which is what it means to be scored against a league of
        teams at slightly different points in the snake.
        """
        rosters = state.opponent_rosters
        if not rosters:
            return [({c: 0.0 for c in self.settings.categories},
                     {c: 0.0 for c in self.settings.categories}, opp_future)]

        if self.opponent_model is OpponentModel.FIELD:
            out = []
            for r in rosters:
                mean, var = self._totals(r)
                out.append((mean, var, max(0, self.settings.n_rounds - len(r))))
            return out

        if self.opponent_model is OpponentModel.STRONGEST:
            chosen = max(rosters, key=lambda r: sum(self.basis.total(p) for p in r))
        else:
            chosen = _representative(rosters)
        mean, var = self._totals(chosen)
        return [(mean, var, opp_future)]

    def _evaluate(
        self,
        my_totals: tuple[dict[str, float], dict[str, float]],
        opponents: list[tuple[dict[str, float], dict[str, float], int]],
        available: list[str],
        my_future: int,
        opp_future_profile: tuple[dict[str, float], dict[str, float]] | None,
        weights: list[float],
    ) -> tuple[float, list[float]]:
        """Objective and per-category win probabilities under strategy ``weights``.

        Averaged over ``opponents`` — one entry for the stand-in models, all of them for
        ``FIELD``. Everything independent of ``weights`` (roster totals, the opponents' shared
        future-pick profile) arrives precomputed, because this runs ~2·C times per gradient step
        and the softmax over the pool dominates its cost.
        """
        cats = self.settings.categories
        base_mean, base_var = my_totals
        my_mean = dict(base_mean)
        my_var = dict(base_var)

        if my_future > 0:
            if self.future_slices:
                fm, fv = self._future_block(
                    available, weights, my_future, self.settings.n_teams
                )
                for c in cats:
                    my_mean[c] += fm[c]
                    my_var[c] += fv[c]
            else:
                fm, fv = self._weighted_future(available, weights)
                for c in cats:
                    my_mean[c] += my_future * fm[c]
                    my_var[c] += my_future * fv[c]

        total = 0.0
        acc = [0.0] * len(cats)
        for op_mean0, op_var0, opp_future in opponents:
            op_mean = dict(op_mean0)
            op_var = dict(op_var0)
            if opp_future > 0 and opp_future_profile is not None:
                if isinstance(opp_future_profile, dict):
                    om, ov = opp_future_profile[opp_future]
                    for c in cats:
                        op_mean[c] += om[c]
                        op_var[c] += ov[c]
                else:
                    om, ov = opp_future_profile
                    for c in cats:
                        op_mean[c] += opp_future * om[c]
                        op_var[c] += opp_future * ov[c]
            mean_diffs = {c: my_mean[c] - op_mean[c] for c in cats}
            var_diffs = {c: max(my_var[c] + op_var[c], 1e-12) for c in cats}
            probs = category_probabilities(mean_diffs, var_diffs, self.settings, self.tie_margin)
            total += score_objective(probs, self.settings)
            for i, p in enumerate(probs):
                acc[i] += p

        k = len(opponents) or 1
        return total / k, [p / k for p in acc]

    def _optimise_weights(
        self,
        my_totals: tuple[dict[str, float], dict[str, float]],
        opponents: list[tuple[dict[str, float], dict[str, float], int]],
        available: list[str],
        my_future: int,
        opp_future_profile: tuple[dict[str, float], dict[str, float]] | None,
        start: list[float],
    ) -> tuple[float, list[float], list[float]]:
        """Adam on the strategy weights, numeric gradient."""
        n = len(self.settings.categories)
        w = list(start)
        m = [0.0] * n
        v = [0.0] * n
        b1, b2, eps, h = 0.9, 0.999, 1e-8, 1e-3

        best_val, best_probs = self._evaluate(
            my_totals, opponents, available, my_future, opp_future_profile, w
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
                    my_totals, opponents, available, my_future, opp_future_profile, up
                )
                gd, _ = self._evaluate(
                    my_totals, opponents, available, my_future, opp_future_profile, dn
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
            if self.normalise_weights:
                w = _renormalise(w, n)
            val, probs = self._evaluate(
                my_totals, opponents, available, my_future, opp_future_profile, w
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

        ranked = sorted(pool, key=lambda p: -self.basis.total(p))
        shortlisted = ranked[: max(shortlist, top_n)]
        # ``shortlist`` exists to bound how many players get the full gradient descent. It is
        # also, silently, bounding the pool that *future picks* are drawn from: with the default
        # of 40 the engine draws all twelve of its remaining rounds, and its opponent's, from
        # the top forty when a twelve-team draft will consume about 150.
        #
        # **Measured, this is worth about 1%** and is not the defect it looks like — at the
        # shipped softmax temperature nearly all the mass sits at the top of the board whatever
        # else is in the bag. It is switchable so that stays measured rather than remembered;
        # the term that actually mattered is `future_slices`.
        future_universe = shortlisted if self.future_from_shortlist else ranked
        pool = shortlisted

        n_rounds = self.settings.n_rounds
        my_future_after = max(0, n_rounds - len(state.my_roster) - 1)
        opp_players = [p for r in state.opponent_rosters for p in r]
        n_opp = max(1, len(state.opponent_rosters))
        # One representative opponent: the average opposing team, not the union of them all.
        opp_avg_size = len(opp_players) / n_opp if n_opp else 0
        opp_future = max(0, int(round(n_rounds - opp_avg_size)))
        opponents = self._opponent_totals(state, opp_future)

        start = self._warm or [1.0] * len(cats)

        # Opponents draft *competently but not adaptively*: best-available under neutral
        # category weights, via the same softmax machinery. Modelling them as the pool average
        # instead would hand us a free edge for every remaining round — the optimizer would then
        # believe that simply having picks left is an advantage, which inflates early-draft
        # confidence. The edge H₀ should claim is that it *shapes* its picks, not that its
        # opponents draft at random.
        #
        # Neutral weights make this independent of the strategy being optimised, so it is
        # computed once per pick rather than once per gradient step. That hoist is what keeps
        # FIELD affordable: the extra opponents only add the O(C²) Poisson-binomial DP, while
        # the softmax over the pool — the actual cost — is now shared by all of them.
        neutral = [1.0] * len(cats)

        def opp_future_for(avail: list[str]):
            """The opposing side's unknown picks, priced the same way ours are.

            Under ``future_slices`` a count of remaining picks no longer scales one profile, so
            this returns a total per distinct remaining-pick count instead. ``FIELD`` is the
            only model that produces more than one count, and never more than a handful.
            """
            if not self.future_slices:
                return self._weighted_future(avail, neutral)
            return {
                k: self._future_block(avail, neutral, k, self.settings.n_teams)
                for k in {o[2] for o in opponents}
            }

        opp_profile = opp_future_for(future_universe)

        # Baseline: the objective if we passed. Candidate value is reported as a delta from
        # this, so "how much does this pick actually move the matchup?" is legible.
        base_val, _ = self._evaluate(
            self._totals(state.my_roster), opponents, future_universe,
            my_future_after + 1, opp_profile, start,
        )

        out: list[Candidate] = []
        for pid in pool:
            rest = [p for p in future_universe if p != pid]
            val, probs, w = self._optimise_weights(
                self._totals([*state.my_roster, pid]), opponents, rest,
                my_future_after, opp_future_for(rest), start,
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


def _renormalise(w: list[float], n: int) -> list[float]:
    """Project the strategy weights back onto a fixed L1 norm (the paper's constraint).

    The published formulation holds ``Σ j_C = 1`` and renormalises after every gradient step.
    Without it the weight scale and the softmax temperature are the same parameter twice: the
    optimizer can sharpen the future-pick distribution simply by inflating every weight, which
    is a direction in the objective that changes the answer without expressing any strategy.
    That is a textbook way to land in a bad local optimum, and this engine had no constraint at
    all — only a ±4 clip, which bounds the degeneracy rather than removing it.

    We hold the **L1** norm rather than the signed sum. ``Σ j = 1`` is only well defined while
    every weight is positive, as it is in the paper, where punting shows up as a weight near
    zero; this engine allows negative weights and the signed sum can pass through zero. Fixing
    ``Σ|j| = n`` is the same constraint on the positive orthant, up to the constant that makes
    the neutral vector ``[1, …, 1]`` a fixed point.
    """
    scale = sum(abs(x) for x in w)
    if scale < 1e-9:
        return [1.0] * n
    return [x * n / scale for x in w]


def _representative(opponent_rosters: list[list[str]]) -> list[str]:
    """A single stand-in opponent: the roster of the strongest opposing team.

    Averaging every opponent into one composite would understate the difficulty of the
    matchup, because a category league is won against *specific* teams and the composite is
    smoother than any of them. Taking the deepest roster is a deliberate slight pessimism.
    """
    if not opponent_rosters:
        return []
    return max(opponent_rosters, key=len)
