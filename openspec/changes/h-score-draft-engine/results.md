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

## Task 3.12 — the representative opponent, measured (2026-08-17)

Candidate cause 1 was the leading suspect. It is **real, and it is partial**: fixing it recovers
a meaningful share of the deficit without closing it.

Three opponent models drafted as separate seats in the *same* room, against the same bots,
graded on the same weeks — 12 rotations, pool 180, 5 Adam steps, n = 29,700 each.

| arm | opponent model | cat win% | vs g_score | matchup% | vs g_score |
|---|---|---|---|---|---|
| `g_score` | — (static board) | **62.9%** | — | **76.7%** | — |
| `h_score_field` | all 11, objective averaged | 59.7% | −3.2pp | 72.8% | −3.9pp |
| `h_score` | one stand-in (shipped) | 58.7% | −4.2pp | 69.9% | −6.8pp |
| `h_score_strongest` | one stand-in, genuinely strongest | 55.0% | −7.9pp | 63.8% | −12.9pp |

**Grading the objective against the field it is scored on closes 43% of the matchup gap and
24% of the category gap.** That it helps roughly twice as much on matchup win rate is the
coherence check: matchup% is much closer to H₀'s own `P(win ≥5)` objective, so an opponent-model
fix should show up there first, and it does.

**Making the stand-in *stronger* makes it much worse** — `strongest` is the worst arm by a wide
margin. This kills the intuitive reading of the bug. The problem was never that the stand-in
was too weak a benchmark; it is that optimising against **any single opponent** licenses
concentration that a league then punishes. The per-category rates show the mechanism directly:
`strongest` punts points to 45.8% and threes to **17.2%** to buy fg_pct at 72.1%, which is a
coherent plan against one team and a losing one against eleven. `field` is the least
concentrated H₀ arm and the best.

### A methodological correction that matters

**These numbers are not comparable to the headline table above, and neither are any future
runs with a different arm count.** Adding two more H₀ seats changed the room from 4 named
strategies + 8 bots to 6 + 6. The proof is that `g_score` — a static board that cannot be
affected by any engine setting — moved from 63.1%/80.0% to 62.9%/76.7%, and `z_score` fell
49.7% → 44.3%. A stronger field simply beats the weaker strategies more often under
all-play-all.

So only **within-run** deltas are meaningful. The `h_score` vs `g_score` matchup deficit
reproduces almost exactly across the two runs (−6.6pp then −6.8pp), which is the reassuring
part; the absolute rates do not, and should not be quoted across runs.

### What this does and does not settle

- H₀ **still loses to the static G-score board** on its best arm. 3.12 is a real contributor,
  not the whole explanation. Causes 3 (local optima, task 3.9) and 4 (the future-pick softmax)
  remain open, and are now the leading candidates.
- Every H₀ arm is bad at ft_pct (21.9–34.9%) and tov (23.6–42.1%) where `g_score` is fine on
  ft_pct (67.4%). That is a category-level weakness the pooled numbers hide, and it echoes the
  waiver-replay finding that percentage categories are the weak spot.
- Single season. A three-season replication (2023-24, 2024-25, 2025-26) and the task 3.13
  schedule-grading arm are running; this section will be revised if they disagree.

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
- The headline table is a single season. **No longer a blocker**: 2023-24 and 2024-25 were
  backfilled on 2026-08-17, so the store now holds three complete seasons (26,401 / 26,306 /
  26,651 logs) and replication is running rather than pending.
- Category-level rates are collected per strategy but not yet analysed; the percentage
  categories are the likely weak spot, as they were in waiver replay.
