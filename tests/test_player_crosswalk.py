"""Provider-id -> NBA-id joining, and its refusal to guess."""

from __future__ import annotations

from fantasy_gm.data.player_crosswalk import build_crosswalk, normalize_name
from fantasy_gm.data.store import Store
from fantasy_gm.models import PlayerGameLog

SEASON = "2025-26"


def _store(names: dict[str, str]) -> Store:
    store = Store(":memory:")
    store.upsert_player_logs([
        PlayerGameLog(f"g{i}", SEASON, "2025-11-01", pid, name, "LAL", {"pts": 10.0})
        for i, (pid, name) in enumerate(names.items())
    ])
    return store


def test_normalization_absorbs_punctuation_accents_and_suffixes():
    assert normalize_name("D'Angelo Russell") == normalize_name("DAngelo Russell")
    assert normalize_name("Nikola Jokić") == normalize_name("Nikola Jokic")
    assert normalize_name("Kelly Oubre Jr.") == normalize_name("Kelly Oubre")
    assert normalize_name("Karl-Anthony Towns") == normalize_name("Karl Anthony Towns")
    assert normalize_name("Jaren Jackson Jr.") == "jaren jackson"


def test_suffix_stripping_never_empties_a_short_name():
    """'V' is a suffix token, but a two-word name must survive intact."""
    assert normalize_name("Player V") == "player v"


def test_exact_normalised_names_match():
    store = _store({"201939": "Stephen Curry", "1626157": "Karl-Anthony Towns"})
    cw = build_crosswalk(store, SEASON, {"y1": "Stephen Curry", "y2": "Karl Anthony Towns"})
    assert cw.ok
    assert cw.mapping == {"y1": "201939", "y2": "1626157"}


def test_unknown_player_is_reported_not_guessed():
    store = _store({"201939": "Stephen Curry"})
    cw = build_crosswalk(store, SEASON, {"y9": "Steph Currie"})
    assert not cw.ok
    assert cw.mapping == {}
    assert cw.unmatched == [("y9", "Steph Currie")]


def test_shared_name_is_ambiguous_rather_than_arbitrarily_resolved():
    store = _store({"111": "Marcus Williams", "222": "Marcus Williams"})
    cw = build_crosswalk(store, SEASON, {"y1": "Marcus Williams"})
    assert not cw.ok
    assert cw.mapping == {}
    assert cw.ambiguous[0][0] == "y1"
    assert sorted(cw.ambiguous[0][2]) == ["111", "222"]


def test_override_resolves_a_miss_without_weakening_the_matcher():
    store = _store({"111": "Marcus Williams", "222": "Marcus Williams"})
    cw = build_crosswalk(store, SEASON, {"y1": "Marcus Williams"}, overrides={"y1": "222"})
    assert cw.ok and cw.mapping == {"y1": "222"}
