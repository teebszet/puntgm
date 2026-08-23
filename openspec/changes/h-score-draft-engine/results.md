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

## Availability is most of the edge (2026-08-23) — SUPERSEDED 2026-08-24

> **Read the next section first.** The +5 to +9pp "forward-honest" figure below is
> **withdrawn**: the `neutral`/`projected` boards it was measured on still leaked realized
> availability, and the harness that measured it had a seat-adjacency bias. Both are fixed.
> The section is kept because its *direction* was right and because the correction is only
> legible next to what it corrects.

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
- ~~**The forward-honest claim is +5 to +9pp**, and it holds in all four runs across two seasons
  and two seeds.~~ **Withdrawn — see below. The true figure against a total-value baseline is zero.** Still far larger than z-score's own margin over ADP — which is *negative* in
  2024-25, reinforcing the standing finding that z-score ≈ reading down a list.
- **Neutral and projected are not separable here.** The ordering flips by season and by seed.
  Per the standing rule on single-season deltas, neither is claimed to win; the board defaults
  to `projected` on product grounds, stated as such in A-DRAFT-14.

**This does not move any number already published**, because `include_idle_weeks` still
defaults to True everywhere the replay harness touches. What changes is which number the
*board* is allowed to advertise.

~~**For the write-up (task 6.3):** lead with +5 to +9pp against z-score on a preseason-honest
basis.~~ Superseded; see the write-up guidance in the next section.

## The whole edge is availability. The variance correction is worth nothing. (2026-08-24)

Prompted by a fair challenge: *the industry does not run bare z-score, it runs z-score plus
tweaks — what are they, and does the result survive them?* It does not survive the one that
matters.

### The baseline was a strawman

`valuation.player_values` is a per-game z-score. That is what every **free** ranking list
publishes, and it is blind to games played. It is not what a drafter using Basketball Monster
or Hashtag Basketball holds: both expose a **total-value** mode that multiplies by games. Since
the whole measured G-score edge turned out to be availability (previous section), beating only
the per-game form was never going to survive contact.

`fantasy_gm/draft/zvariants.py` adds the tweaks as switchable arms — total value (realized /
fitted-projection / naive-carry-forward), replacement-level iteration, punt-aware category
subsets — reproducing the shipped z-score exactly under defaults, which a test pins.

### Two measurement defects found on the way, both of which inflated our own numbers

**1. A second hindsight leak in the forward boards.** `neutral` and `projected` aggregated to
weekly totals over *active* weeks. A week in which a player suited up twice instead of four
times still counted, at a lower total — so each player's realized games *per week* survived the
A-DRAFT-14 fix, which had only removed whole missed weeks. That factor correlates +0.72/+0.78
with realized games played and is measured on the season being graded:

```
corr(REALIZED  games, G-vs-z rank gain) = +0.604 / +0.642   <- should be ~0
corr(PROJECTED games, G-vs-z rank gain) = -0.024 / +0.041   <- is ~0
```

The board promoted exactly the players who turned out to play more while showing no
relationship to the availability it was handed. Fixed by *constructing* the weekly total
instead of measuring it — per-game mean and variance compounded over a **scheduled** game
count, `mu' = n.r.m`, `tau'^2 = n.r.v + n.r(1-r).m^2` — which removes the term structurally.
After the fix: +0.067 / -0.104.

**2. A seat-adjacency bias in the replay harness itself.** Named arms are seated consecutively
and rotate *together*, so they stay adjacent in a fixed order, and over an odd number of rounds
a snake gives the lower-seated neighbour the first of the pair once more than its neighbour.
Rotation does not fix this. Calibrated by drafting one board **against itself**, the artifact
measured up to **+9.5pp** for whichever arm was listed first — which in `build_strategies` is
always `g_score`, ahead of `z_score`. Fixed by mirroring: every rotation also runs with the arm
order reversed. **Every table from here on carries a null arm** (the same board in both seats)
so the noise floor is visible rather than assumed.

### The numbers

Seat-mirrored, 12 rotations so every arm visits every seat, 4 seeds, ~29,700 category decisions
per arm per seed. Board kappa = 0 (see A-DRAFT-4 below). Category win-rate delta, in pp:

| pair | 2025-26 | 2024-25 | 2023-24 |
|---|---|---|---|
| **null — same board both seats** | +2.6 −0.7 +2.1 +1.0 | −0.2 −0.6 −0.2 +0.6 | +0.1 −0.2 +0.3 −0.5 |
| G-realized vs **per-game** z | +17.9 +19.6 +17.5 +18.6 | +15.9 +16.1 +15.9 +17.1 | +10.9 +13.1 +12.0 +11.9 |
| G-realized vs **total-value** z (both see realized games) | −1.2 −0.9 −1.3 −1.6 | −0.7 −0.7 −1.0 −1.0 | −3.8 −3.8 −3.4 −3.8 |
| G-projected vs **per-game** z | +2.8 +4.6 +3.8 +2.8 | +5.7 +5.7 +6.7 +6.9 | — |
| G-projected vs **total-value** z (both see the same projection) | *identical to null* | *identical to null* | — |

Read in order:

1. **The published headline survives, and it is real: +11 to +20pp over the per-game z-score,
   in all twelve runs across three seasons.** The seat fix moved it by under 1pp. This is the
   number the free board can advertise, and the mechanism is stated honestly by saying so:
   *the rankings everyone publishes cannot see who plays.*

2. **Give z-score the games it was missing and the entire edge disappears — G-score loses.**
   −0.7 to −3.8pp in all twelve runs. The variance correction, which is the whole thesis of
   G-score, is worth *less than nothing* once the two metrics are matched on availability.

3. **The forward comparison is not merely zero, it is degenerate.** At kappa=0 the forward
   G board and total-value z are *the same board* — max rank delta 0 over 156 players — because
   a leak-free forward G board is per-game mean x projected rate, which is exactly what
   total-value z computes (the x82 is a per-player constant and cancels under standardisation).
   The last row of the table equals the null row not by coincidence but by identity. That is
   the cleanest available proof that nothing is left.

4. **Replacement-level iteration is a no-op** (mean rank shift 2.2 places, no measurable
   effect), and **the fitted A13 availability model is not separable from naive last-season
   carry-forward on a board** (flips by season: −0.3 to −3.5 in 2025-26, +0.2 to +1.5 in
   2024-25). A13's large in-season win stands; it is a different task.

### A-DRAFT-4 resolved: kappa is 0, not 1

kappa weights period variance in the denominator. Swept over {0, 0.25, 0.5, 1, 2, 4} in both a
forward-honest and a hindsight pairing, across two seasons and two seeds, **kappa=0 won every
run** and the decline was monotone (hindsight pair: −0.2/+0.2/+1.0/+1.2 at kappa=0 down to
−3.4/−3.5/−1.3/−0.3 at kappa=4). It was never under-tuned; it is harmful. `BOARD_KAPPA = 0.0`.

`xscore.DEFAULT_KAPPA` is deliberately left at 1.0 for the H₀ engine: H₀ scores through a
Poisson-binomial over category wins, where the term does different work, and task 3.8 has not
established that the implementation reproduces the paper at all. Moving both at once would
confound the two investigations.

### What to write up

The defensible claim is **not** "our metric beats z-score". It is:

> **The public rankings the whole category-league market drafts from are per-game numbers, and
> a per-game number cannot see that a player misses a third of the season. Correcting for that
> is worth +11 to +20pp of category win rate against those rankings, measured on three
> completed seasons.**

True, large, reproducible, and it survives a hostile reader — because it is stated against the
baseline it actually beats. What must **not** be claimed is that this is a variance effect, or
that it holds against a paid tool's total-value mode. It does not.

The product consequence is that the moat is not the metric. Anyone with a total-value toggle
already has this. What is left that is genuinely ours is the **punt-build machinery, the
expected-games column, and the replay harness that can grade any of it on real seasons** — and
the harness is arguably the most defensible asset in the project, since it is what caught both
of the errors above.

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
