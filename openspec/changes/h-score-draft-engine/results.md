# Draft replay results

Real 2025-26 season, 12-team leagues, 13 rounds, 180-player pool, realized weekly category
outcomes from actual box scores. **Seat assignment rotated 12 times so every strategy drafts
from every seat**; grading is all-play-all (every team vs every other, every week).
n = 29,700 category decisions per strategy.

## Headline (2026-08-17)

| strategy | cat win% | matchup% | cat win% | matchup% |
|---|---|---|---|---|
| | *(8 Adam steps→5)* | | *(20 steps)* | |
| **g_score** | **63.2%** | **79.6%** | **63.1%** | **80.0%** |
| h_score | 60.3% | 72.9% | 60.6% | 73.4% |
| z_score | 49.1% | 48.0% | 49.7% | 48.7% |
| adp | 48.9% | 47.8% | 48.2% | 44.0% |

## What is established

**The variance-aware basis is the real finding, and it is large.** G-score beats z-score by
**+13.4pp** on category win rate and **+31pp** on matchup win rate. That is the change's core
thesis — that z-score is the wrong metric for weekly H2H because it assumes future production
is known exactly — and it holds decisively on real data against the incumbent every commercial
tool uses. It is also a *shippable product on its own*: a static board, no optimizer required.

**z-score is barely better than following ADP** (49.7% vs 48.2%). Both sit at chance. That is
worth sitting with: the metric the entire market runs on is, in this replay, worth almost
nothing over reading down a consensus list.

## What is NOT established: H₀ is currently worse than the static board

H₀ loses to G-score by ~2.5pp on categories and ~6.6pp on matchups. **This is not
under-optimization** — quadrupling the Adam budget (5 → 20 steps, 250s → 986s) moved it by
0.3pp, well inside noise. Something structural is wrong, not something under-tuned.

This does not refute the published result. It says *this implementation* does not reproduce it.
Candidate causes, in the order I would test them:

1. **The single representative opponent may be the wrong model for this grading.** H₀ optimizes
   `P(win ≥5 of 9)` against *one* opponent (currently the deepest opposing roster). It is then
   graded against all eleven, every week. Conceding a category is cheap against one opponent and
   expensive against a field — a punted category is lost to *everyone*, forever. So the
   optimizer is solving a different problem from the one it is scored on.
2. **All-play-all grading may itself be biased against concentration.** Real H2H plays a weekly
   schedule against one team, which is precisely the setting where punting works. All-play-all
   was chosen to remove schedule luck, but it may systematically penalise exactly the strategy
   H₀ discovers. **This cuts both ways and needs testing, not assertion** — a schedule-based
   grading run is the robustness check.
3. **Local optima** — see A-DRAFT-9: on a real board H₀ punts rebounds after taking Jokić. The
   step-count insensitivity above makes "it just needs more iterations" unlikely, but multi-start
   is a different remedy from more steps and has not been tried.
4. **The future-pick softmax** is my formulation, not the paper's. It models an unknown future
   pick as a softmax-weighted draw over the pool; the paper decomposes positional assignment
   into the differential directly.

Note that H₀ also loses on **matchup win rate** (73.4% vs 80.0%), which is much closer to its
own objective than category win rate is. That weakens the "we are grading it on the wrong
metric" defence, and points at causes 1 and 3.

## Consequence for the plan

The interview chose to skip shipping G-score and go straight to H₀ (see `discussion.md`). On
current evidence that ordering is inverted: **the G-score board is the product that works
today**, and H₀ is a research problem that has not yet paid off. Given the September window,
the low-risk read is that the fallback is now the front-runner and H₀ continues as the
follow-up it was always intended to be — not that H₀ should be abandoned.

## Caveats on these numbers

- **ADP is a value-ranking proxy**, not market ADP (Yahoo's API is application-gated). Real ADP
  is wrong in ways a value ranking is not, and beating a wrong market is where draft edge comes
  from. Margins over `adp` here are therefore a lower bound.
- **The replay is an oracle.** Strategies draft with realized season production known
  (`ActualsProjectionSource`). This measures *drafting*, not forecasting — a live draft also has
  to survive projection error, which is Track B's problem and is not tested here.
- Single season. Multi-season replay needs the 2024-25 backfill (task 2.10).
- Category-level rates are collected per strategy but not yet analysed; the percentage
  categories are the likely weak spot, as they were in waiver replay.
