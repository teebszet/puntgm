"""Map a fantasy provider's player IDs onto the NBA.com player IDs the store is keyed by.

Yahoo (and every other provider) issues its own player IDs, while ``player_logs`` is keyed
by NBA.com's. Nothing downstream works until the two are joined, and the join is on names
that differ in punctuation, accents, and suffix conventions ("Kelly Oubre Jr." vs "Kelly
Oubre", "Nikola Jokic" vs "Nikola Jokić", "D'Angelo Russell" vs "DAngelo Russell").

The rule here is **never guess silently**. A fuzzy matcher that quietly picks the closest
name will map a fringe player onto a star a few times per league and corrupt the replay in
a way no aggregate metric would reveal. So this resolves only exact normalised matches,
and returns everything else — unmatched *and* ambiguous — as data for the caller to handle
explicitly (fail the import, prompt the user, or record provenance).

Normalisation is deliberately conservative: casefold, strip accents, drop punctuation, and
strip generational suffixes. It does not attempt nicknames or initials.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
# Apostrophes are *deleted* rather than spaced: providers disagree on whether the name is
# "D'Angelo Russell" or "DAngelo Russell", and spacing them would split one into two words
# while the other stays joined. Remaining punctuation (hyphens, periods) becomes a space,
# so "Karl-Anthony" and "Karl Anthony" agree.
_APOSTROPHE = re.compile(r"['‘’ʼ`]")
_PUNCT = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Casefold, strip accents/punctuation, and drop a trailing generational suffix."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _PUNCT.sub(" ", _APOSTROPHE.sub("", s.lower()))
    parts = [p for p in _WS.sub(" ", s).strip().split(" ") if p]
    while len(parts) > 2 and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts)


@dataclass
class Crosswalk:
    mapping: dict[str, str] = field(default_factory=dict)      # provider id -> nba id
    unmatched: list[tuple[str, str]] = field(default_factory=list)   # (provider id, name)
    ambiguous: list[tuple[str, str, list[str]]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unmatched and not self.ambiguous

    def report(self) -> str:
        lines = [f"matched {len(self.mapping)}"]
        if self.unmatched:
            lines.append(f"unmatched {len(self.unmatched)}: "
                         + ", ".join(n for _i, n in self.unmatched[:10])
                         + (" …" if len(self.unmatched) > 10 else ""))
        if self.ambiguous:
            lines.append(f"ambiguous {len(self.ambiguous)}: "
                         + ", ".join(f"{n} -> {ids}" for _i, n, ids in self.ambiguous[:5]))
        return "; ".join(lines)


def store_name_index(store, season: str) -> dict[str, list[str]]:
    """Normalised name -> NBA player ids seen in ``season``. A list, because distinct
    players genuinely do share a name and that must stay visible."""
    index: dict[str, list[str]] = {}
    for r in store.conn.execute(
        "SELECT DISTINCT player_id, player_name FROM player_logs WHERE season = ?", (season,)
    ):
        index.setdefault(normalize_name(r["player_name"]), []).append(r["player_id"])
    return index


def build_crosswalk(
    store, season: str, provider_players: dict[str, str],
    overrides: dict[str, str] | None = None,
) -> Crosswalk:
    """Join ``{provider_player_id: display_name}`` to NBA ids for ``season``.

    ``overrides`` maps provider id -> NBA id and wins over name matching, so an operator
    can resolve the handful of genuine misses once instead of weakening the matcher for
    everyone.
    """
    index = store_name_index(store, season)
    overrides = overrides or {}
    cw = Crosswalk()

    for pid, name in provider_players.items():
        if pid in overrides:
            cw.mapping[pid] = overrides[pid]
            continue
        hits = index.get(normalize_name(name), [])
        if len(hits) == 1:
            cw.mapping[pid] = hits[0]
        elif len(hits) > 1:
            cw.ambiguous.append((pid, name, hits))
        else:
            cw.unmatched.append((pid, name))
    return cw
