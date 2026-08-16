"""The ProjectionSource contract, the fixture source, and the replay oracle.

These pin the interface both parallel tracks code against: the draft engine (Track A)
and the derived projection model (Track B).
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from fantasy_gm.config import Config, default_data_dir
from fantasy_gm.data.store import Store
from fantasy_gm.models import ADP, ForwardRoster, Game, IncomingPlayer, PlayerGameLog, Transaction
from fantasy_gm.projections.actuals import ActualsProjectionSource, LookaheadError
from fantasy_gm.projections.fixture import FixtureProjectionSource
from fantasy_gm.projections.source import (
    ProjectionBasis,
    percentage_components,
    projected_stat_keys,
)

SEASON = "2026-27"
PRIOR = "2025-26"


def _line(**c):
    base = {k: 0.0 for k in ("pts", "reb", "ast", "stl", "blk", "fg3m", "tov",
                             "fgm", "fga", "ftm", "fta")}
    base.update(c)
    return base


# --- the contract ------------------------------------------------------------


def test_projected_keys_exclude_percentage_categories_and_include_components():
    """Percentage cats are derived from volume (A8), never projected directly — a source
    that emitted a bare fg_pct would be averaging per-game percentages, which is wrong."""
    keys = projected_stat_keys()
    assert "fg_pct" not in keys and "ft_pct" not in keys
    for component in percentage_components():
        assert component in keys
    assert "pts" in keys and "tov" in keys


def test_percentage_is_volume_weighted_not_mean_of_percentages():
    src = FixtureProjectionSource({"p": {"fgm": 5.0, "fga": 10.0, "ftm": 3.0, "fta": 4.0}})
    proj = src.project(SEASON, "2026-10-01")["p"]
    assert proj.percentage("fg_pct") == pytest.approx(0.5)
    assert proj.percentage("ft_pct") == pytest.approx(0.75)
    assert proj.percentage("pts") is None


def test_percentage_with_zero_attempts_is_none_not_zero():
    """Zero attempts is undefined, not 0% — collapsing it would drag a shooter's value down."""
    src = FixtureProjectionSource({"p": {"fgm": 0.0, "fga": 0.0}})
    assert src.project(SEASON, "2026-10-01")["p"].percentage("fg_pct") is None


def test_missing_estimate_is_explicit_zero_not_keyerror():
    src = FixtureProjectionSource({"p": {"pts": 20.0}})
    proj = src.project(SEASON, "2026-10-01")["p"]
    assert proj.estimate("nonexistent_key").per_game_mean == 0.0


def test_season_total_uses_expected_games_not_a_fixed_82():
    """Value is E[games] x per-game (design D7): equal rates, different availability,
    different season value."""
    src = FixtureProjectionSource(
        {"iron": {"pts": 20.0}, "fragile": {"pts": 20.0}},
        games={"iron": 78.0, "fragile": 40.0},
    )
    proj = src.project(SEASON, "2026-10-01")
    assert proj["iron"].season_total("pts") == pytest.approx(1560.0)
    assert proj["fragile"].season_total("pts") == pytest.approx(800.0)


def test_production_variance_and_mean_uncertainty_are_separate_terms():
    """A short history and a long one can share a mean and a game-to-game spread while
    differing in how well the mean is known (assumptions A-DRAFT-2)."""
    src = FixtureProjectionSource(
        {"veteran": {"pts": 20.0}, "rookie": {"pts": 20.0}},
        stds={"veteran": {"pts": 6.0}, "rookie": {"pts": 6.0}},
        mean_stderrs={"veteran": {"pts": 0.3}, "rookie": {"pts": 4.0}},
    )
    proj = src.project(SEASON, "2026-10-01")
    vet, rook = proj["veteran"].estimate("pts"), proj["rookie"].estimate("pts")
    assert vet.per_game_mean == rook.per_game_mean
    assert vet.per_game_std == rook.per_game_std
    assert rook.mean_stderr > vet.mean_stderr


def test_provisional_basis_is_flagged():
    """Nothing asserted may masquerade as measured (project standing rule)."""
    modeled = FixtureProjectionSource({"p": {"pts": 1.0}}, basis=ProjectionBasis.MODELED)
    prior = FixtureProjectionSource({"p": {"pts": 1.0}}, basis=ProjectionBasis.PRIOR)
    override = FixtureProjectionSource({"p": {"pts": 1.0}}, basis=ProjectionBasis.OVERRIDE)
    assert not modeled.project(SEASON, "2026-10-01")["p"].is_provisional
    assert prior.project(SEASON, "2026-10-01")["p"].is_provisional
    assert override.project(SEASON, "2026-10-01")["p"].is_provisional


def test_pool_and_restriction():
    src = FixtureProjectionSource({"a": {"pts": 1.0}, "b": {"pts": 2.0}})
    assert src.pool(SEASON, "2026-10-01") == ["a", "b"]
    assert list(src.project(SEASON, "2026-10-01", ["b"])) == ["b"]
    assert src.project_one("a", SEASON, "2026-10-01").player_id == "a"
    assert src.project_one("missing", SEASON, "2026-10-01") is None


# --- the replay oracle (design D11) ------------------------------------------


def _seed_season(store: Store, season: str, pid: str = "p1", n: int = 20, pts: float = 20.0):
    base = date(2025, 10, 20)
    for i in range(n):
        d = (base + timedelta(days=i)).isoformat()
        store.upsert_games([Game(f"g{i}", season, d, "X", "Y")])
        store.upsert_player_logs(
            [PlayerGameLog(f"g{i}", season, d, pid, "P One", "X", _line(pts=pts, fgm=8, fga=16))]
        )
    return (base + timedelta(days=n - 1)).isoformat()


def test_actuals_source_projects_realized_production():
    store = Store(":memory:")
    end = _seed_season(store, PRIOR)
    src = ActualsProjectionSource(store, PRIOR)
    proj = src.project(PRIOR, end)["p1"]
    assert proj.expected_games == 20
    assert proj.estimate("pts").per_game_mean == pytest.approx(20.0)
    assert proj.basis is ProjectionBasis.ACTUALS
    # realized production is known exactly, so there is no error in the mean
    assert proj.estimate("pts").mean_stderr == 0.0


def test_actuals_source_refuses_a_season_still_in_progress():
    """The oracle is deliberate lookahead; using it mid-season would silently leak the
    future into a live path."""
    store = Store(":memory:")
    _seed_season(store, PRIOR)
    src = ActualsProjectionSource(store, PRIOR)
    with pytest.raises(LookaheadError):
        src.project(PRIOR, "2025-11-01")


def test_actuals_source_is_bound_to_one_season():
    store = Store(":memory:")
    _seed_season(store, PRIOR)
    with pytest.raises(ValueError):
        ActualsProjectionSource(store, PRIOR).project("2024-25", "2030-01-01")


def test_actuals_source_is_flagged_replay_only():
    assert ActualsProjectionSource(Store(":memory:"), PRIOR).replay_only is True


# --- forward-season store inputs ---------------------------------------------


def test_forward_roster_is_effective_dated():
    """A trade reported after draft day must be invisible to a draft-day read."""
    store = Store(":memory:")
    store.add_forward_roster([
        ForwardRoster("p1", SEASON, "OLD", 1, "2026-08-01"),
        ForwardRoster("p1", SEASON, "NEW", 2, "2026-09-15"),
    ])
    assert store.forward_roster_asof("p1", SEASON, "2026-09-01").team == "OLD"
    assert store.forward_roster_asof("p1", SEASON, "2026-09-20").team == "NEW"
    assert store.forward_roster_asof("p1", SEASON, "2026-07-01") is None


def test_transactions_are_effective_dated_and_filterable():
    store = Store(":memory:")
    store.add_transactions([
        Transaction("p1", SEASON, "trade", "2026-07-05", from_team="A", to_team="B"),
        Transaction("p2", SEASON, "signing", "2026-09-20", to_team="C"),
    ])
    assert len(store.transactions_asof(SEASON, "2026-08-01")) == 1
    assert len(store.transactions_asof(SEASON, "2026-10-01")) == 2
    assert len(store.transactions_asof(SEASON, "2026-10-01", player_id="p2")) == 1


def test_incoming_players_appear_in_the_draft_pool_without_game_logs():
    """Rookies have no logs, and the pool derives from logs — without this they would be
    undraftable (assumptions A-DRAFT-6)."""
    store = Store(":memory:")
    _seed_season(store, PRIOR, pid="vet")
    store.add_incoming_players([
        IncomingPlayer("rook", SEASON, "Rook Ie", "2026-06-26", draft_pick=3, draft_team="D"),
    ])
    pool = store.draft_pool_asof(SEASON, "2026-10-01")
    assert "vet" in pool and "rook" in pool
    # ...and not before they were known
    assert "rook" not in store.draft_pool_asof(SEASON, "2026-06-01")


def test_adp_absence_is_explicit_rather_than_a_default():
    store = Store(":memory:")
    store.add_adp([ADP("p1", SEASON, 12.5, "yahoo", "2026-09-01", pct_drafted=0.99)])
    adp = store.adp_asof(SEASON, "2026-10-01")
    assert adp["p1"].adp == pytest.approx(12.5)
    assert "p2" not in adp  # caller must decide, not inherit a default


def test_adp_takes_the_latest_known_value():
    store = Store(":memory:")
    store.add_adp([
        ADP("p1", SEASON, 30.0, "yahoo", "2026-08-01"),
        ADP("p1", SEASON, 12.0, "yahoo", "2026-09-25"),
    ])
    assert store.adp_asof(SEASON, "2026-09-01")["p1"].adp == pytest.approx(30.0)
    assert store.adp_asof(SEASON, "2026-10-01")["p1"].adp == pytest.approx(12.0)


# --- shared store location ---------------------------------------------------


def test_data_dir_env_override(monkeypatch, tmp_path):
    """Parallel worktrees share one backfilled store instead of each re-backfilling."""
    monkeypatch.setenv("FANTASY_GM_DATA_DIR", str(tmp_path / "shared"))
    assert default_data_dir() == tmp_path / "shared"
    assert Config().db_path == tmp_path / "shared" / "fantasy_gm.sqlite"


def test_data_dir_defaults_to_local_data(monkeypatch):
    monkeypatch.delenv("FANTASY_GM_DATA_DIR", raising=False)
    assert default_data_dir().name == "data"
    assert Config().data_dir.name == "data"


def test_data_dir_ignores_empty_env(monkeypatch):
    monkeypatch.setenv("FANTASY_GM_DATA_DIR", "")
    assert default_data_dir().name == "data"


def test_config_reads_env_at_construction_not_import(monkeypatch, tmp_path):
    """default_factory, not a module-level constant — otherwise setting the env var after
    import would silently have no effect."""
    monkeypatch.setenv("FANTASY_GM_DATA_DIR", str(tmp_path / "late"))
    assert Config().data_dir == tmp_path / "late"
    assert os.environ["FANTASY_GM_DATA_DIR"] == str(tmp_path / "late")
