# Discussion — round 0 (scoping interview + competitive scan, 2026-08-12 → 2026-08-14)

## Origin

The user asked to start on a draft tool, for two reasons stated up front: usable for their own draft
in October 2026, and able to capture attention during September mock-draft season. Those two goals
pull in different directions, and most of the interview was about whether one artifact can serve both.

## Competitive scan

- **Basketball Monster** — the serious grinder's tool. Draft Tracker with `DynV` (value recomputed
  against the *remaining pool* as picks come off), `PosV`, categorical scarcity, punt analysis via
  category checkboxes. Live import from Yahoo/ESPN/CBS/Fantrax. Friction: league settings and display
  columns must be pre-configured; offline drafts require manual checkbox logging while
  cross-referencing two other pages.
- **Hashtag Basketball** — the popular free tier. Custom z-score rankings, punt tool, schedule grid,
  trade analyzer. Broad platform support.
- **FantasyPros Draft Wizard** — owns the September mock window. Fast mocks, redo-from-any-pick,
  60+ expert consensus cheat sheets, Pick Predictor (survival odds), live sync, keeper support.
- **AI entrants** — thin. A $39 static draft kit, a Yahoo-connected in-season advisor, and ChatGPT
  wrappers with no data layer. No defensible AI draft product exists yet.

**The common thread: every one of them values players in season-long z-score space, and every one of
them makes punting a manual checkbox.**

## The finding that reframed the change

An initial list of gaps included availability/games-played modeling. The user pushed back — correctly
— that incumbents already fold projected games played into their projections and offer total-value
rankings, so that is not an open gap. They asked for better ideas and for something generic across
league formats.

Searching the literature rather than the market turned up Zach Rosenof's work:

- **[arXiv 2307.02188](https://arxiv.org/abs/2307.02188)** — Z-score is a special case of a more
  general metric under the assumption that future performance is known exactly. G-score corrects it
  with a period-to-period variance term. Z-score drafter in a G-score field: 0.4–1.1% win rate vs an
  8.3% baseline. G-score drafter in a Z-score field: 32.5%.
- **[arXiv 2409.09884](https://arxiv.org/abs/2409.09884)** — H-scoring / `H₀`: dynamic,
  roster-conditional, positional assignment via Jonker-Volgenant, gradient descent over category
  weights. Beats G-score at 21.8% / 37.7%. **Punting emerges implicitly.**
- **[arXiv 2501.00933](https://arxiv.org/pdf/2501.00933)** — roto needs a different objective.

Nobody has shipped any of it. That converts the pitch from "we invented something" (hard to defend)
to "we shipped the method the literature already validated, and proved it on real data" (easy to
defend, and squarely the project's existing track-record-is-the-content thesis).

`H₀`'s stated future work — per-player variance forecasting, week-to-week category correlations,
opponent-strategy adaptation, waiver-wire integration — is a description of this repo. That is what
makes it the right bet here specifically rather than a good idea in general.

## Decisions taken in the interview

| Question | Decision |
|---|---|
| v1 scope | Live co-pilot **and** mock, one engine, two entry points |
| Surface | CLI now, web before September |
| How far up the ladder | **Straight to H₀** (not G-score first) |
| Formats | 9-cat H2H snake only |
| Projections | **Own-built from day one** — no licensed source |
| Opponent model | ADP-driven bots |
| Pick ingestion | Yahoo draft API sync |
| Validation | **In scope — it's the whole pitch** |

## Open tensions recorded, not resolved

**1. This is the opposite of the 2026-08-12 GTM call.** That decision was explicit: *"Cheapest thing
that can face paid traffic by mid-September: a free draft ranker / punt-build tool built on the
existing `valuation.py` z-scores... Decouple it from the waiver engine's readiness — coupling them
costs the September window."* This change couples the September surface to a novel optimizer, an
own-built projection system, and live OAuth.

Raised with the user; they chose the ambitious path anyway. The mitigations are structural rather
than rhetorical: design D11 removes the projection dependency from the engine's critical path, and
D2 means the G-score fallback falls out of the same code whether or not it is shipped. If the
calendar bites, the cut is projection sophistication (tasks 2.5–2.9), not the engine.

**2. Own-built projections are the highest-risk item and are unmeasured.** A superior optimizer fed
bad means loses to a z-score tool fed good means. A-DRAFT-5 sets an explicit gate: if the method
cannot beat naive prior-season carry-forward on minutes MAE, it is not ready. Note this requires a
2024-25 backfill the project does not currently hold (task 2.10).

**3. Rookies are a structural gap, not a modeling gap.** A store of NBA box scores cannot project a
player with no NBA games. v1 uses a draft-slot prior plus manual overrides, both explicitly labeled.

**4. Licensing forced the `ProjectionSource` seam.** FantasyPros' API ($8.99/mo) is competent but
*personal, non-commercial only* — legitimate for the user's own draft, unusable in a public product.
ESPN's fantasy endpoints are undocumented and unlicensed. DARKO is the best free rate source but its
terms are unstated. Own-built is the only path with no exposure, and it is what ships; the interface
keeps the others usable for private evaluation and testing.

**5. Yahoo's live path is unproven here.** `draft_results` is documented to return picks made so far
during a draft, so the capability exists. But the fetch layer in this project has never run against a
live token. Manual entry is a required fallback (spec, not preference), and task 4.8 requires a
dry-run against a real Yahoo mock before draft day.

## Dependency note

The Yahoo fetch layer, player crosswalk, and strategy-baseline replay work are in flight on a
parallel worktree branch and are not present in this branch. This change **consumes** them
(tasks 4.1, 4.2, and A-DRAFT-8's validation) and must not rebuild them. Sequencing against that
branch is the first coordination point before implementation starts.
