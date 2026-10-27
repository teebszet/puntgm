"""Rest-of-season what-if: project a waiver add/drop swap's effect on H2H cat wins.

Models the remaining regular-season matchups week by week with the production projector
(``Projector.team_projection``). Lineups are auto-managed per week (both scenarios get the
same rule, so the delta is the swap, not a lineup assumption): the ten active slots are
filled greedily by per-game 9-cat z (prior-season pool) with Yahoo slot eligibility, from
every roster player whose modeled health is not OUT that week. BN/IL players price zero
remaining games until auto-activated — the platform semantics the projector uses.

Week 2 (the current matchup) is priced exactly like the daily digest: scheduled per-date
lineup reads, banked stats through the as-of date. A drop's already-banked stats stay with
the team (Yahoo weekly semantics), and the added player prices zero while OUT.

The added player's return timeline is modeled with ``--return-date`` (first date he may
play again) and ``--ramp-weeks`` (weeks counted at the QUESTIONABLE 0.5 scale).

Approximations, stated: no trades; participation rescale is off for known lineups (the
projector's convention); per-week health beyond the modeled timeline is today's as-of
statuses; slot eligibility uses the store's coarse NBA positions.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import types
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fantasy_gm.config import (  # noqa: E402
    CATEGORY_DIRECTION, PERCENTAGE_CATEGORIES, Config,
)
from fantasy_gm.data.store import Store  # noqa: E402
from fantasy_gm.engine.projection import Projector  # noqa: E402

# Yahoo starting slots for this league (stream_plan convention: BN/IL accrue nothing).
ACTIVE_SLOTS = ["PG", "SG", "G", "SF", "PF", "F", "C", "C", "Util", "Util"]

_COARSE_SLOTS = {
    "G": {"PG", "SG", "G", "Util"},
    "F": {"SF", "PF", "F", "Util"},
    "C": {"C", "Util"},
    "G-F": {"PG", "SG", "G", "SF", "PF", "F", "Util"},
    "F-G": {"PG", "SG", "G", "SF", "PF", "F", "Util"},
    "F-C": {"SF", "PF", "F", "C", "Util"},
    "C-F": {"SF", "PF", "F", "C", "Util"},
}


def player_name(store: Store, pid: str) -> str:
    row = store.conn.execute(
        "SELECT player_name FROM player_logs WHERE player_id = ? LIMIT 1", (pid,)
    ).fetchone()
    return row["player_name"] if row else pid


def coarse_position(store: Store, pid: str) -> str:
    row = store.conn.execute(
        """SELECT position FROM player_positions WHERE player_id = ?
           ORDER BY known_from DESC LIMIT 1""",
        (pid,),
    ).fetchone()
    return row["position"] if row else "F"


def pool_stats(store: Store, season: str, categories: list[str], min_games: int = 20):
    """Per-cat per-game mean/sd across a season's qualifying players (z reference pool)."""
    rows = store.conn.execute(
        "SELECT player_id, stats_json FROM player_logs WHERE season = ?", (season,)
    ).fetchall()
    per_player: dict[str, list[dict]] = {}
    for r in rows:
        per_player.setdefault(r["player_id"], []).append(json.loads(r["stats_json"]))
    means: dict[str, list[float]] = {c: [] for c in categories}
    for logs in per_player.values():
        if len(logs) < min_games:
            continue
        for c in categories:
            if c in PERCENTAGE_CATEGORIES:
                mk, at = PERCENTAGE_CATEGORIES[c]
                ats = sum(g.get(at, 0.0) for g in logs)
                vals = [sum(g.get(mk, 0.0) for g in logs) / ats] if ats else []
            else:
                vals = [g.get(c, 0.0) for g in logs]
            if vals:
                means[c].append(statistics.fmean(vals))
    mean = {c: statistics.fmean(v) for c, v in means.items() if v}
    sd = {c: statistics.pstdev(v) for c, v in means.items() if v}
    return mean, sd


def per_game_z(store: Store, pid: str, as_of: str, categories: list[str],
               pool_mean: dict, pool_sd: dict, window: int) -> float:
    dist, _ = store.player_distribution_with_n(pid, as_of, categories, window=window)
    return sum(
        (dist[c][0] - pool_mean[c]) / pool_sd[c]
        for c in categories if pool_sd.get(c, 0.0) > 0
    )


def patch_availability(store: Store, pid: str, timeline: list[tuple[str, str, str]]) -> None:
    """Override one player's availability with a modeled timeline [(from, until, status)].

    Dates inclusive; a ``None`` status falls through to the real availability table.
    """
    real = store.availability_asof

    def patched(player_id: str, as_of: str):
        if player_id == pid:
            for frm, until, status in timeline:
                if frm <= as_of <= until:
                    if status is None:
                        break
                    return types.SimpleNamespace(
                        player_id=pid, status=status, known_from=frm,
                        source="modeled", confidence=0.5, note="modeled return timeline",
                    )
        return real(player_id, as_of)

    store.availability_asof = patched  # type: ignore[method-assign]


def best_active_set(store: Store, roster: list[str], pid_z: dict[str, float],
                    as_of: str, slots: list[str] = ACTIVE_SLOTS) -> list[str]:
    """Fill the ten active slots greedily by z from every non-OUT roster player."""
    candidates: list[tuple[float, str]] = []
    for pid in roster:
        av = store.availability_asof(pid, as_of)
        if av is not None and av.status == "OUT":
            continue
        candidates.append((pid_z[pid], pid))
    candidates.sort(reverse=True)
    assigned: list[str] = []
    remaining = list(ACTIVE_SLOTS)
    # Specialty slots first, then Util.
    for slot in [s for s in slots if s != "Util"] + [s for s in slots if s == "Util"]:
        if slot not in remaining:
            continue
        for z, pid in candidates:
            if pid in assigned:
                continue
            slots_ok = _COARSE_SLOTS.get(coarse_position(store, pid), set())
            if slot in slots_ok:
                assigned.append(pid)
                remaining.remove(slot)
                break
    return assigned


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", default="yahoo-478.l.25733")
    ap.add_argument("--team", default="t.8")
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--add", required=True, help="player id to add")
    ap.add_argument("--drop", required=True, action="append",
                    help="player id dropped (repeatable)")
    ap.add_argument("--return-date", required=True,
                    help="first date the added player may play again (inclusive)")
    ap.add_argument("--ramp-weeks", type=int, default=1,
                    help="weeks after return counted at the QUESTIONABLE 0.5 scale")
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--healthy-from", default=None,
                    help="assume every roster player currently OUT is ACTIVE from this "
                         "date (sensitivity: models recoveries the news wire cannot "
                         "timeline); the added player keeps his modeled timeline")
    args = ap.parse_args()

    config = Config()
    store = Store(config.db_path)
    team = store.resolve_team_id(args.league, args.team)
    if team is None:
        print(f"unknown team {args.team!r}", file=sys.stderr)
        return 1
    cats = config.categories
    meta = store.league_meta(args.league)
    season = meta["season"]
    as_of = args.as_of

    rosters = store.rosters_asof(args.league, as_of)
    roster = list(rosters[team])
    if args.add in roster:
        print(f"{player_name(store, args.add)} is already on the roster", file=sys.stderr)
        return 1
    for d in args.drop:
        if d not in roster:
            print(f"drop candidate {player_name(store, d)} not on roster", file=sys.stderr)
            return 1

    weeks = store.conn.execute(
        """SELECT period_index, period_start, period_end, team_a, team_b FROM matchups
           WHERE league_id = ? AND (team_a = ? OR team_b = ?) AND period_end >= ?
           ORDER BY period_start""",
        (args.league, team, team, as_of),
    ).fetchall()
    if not weeks:
        print("no remaining matchups", file=sys.stderr)
        return 1

    prior = "2025-26" if season != "2025-26" else "2024-25"
    pool_mean, pool_sd = pool_stats(store, prior, cats)
    pool = {pid: per_game_z(store, pid, as_of, cats, pool_mean, pool_sd, args.window)
            for pid in roster + [args.add]}
    print(f"# per-game z ({prior} pool, last {args.window} games):")
    for z, pid in sorted((z, p) for p, z in pool.items()):
        print(f"#   {z:6.2f}  {player_name(store, pid)}")

    # Modeled health timeline for the added player.
    ret = date.fromisoformat(args.return_date)
    last_end = max(date.fromisoformat(w["period_end"]) for w in weeks)
    timeline: list[tuple[str, str, str]] = [
        (as_of, (ret - timedelta(days=1)).isoformat(), "OUT")]
    ramp = timedelta(weeks=args.ramp_weeks)
    if ramp > timedelta(0):
        timeline.append((ret.isoformat(),
                         min(ret + ramp - timedelta(days=1), last_end).isoformat(),
                         "QUESTIONABLE"))
        start_full = ret + ramp
    else:
        start_full = ret
    if start_full <= last_end:
        timeline.append((start_full.isoformat(), last_end.isoformat(), "ACTIVE"))
    patch_availability(store, args.add, timeline)
    if args.healthy_from:
        for pid in roster:
            if pid == args.add:
                continue
            av = store.availability_asof(pid, as_of)
            if av is not None and av.status == "OUT":
                patch_availability(
                    store, pid,
                    [(as_of, (date.fromisoformat(args.healthy_from)
                              - timedelta(days=1)).isoformat(), "OUT"),
                     (args.healthy_from, last_end.isoformat(), "ACTIVE")])

    proj = Projector(config)

    def weekly(w_row, scenario: bool, drop: str) -> dict[str, float]:
        ps, pe = w_row["period_start"], w_row["period_end"]
        opp = w_row["team_a"] if w_row["team_a"] != team else w_row["team_b"]
        is_current = ps <= as_of <= pe
        state = store.league_state_asof(args.league, as_of if is_current else ps)
        my_roster = list(roster)
        if scenario:
            my_roster.remove(drop)
            my_roster.append(args.add)
        opp_roster = state.rosters.get(opp, [])
        if is_current:
            # Digest path: scheduled per-date reads, banked through the as-of date.
            mine = proj.team_projection(
                store, state.category_tally.get(team, {}), my_roster,
                as_of, ps, pe, cats, season,
                per_day_games=store.scheduled_active_games(args.league, team, as_of, pe),
                league_id=args.league, team_id=team)
            opp_proj = proj.team_projection(
                store, state.category_tally.get(opp, {}), opp_roster,
                as_of, ps, pe, cats, season,
                active_ids=active_ids_plain(opp_roster, state, opp),
                league_id=args.league, team_id=opp)
        else:
            mine = proj.team_projection(
                store, {}, my_roster, ps, ps, pe, cats, season,
                active_ids=best_active_set(store, my_roster, pool, ps),
                league_id=args.league, team_id=team)
            opp_proj = proj.team_projection(
                store, {}, opp_roster, ps, ps, pe, cats, season,
                active_ids=opp_auto(opp_roster, ps),
                league_id=args.league, team_id=opp)
        out = {}
        for c in cats:
            mt, ms = mine[c]
            ot, os_ = opp_proj[c]
            diff = CATEGORY_DIRECTION[c] * (mt - ot)
            combined = math.hypot(ms, os_)
            if combined == 0.0:
                p = 1.0 if diff > 0 else (0.5 if diff == 0 else 0.0)
            else:
                p = 0.5 * (1.0 + math.erf((diff / combined) / math.sqrt(2)))
            out[c] = p
        return out

    def active_ids_plain(opp_roster, state, opp):
        lineup = store.lineup_asof(args.league, opp, as_of)
        if not lineup:
            return None
        on = set(opp_roster)
        return [pid for pid, slot in lineup.items()
                if slot not in {"BN", "IL", "IR", "NA", "INACT"} and pid in on]

    def opp_auto(opp_roster, ps):
        opp_pool = {}
        for pid in opp_roster:
            if pid not in pool:
                pool[pid] = per_game_z(store, pid, as_of, cats, pool_mean,
                                       pool_sd, args.window)
            opp_pool[pid] = pool[pid]
        return best_active_set(store, opp_roster, opp_pool, ps)

    drops = args.drop
    scenarios: dict[str, dict[int, dict[str, float]]] = {"baseline": {}}
    for d in drops:
        scenarios[f"drop {player_name(store, d)}"] = {}
    for w in weeks:
        scenarios["baseline"][w["period_index"]] = weekly(w, False, "")
        for d in drops:
            key = f"drop {player_name(store, d)}"
            scenarios[key][w["period_index"]] = weekly(w, True, d)

    labels = {w["period_index"]: f"{w['period_start']}..{w['period_end']} vs "
              f"{player_name(store, w['team_a'] if w['team_a'] != team else w['team_b'])}"
              for w in weeks}

    print(f"\n{'week':34s} " + " ".join(f"{k:>20s}" for k in scenarios))
    for w in weeks:
        i = w["period_index"]
        cells = []
        for key, per_week in scenarios.items():
            cats_won = sum(1 for c in cats if per_week[i][c] > 0.5)
            exp = sum(per_week[i][c] for c in cats)
            cells.append(f"{cats_won}/9 ({exp:4.2f})".rjust(20))
        print(f"{labels[i]:34s} " + " ".join(cells))

    print("\n# totals over remaining matchups (cats won with p>0.5, and Σp)")
    base_total = None
    for key, per_week in scenarios.items():
        cats_won = sum(sum(1 for c in cats if per_week[w][c] > 0.5) for w in per_week)
        exp = sum(sum(per_week[w][c] for c in cats) for w in per_week)
        line = f"{key:28s} cat-wins {cats_won:4d}  expected {exp:6.2f}"
        if key == "baseline":
            base_total = (cats_won, exp)
            print(line)
        else:
            flips = []
            for w in per_week:
                for c in cats:
                    b = scenarios["baseline"][w][c]
                    if (per_week[w][c] > 0.5) != (b > 0.5):
                        flips.append(f"w{w} {c} {b:.2f}->{per_week[w][c]:.2f}")
            print(line + f"  Δ {cats_won - base_total[0]:+d} cats, {exp - base_total[1]:+.2f} Σp"
                  + ("  flips: " + ", ".join(flips) if flips else ""))

    print(f"\n# {player_name(store, args.add)} ROS games (modeled: return "
          f"{args.return_date}, ramp {args.ramp_weeks}w)")
    for w in weeks:
        ps, pe = w["period_start"], w["period_end"]
        av = store.availability_asof(args.add, pe)
        if av is not None and av.status == "OUT":
            continue
        scale = 0.5 if (av is not None and av.status == "QUESTIONABLE") else 1.0
        nba = store.player_team(args.add, ps)
        rg = store.remaining_games_for_team(nba, ps, pe) if nba else 0
        slotted = args.add in best_active_set(
            store, ([p for p in roster if p not in args.drop] + [args.add]), pool, ps)
        if not slotted:
            rg = 0
        print(f"#   week {w['period_index']:2d} {ps}..{pe}: {rg} games x{scale}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
