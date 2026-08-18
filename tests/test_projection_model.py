"""The derived projection model: minutes/role, rates, availability, rookies (tasks 2.5-2.9).

The properties pinned here are the ones the `player-projections` requirements turn on, and
the ones a plausible-looking refactor would silently break: reads are forward-only, a role
change moves the projection, provisional output stays labeled, and the two uncertainties
stay distinct.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fantasy_gm.data.store import Store
from fantasy_gm.models import ForwardRoster, Game, IncomingPlayer, PlayerGameLog, UsageRole
from fantasy_gm.projections.availability import (
    fit_games,
    measure_games_production_correlation,
)
from fantasy_gm.projections.derived import DerivedProjectionSource
from fantasy_gm.projections.minutes import ew_mean, fit_minutes
from fantasy_gm.projections.rookies import fit_rookie_prior, slot_bucket
from fantasy_gm.projections.source import ProjectionBasis

SEASON = "2026-27"
PRIOR = "2025-26"
START = date(2025, 10, 21)
# The draft: after the prior season, before a game of the projected one is played. Forward
# roster and incoming-player records are dated before it so an as-of read can see them.
DRAFT_DAY = "2026-10-01"
KNOWN_FROM = "2026-08-01"

# Teams big enough that a rotation rank means something, and a pool big enough that the
# empirical-Bayes fits are identifiable rather than falling back.
TEAMS = ("AAA", "BBB", "CCC", "DDD")
PER_TEAM = 10
# A player with barely any history, to pin the behaviour the uncertainty terms exist for.
THIN = "AAA-thin"
THIN_GAMES = 6


def _line(minutes: float, **c) -> dict[str, float]:
    """A box line scaled off minutes, so per-minute rates are stable and rank is meaningful."""
    base = {
        "pts": minutes * 0.55, "reb": minutes * 0.18, "ast": minutes * 0.12,
        "stl": minutes * 0.03, "blk": minutes * 0.02, "fg3m": minutes * 0.07,
        "tov": minutes * 0.05, "fgm": minutes * 0.21, "fga": minutes * 0.45,
        "ftm": minutes * 0.10, "fta": minutes * 0.13,
    }
    base.update(c)
    return base


def _seed_league(store: Store, season: str = PRIOR, n_games: int = 40,
                 wobble: float = 1.0) -> str:
    """A season for four teams: minutes descend by depth, and players miss games.

    Absences matter — with everyone playing every night the availability model has no spread
    to fit and would report a certainty it has not earned.
    """
    import random

    rng = random.Random(11)
    for i in range(n_games):
        d = (START + timedelta(days=i)).isoformat()
        for t_index in range(0, len(TEAMS), 2):
            home, away = TEAMS[t_index], TEAMS[t_index + 1]
            gid = f"g{i}-{home}"
            store.upsert_games([Game(gid, season, d, home, away)])
            logs, usage = [], []
            for team in (home, away):
                roster = [f"{team}-{slot}" for slot in range(PER_TEAM)]
                if team == "AAA" and i < THIN_GAMES:
                    roster.append(THIN)
                for pid in roster:
                    slot = 4 if pid == THIN else int(pid.rsplit("-", 1)[1])
                    if rng.random() < 0.12:  # a night off
                        continue
                    minutes = max(34.0 - 3.0 * slot + rng.gauss(0, wobble), 2.0)
                    logs.append(PlayerGameLog(gid, season, d, pid, f"Player {pid}", team,
                                              _line(minutes)))
                    usage.append(UsageRole(pid, d, round(minutes, 1), minutes * 0.45,
                                           minutes >= 24, slot + 1))
            store.upsert_player_logs(logs)
            store.add_usage_role(usage)
    return (START + timedelta(days=n_games - 1)).isoformat()


@pytest.fixture
def store() -> Store:
    s = Store(":memory:")
    _seed_league(s)
    return s


# --- no lookahead (structural, not by convention) ----------------------------


def test_projection_reads_nothing_after_as_of(store):
    """The backtest requirement is that projecting a season uses no information from inside
    it. Adding a wildly different future changes nothing for an earlier as_of."""
    as_of = (START + timedelta(days=20)).isoformat()
    before = DerivedProjectionSource(store).project(SEASON, as_of)["AAA-9"]

    # A twelfth man suddenly plays 40 minutes a night — but only after the as-of date.
    for i in range(40, 60):
        d = (START + timedelta(days=i)).isoformat()
        gid = f"late{i}"
        store.upsert_games([Game(gid, PRIOR, d, "AAA", "BBB")])
        store.upsert_player_logs(
            [PlayerGameLog(gid, PRIOR, d, "AAA-9", "Player AAA-9", "AAA", _line(40.0))]
        )
        store.add_usage_role([UsageRole("AAA-9", d, 40.0, 18.0, True, 1)])

    after = DerivedProjectionSource(store).project(SEASON, as_of)["AAA-9"]
    assert after.notes["minutes"] == before.notes["minutes"]
    assert after.estimate("pts").per_game_mean == before.estimate("pts").per_game_mean


def test_backtest_source_is_fit_only_from_games_before_the_cut(store):
    """The fit itself — not just the per-player read — is built as-of, which is what makes
    the no-lookahead property structural."""
    early = fit_minutes(store, (START + timedelta(days=15)).isoformat())
    late = fit_minutes(store, (START + timedelta(days=39)).isoformat())
    assert early.n_players <= late.n_players
    assert early.as_of < late.as_of


# --- minutes and role (2.5) --------------------------------------------------


def test_a_role_change_moves_projected_minutes_and_production(store):
    """A player moved into a lead role projects for more minutes, and the per-game line
    follows — the requirement that prior-season rates alone are insufficient."""
    baseline = DerivedProjectionSource(store).project(SEASON, DRAFT_DAY, ["AAA-8"])["AAA-8"]

    store.add_forward_roster([ForwardRoster("AAA-8", SEASON, "CCC", 1, KNOWN_FROM)])
    promoted = DerivedProjectionSource(store).project(SEASON, DRAFT_DAY, ["AAA-8"])["AAA-8"]

    assert float(promoted.notes["minutes"]) > float(baseline.notes["minutes"])
    assert promoted.estimate("pts").per_game_mean > baseline.estimate("pts").per_game_mean
    assert promoted.notes["stated_depth"] == "1"


def test_a_role_change_reported_after_the_draft_is_invisible_on_draft_day(store):
    """Forward inputs are effective-dated; a depth chart published in November cannot have
    moved an October pick."""
    store.add_forward_roster([ForwardRoster("AAA-8", SEASON, "CCC", 1, "2026-11-15")])
    proj = DerivedProjectionSource(store).project(SEASON, DRAFT_DAY, ["AAA-8"])["AAA-8"]
    assert "stated_depth" not in proj.notes


def test_a_demotion_moves_projected_minutes_down(store):
    baseline = DerivedProjectionSource(store).project(SEASON, DRAFT_DAY, ["AAA-0"])["AAA-0"]
    store.add_forward_roster([ForwardRoster("AAA-0", SEASON, "CCC", 9, KNOWN_FROM)])
    demoted = DerivedProjectionSource(store).project(SEASON, DRAFT_DAY, ["AAA-0"])["AAA-0"]
    assert float(demoted.notes["minutes"]) < float(baseline.notes["minutes"])


def test_a_team_change_widens_the_band(store):
    """A trade says the situation moved, not which way — so the band grows. Compared at the
    same stated depth, so the only difference is the change of team itself."""
    store.add_forward_roster([ForwardRoster("AAA-3", SEASON, "AAA", 4, KNOWN_FROM)])
    stayed = DerivedProjectionSource(store).minutes_projection(SEASON, DRAFT_DAY, "AAA-3")

    moved_store = Store(":memory:")
    _seed_league(moved_store)
    moved_store.add_forward_roster([ForwardRoster("AAA-3", SEASON, "DDD", 4, KNOWN_FROM)])
    moved = DerivedProjectionSource(moved_store).minutes_projection(SEASON, DRAFT_DAY, "AAA-3")

    assert moved.team_changed and not stayed.team_changed
    assert moved.mean_stderr > stayed.mean_stderr


def test_thin_history_leans_on_the_stated_role_and_long_history_does_not(store):
    """Inverse-variance weighting, stated as behaviour: six games of history barely outweigh
    a stated role, a full season does."""
    store.add_forward_roster([
        ForwardRoster(THIN, SEASON, "AAA", 1, KNOWN_FROM),
        ForwardRoster("AAA-5", SEASON, "AAA", 1, KNOWN_FROM),
    ])
    src = DerivedProjectionSource(store)
    thin = src.minutes_projection(SEASON, DRAFT_DAY, THIN)
    settled = src.minutes_projection(SEASON, DRAFT_DAY, "AAA-5")
    assert thin.observed_games < settled.observed_games
    assert thin.role_weight > settled.role_weight


def test_recency_weighting_costs_effective_sample_size():
    """Leaning on recent games is not free — it has to reduce the confidence claimed."""
    values = [10.0] * 40
    flat_mean, flat_n = ew_mean(values, None)
    weighted_mean, weighted_n = ew_mean(values, 10.0)
    assert flat_mean == pytest.approx(weighted_mean)
    assert weighted_n < flat_n


def test_minutes_fit_labels_fallbacks_when_the_pool_is_too_thin():
    """A fit that could not be measured says so, rather than reporting a constant as data."""
    store = Store(":memory:")
    _seed_league(store, n_games=3)
    fit = fit_minutes(store, (START + timedelta(days=2)).isoformat())
    assert set(fit.basis.values()) == {"fallback"}


# --- rates conditioned on minutes (2.6) and the two uncertainties (2.8) ------


def test_production_variance_and_mean_uncertainty_are_separate_and_move_apart(store):
    """A short history and a long one differ in how well the mean is known, not in how
    volatile the player is game to game (A-DRAFT-2)."""
    proj = DerivedProjectionSource(store).project(SEASON, DRAFT_DAY, [THIN, "AAA-4"])
    thin, settled = proj[THIN].estimate("pts"), proj["AAA-4"].estimate("pts")
    assert thin.mean_stderr > settled.mean_stderr
    assert thin.per_game_std > 0 and settled.per_game_std > 0
    assert proj[THIN].notes["thin_history"].endswith("games")


def test_percentage_categories_are_projected_as_components_not_as_percentages(store):
    proj = DerivedProjectionSource(store).project(SEASON, DRAFT_DAY, ["AAA-0"])["AAA-0"]
    assert "fg_pct" not in proj.estimates and "ft_pct" not in proj.estimates
    assert proj.estimate("fga").per_game_mean > 0
    assert proj.percentage("fg_pct") == pytest.approx(
        proj.estimate("fgm").per_game_mean / proj.estimate("fga").per_game_mean
    )


# --- expected games played (2.7) ---------------------------------------------


def test_expected_games_is_a_separate_output_with_its_own_uncertainty(store):
    proj = DerivedProjectionSource(store).project(SEASON, DRAFT_DAY, ["AAA-1"])["AAA-1"]
    assert 0 < proj.expected_games <= 82
    assert proj.expected_games_std > 0


def test_a_less_available_player_projects_fewer_games(store):
    """Same per-game line, different availability, different season value (design D7)."""
    # Drop most of one player's games: unchanged rates, much worse availability.
    store.conn.execute(
        "DELETE FROM player_logs WHERE player_id = ? AND game_date > ?",
        ("BBB-1", (START + timedelta(days=13)).isoformat()),
    )
    store.conn.commit()
    src = DerivedProjectionSource(store)
    healthy = src.project(SEASON, DRAFT_DAY, ["AAA-1"])["AAA-1"]
    fragile = src.project(SEASON, DRAFT_DAY, ["BBB-1"])["BBB-1"]
    assert fragile.expected_games < healthy.expected_games


def test_games_fit_reports_whether_the_prior_was_measured(store):
    fit = fit_games(store, DRAFT_DAY)
    assert fit.basis in ("measured", "fallback")
    assert 0.0 < fit.pool_rate <= 1.0


def test_games_production_separability_is_measured_not_assumed(store):
    """A-DRAFT-7: the claim that value factorizes as E[games] x E[per-game] is checkable,
    and this is the check."""
    out = measure_games_production_correlation(store, PRIOR)
    assert "corr_games_minutes" in out and "corr_games_scoring" in out
    assert -1.0 <= out["corr_games_minutes"] <= 1.0


# --- rookies (2.9) -----------------------------------------------------------


def test_rookie_projection_is_labeled_prior_derived(store):
    """Nothing asserted may masquerade as measured — a rookie line says where it came from."""
    store.add_incoming_players(
        [IncomingPlayer("rook", SEASON, "Rook Ie", "2026-06-26", draft_pick=3, draft_team="AAA")]
    )
    proj = DerivedProjectionSource(store).project(SEASON, DRAFT_DAY, ["rook"])["rook"]
    assert proj.basis is ProjectionBasis.PRIOR
    assert proj.is_provisional
    assert proj.notes["prior"] == "draft-slot"
    assert proj.notes["prior_basis"] in ("fitted", "fallback")
    assert proj.notes["slot_bucket"] == "1-5"
    assert proj.estimate("pts").per_game_mean > 0


def test_a_higher_draft_slot_projects_more_minutes(store):
    store.add_incoming_players([
        IncomingPlayer("lottery", SEASON, "Lott Ery", "2026-06-26", draft_pick=2),
        IncomingPlayer("second", SEASON, "Sec Ond", "2026-06-26", draft_pick=45),
    ])
    proj = DerivedProjectionSource(store).project(SEASON, DRAFT_DAY, ["lottery", "second"])
    assert float(proj["lottery"].notes["minutes"]) > float(proj["second"].notes["minutes"])


def test_rookie_uncertainty_is_wider_than_an_established_player(store):
    """If the band is as wide as the signal, say so and let the optimizer price it."""
    store.add_incoming_players(
        [IncomingPlayer("rook", SEASON, "Rook Ie", "2026-06-26", draft_pick=10)]
    )
    proj = DerivedProjectionSource(store).project(SEASON, DRAFT_DAY, ["rook", "AAA-3"])
    assert proj["rook"].estimate("pts").mean_stderr > \
        proj["AAA-3"].estimate("pts").mean_stderr


def test_rookie_prior_falls_back_and_labels_it_when_no_cohort_exists(store):
    prior = fit_rookie_prior(store, SEASON, DRAFT_DAY, {})
    assert not prior.is_fitted
    assert all(v == "fallback" for v in prior.basis.values())
    assert slot_bucket(1) == "1-5" and slot_bucket(None) == "undrafted"


def test_rookie_prior_is_fitted_when_a_past_cohort_exists(store):
    """Once a prior cohort is in the store the slot→rank arrow is measured, not asserted."""
    ranks = {f"{t}-{s}": s + 1 for t in TEAMS for s in range(PER_TEAM)}
    store.add_incoming_players(
        [IncomingPlayer(f"{t}-{s}", PRIOR, f"P{t}{s}", "2025-06-26", draft_pick=3)
         for t in TEAMS for s in range(2)]
    )
    prior = fit_rookie_prior(store, SEASON, DRAFT_DAY, ranks)
    assert prior.basis["1-5"] == "fitted"
    assert prior.cohort_size["1-5"] >= 8


def test_a_manual_override_is_recorded_as_overridden(store):
    src = DerivedProjectionSource(
        store, overrides={"AAA-0": {"pts": 30.0, "minutes": 36.0, "games": 70.0}}
    )
    proj = src.project(SEASON, DRAFT_DAY, ["AAA-0"])["AAA-0"]
    assert proj.basis is ProjectionBasis.OVERRIDE
    assert proj.is_provisional
    assert proj.notes["override"] == "manual"
    assert proj.estimate("pts").per_game_mean == pytest.approx(30.0)
    assert proj.expected_games == pytest.approx(70.0)


# --- the source contract -----------------------------------------------------


def test_pool_includes_incoming_players_and_excludes_thin_history(store):
    """Rookies have no logs and the pool derives from logs; a six-game sample is not a
    draftable projection, but it is still projectable when asked for by name."""
    store.add_incoming_players(
        [IncomingPlayer("rook", SEASON, "Rook Ie", "2026-06-26", draft_pick=3)]
    )
    pool = DerivedProjectionSource(store).pool(SEASON, DRAFT_DAY)
    assert "rook" in pool and "AAA-0" in pool
    assert THIN not in pool
    assert THIN in DerivedProjectionSource(store).project(SEASON, DRAFT_DAY, [THIN])


def test_source_is_interchangeable_with_the_fixture_source(store):
    """The engine depends on the interface, not on which implementation supplied it."""
    from fantasy_gm.projections.fixture import FixtureProjectionSource
    from fantasy_gm.projections.source import ProjectionSource

    for src in (DerivedProjectionSource(store), FixtureProjectionSource({"AAA-0": {"pts": 1.0}})):
        assert isinstance(src, ProjectionSource)
        proj = src.project(SEASON, DRAFT_DAY, ["AAA-0"])["AAA-0"]
        assert proj.estimate("pts").per_game_mean >= 0
        assert proj.expected_games >= 0
