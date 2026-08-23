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

> **Read +13.4pp with the amendment below (A-DRAFT-14, 2026-08-23).** Most of that margin is
> *availability*, not variance: this replay counts weeks a player missed as zero-production
> weeks, and z-score is availability-blind, so the two metrics disagree loudest about players
> who got injured. That is correct here — the season already happened, and a missed week really
> did lose the category — but it is **hindsight for a board published before a draft**. On a
> forward-honest basis the same board beats z-score by **+5 to +9pp**, which is the number to
> quote in anything a drafter reads. See "Availability is most of the edge" below.

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

## Tasks 3.12 / 3.13 — the two candidate causes, measured (2026-08-17)

Replicated on **three independent seasons** (2023-24, 2024-25, 2025-26), 12 rotations each,
pool 180, 5 Adam steps, n = 29,700 category decisions per strategy per season. All arms draft
as separate seats in the *same* room against the same bots and are graded on the same weeks.

### 3.12 — the representative opponent: real, but small

Pooled over the three seasons, all-play-all grading:

| arm | opponent model | cat win% | vs g_score | matchup% | vs g_score |
|---|---|---|---|---|---|
| `g_score` | — (static board) | **60.3%** | — | **71.7%** | — |
| `h_score_field` | all 11, objective averaged | 57.6% | −2.7pp | 67.6% | −4.1pp |
| `h_score` | one stand-in (shipped) | 57.1% | −3.2pp | 66.0% | −5.7pp |
| `h_score_strongest` | one stand-in, genuinely strongest | 54.3% | −6.0pp | 61.5% | −10.2pp |

Scoring the objective against the field it is graded on closes **28% of the matchup gap and
16% of the category gap**. It helps roughly twice as much on matchup win rate, which is the
coherence check: matchup% is far closer to H₀'s own `P(win ≥5)` objective than category% is, so
an opponent-model fix should surface there first, and it does.

**The effect is directionally consistent but not large, and one season dissents.** `field`
minus `h_score`, per season:

| | 2023-24 | 2024-25 | 2025-26 |
|---|---|---|---|
| cat win% | **−0.5pp** | +0.9pp | +1.0pp |
| matchup% | +0.3pp | +1.8pp | +2.9pp |

Matchup% improves in all three; category% goes the wrong way in 2023-24. An earlier
single-season (2025-26) reading of this put the gap closed at 43%/24% — that overstated it, and
the three-season pooled figures above supersede it. Treat 3.12 as a **modest real effect**, not
a fix.

**The arm that failed is the more informative one.** `strongest` is the worst arm in every
season, by a wide margin. That kills the intuitive reading: the defect was never that the
stand-in was too weak a benchmark. Optimising against **any single opponent** licenses
concentration that a league punishes, and a *tougher* single opponent licenses more of it. The
per-category rates show the mechanism directly — in 2025-26 `strongest` punts points to 45.8%
and threes to **17.2%** to buy fg_pct at 72.1%: coherent against one team, losing against
eleven. `field` is the least concentrated H₀ arm and the best.

### 3.13 — schedule-based grading: the concern was unfounded, structurally

Pooled over three seasons, the same drafts graded both ways:

| strategy | all-play-all cat% | schedule cat% | all-play-all matchup% | schedule matchup% |
|---|---|---|---|---|
| `g_score` | 60.3% | 59.9% | 71.7% | 70.5% |
| `h_score_field` | 57.6% | 58.1% | 67.6% | 69.9% |
| `h_score` | 57.1% | 56.9% | 66.0% | 65.6% |
| `h_score_strongest` | 54.3% | 54.4% | 61.5% | 61.6% |

Nothing moves systematically, and **it cannot**: a round-robin schedule plays a balanced subset
of exactly the pairings all-play-all enumerates, so the two have the *same expectation* and
differ only in variance. All-play-all is the mean of which a schedule is a sample. The
hypothesis that all-play-all "systematically penalises concentrated builds" was therefore
structurally void, not merely unsupported — and the measurement agrees, with every strategy
landing within ~0.5pp on category rate and the differences unsystematic in sign.

The schedule arm carries 11× fewer matchups (n = 900 pooled vs 9,900), so its standard error is
~1.5pp against ~0.5pp. `h_score_field` appearing to draw level with `g_score` on schedule
matchup% (69.9% vs 70.5%) is inside that noise and should not be read as a result.

What punting actually exploits in a real league is the *specific* opponents drawn and the
playoff bracket — variance, not mean. All-play-all remains the right default precisely because
it removes that, and 3.13 is closed rather than a standing caveat.

### A methodological correction that matters

**Absolute rates are not comparable across runs with different arm counts.** Adding two H₀
seats changed the room from 4 named strategies + 8 bots to 6 + 6. The proof is that `g_score` —
a static board no engine setting can touch — moved from 63.1%/80.0% in the headline run to
62.9%/76.7% in the 2025-26 arm of this one, while `z_score` fell 49.7% → 44.3%. A stronger
field simply beats the weaker strategies more often.

So only **within-run** deltas are quotable. The reassuring part is that the `h_score` vs
`g_score` matchup deficit reproduces across independent runs (−6.6pp, then −6.8pp).

### What this does and does not settle

- H₀ **still loses to the static G-score board** on its best arm, in all three seasons. 3.12 is
  a contributor worth keeping; it is not the explanation. Causes 3 (local optima, task 3.9) and
  4 (the future-pick softmax) are now the leading candidates, and 3.8 — does this implementation
  reproduce the paper at all — remains the priority.
- Every H₀ arm is bad at ft_pct (21.9–34.9% in 2025-26) and tov (23.6–42.1%) where `g_score`
  manages ft_pct at 67.4%. A category-level weakness the pooled numbers hide, echoing the
  waiver-replay finding that percentage categories are the weak spot.
- `field` is the opponent model that should ship: better on matchup% in every season, free (the
  expensive softmax over the pool is shared across opponents), and the only one that matches
  what the grading measures. **The default is deliberately left on `REPRESENTATIVE` for now** —
  3.8 asks whether this implementation reproduces the paper at all, and that diagnostic needs
  the shipped configuration as its baseline. Flip the default once 3.8 resolves.

## Availability is most of the edge (2026-08-23)

The headline compares two metrics that differ in **two** ways, not one. G-score aggregates
*weekly* totals and charges a player for weeks they missed; z-score averages *per game* and
cannot see availability at all. Measured on 2025-26 over the 156-player pool,
`corr(games played, rank change vs z-score) = +0.627` — the eight biggest "z-score overrates
them" names played 20–43 games, the eight biggest "underrates" played 75–82.

Scoring the availability treatments as separate arms in the same replay (12-team, 6 seat
rotations, 14,850 category decisions per arm), category win rate:

| arm | 2025-26 (seed 7 / 11) | 2024-25 (seed 7 / 11) |
|---|---|---|
| G, realized availability | 65.2 / 66.2 | 63.6 / 64.3 |
| G, neutral (active weeks only) | 59.1 / 58.6 | 52.4 / 52.7 |
| G, projected availability | 56.3 / 58.1 | 53.9 / 53.9 |
| z-score | 51.3 / 49.0 | 47.4 / 46.9 |
| adp | 45.8 / 46.1 | 48.0 / 48.1 |

- **The realized arm reproduces the headline** (~+14pp over z-score) and is the arm the
  +13.4pp figure comes from. It requires knowing who got hurt.
- **The forward-honest claim is +5 to +9pp**, and it holds in all four runs across two seasons
  and two seeds. Still far larger than z-score's own margin over ADP — which is *negative* in
  2024-25, reinforcing the standing finding that z-score ≈ reading down a list.
- **Neutral and projected are not separable here.** The ordering flips by season and by seed.
  Per the standing rule on single-season deltas, neither is claimed to win; the board defaults
  to `projected` on product grounds, stated as such in A-DRAFT-14.

**This does not move any number already published**, because `include_idle_weeks` still
defaults to True everywhere the replay harness touches. What changes is which number the
*board* is allowed to advertise.

**For the write-up (task 6.3):** lead with +5 to +9pp against z-score on a preseason-honest
basis, and treat the availability result as its own finding rather than burying it — "the
market's metric cannot see who plays" is a stronger and more defensible story than an inflated
single number, and it is the one that survives someone checking it.

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
