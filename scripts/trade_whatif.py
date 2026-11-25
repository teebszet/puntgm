"""Trade what-if: project a 2-for-2 (or N-for-N) trade's effect on H2H cat wins.

Same model as ros_whatif.py, generalized to multiple ins/outs:
- Future weeks (week start > as_of): both sides get auto-managed lineups
  (greedy per-game z, Yahoo slots, OUT players priced zero).
- Current week: the keep scenario uses the digest path (banked tally + per-day
  scheduled games as of today). The trade scenario assumes the deal processes on
  --trade-date: banked stats stay with the team that earned them (Yahoo weekly
  semantics), incoming players accrue only games from the trade date on
  (active_ids mode, health-scaled).
- Opponent rosters: real rosters + auto lineups. With --reflect, a drop landing on
  his new fantasy team flips that team's roster in the weeks it faces us (the
  counterfactual "we traded him strength").

Approximations, stated: no mid-week re-slotting inside future weeks; per-week
health is today's statuses (patch with --patch pid:STATUS:from to timeline a
return, e.g. an injured keep who comes back, or a traded player helping his new
team); participation rescale off for scheduled lineups (projector convention).
"""

from __future__ import annotations

import argparse
import math
import sys
import types
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fantasy_gm.config import (  # noqa: E402
    CATEGORY_DIRECTION, PERCENTAGE_CATEGORIES, Config,
)
from fantasy_gm.data.store import Store  # noqa: E402
from fantasy_gm.engine.projection import Projector  # noqa: E402
from ros_whatif import (  # noqa: E402
    ACTIVE_SLOTS, _COARSE_SLOTS, coarse_position, per_game_z, player_name,
    pool_stats,
)


def best_active_set(store: Store, roster: list[str], pid_z: dict[str, float],
                    as_of: str) -> list[str]:
    candidates: list[tuple[float, str]] = []
    for pid in roster:
        av = store.availability_asof(pid, as_of)
        if av is not None and av.status == "OUT":
            continue
        candidates.append((pid_z[pid], pid))
    candidates.sort(reverse=True)
    assigned: list[str] = []
    remaining = list(ACTIVE_SLOTS)
    for slot in [s for s in ACTIVE_SLOTS if s != "Util"] + [
            s for s in ACTIVE_SLOTS if s == "Util"]:
        if slot not in remaining:
            continue
        for z, pid in candidates:
            if pid in assigned:
                continue
            if slot in _COARSE_SLOTS.get(coarse_position(store, pid), set()):
                assigned.append(pid)
                remaining.remove(slot)
                break
    return assigned


def patch_availability(store: Store, pid: str,
                       timeline: list[tuple[str, str, str]]) -> None:
    real = store.availability_asof

    def patched(player_id: str, as_of: str):
        if player_id == pid:
            for frm, until, status in timeline:
                if frm <= as_of <= until:
                    if status is None:
                        break
                    return types.SimpleNamespace(
                        player_id=pid, status=status, known_from=frm,
                        source="modeled", confidence=0.5, note="modeled")
        return real(player_id, as_of)

    store.availability_asof = patched  # type: ignore[method-assign]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", default="yahoo-478.l.25733")
    ap.add_argument("--team", default="t.8")
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--trade-date", required=True,
                    help="date the trade processes; ins accrue games from here")
    ap.add_argument("--add", required=True, action="append")
    ap.add_argument("--drop", required=True, action="append")
    ap.add_argument("--patch", action="append", default=[],
                    help="pid:STATUS:from — model a return timeline on the real "
                         "statuses (applies to every scenario)")
    ap.add_argument("--window", type=int, default=10)
    args = ap.parse_args()

    config = Config()
    store = Store(config.db_path)
    team = store.resolve_team_id(args.league, args.team)
    cats = config.categories
    meta = store.league_meta(args.league)
    season = meta["season"]
    as_of, tdate = args.as_of, args.trade_date

    for pid_status_from in args.patch:
        pid, status, frm = pid_status_from.split(":")
        last = max(w["period_end"] for w in store.conn.execute(
            "SELECT period_end FROM matchups WHERE league_id=? ORDER BY period_end",
            (args.league,)).fetchall())
        patch_availability(store, pid,
                           [(as_of, (date.fromisoformat(frm)
                                     - timedelta(days=1)).isoformat(), "OUT"),
                            (frm, last, status)])

    rosters = store.rosters_asof(args.league, as_of)
    roster = list(rosters[team])
    for p in args.add + args.drop:
        if p not in roster and p not in args.add:
            print(f"drop candidate {player_name(store, p)} not on roster",
                  file=sys.stderr)
            return 1
    new_roster = [p for p in roster if p not in args.drop] + list(args.add)

    weeks = store.conn.execute(
        """SELECT period_index, period_start, period_end, team_a, team_b FROM matchups
           WHERE league_id = ? AND (team_a = ? OR team_b = ?) AND period_end >= ?
           ORDER BY period_start""",
        (args.league, team, team, as_of)).fetchall()

    prior = "2025-26" if season != "2025-26" else "2024-25"
    pool_mean, pool_sd = pool_stats(store, prior, cats)
    pool: dict[str, float] = {}
    for p in sorted(set(roster + new_roster
                        + [q for t2, ps in rosters.items() if t2 != team for q in ps])):
        pool[p] = per_game_z(store, p, as_of, cats, pool_mean, pool_sd, args.window)

    proj = Projector(config)

    def weekly(w_row, trade: bool, drop_ids: list[str], add_ids: list[str]) -> dict:
        ps, pe = w_row["period_start"], w_row["period_end"]
        opp = w_row["team_a"] if w_row["team_a"] != team else w_row["team_b"]
        is_current = ps <= as_of <= pe
        state = store.league_state_asof(args.league, as_of if is_current else ps)
        mine = new_roster if trade else roster
        opp_roster = state.rosters.get(opp, [])
        if trade and set(drop_ids) & set(opp_roster):
            # the drops landed on this opponent: reflect their new strength
            opp_roster = ([p for p in opp_roster if p not in add_ids]
                          + list(drop_ids))
        if is_current:
            if trade:
                mine = proj.team_projection(
                    store, state.category_tally.get(team, {}), mine, tdate, tdate, pe,
                    cats, season, active_ids=best_active_set(store, mine, pool, tdate),
                    league_id=args.league, team_id=team)
            else:
                mine = proj.team_projection(
                    store, state.category_tally.get(team, {}), mine, as_of, ps, pe,
                    cats, season,
                    per_day_games=store.scheduled_active_games(
                        args.league, team, as_of, pe),
                    league_id=args.league, team_id=team)
            opp_proj = proj.team_projection(
                store, state.category_tally.get(opp, {}), opp_roster, as_of, ps, pe,
                cats, season, active_ids=best_active_set(store, opp_roster, pool, as_of),
                league_id=args.league, team_id=opp)
        else:
            mine = proj.team_projection(
                store, {}, mine, ps, ps, pe, cats, season,
                active_ids=best_active_set(store, mine, pool, ps),
                league_id=args.league, team_id=team)
            opp_proj = proj.team_projection(
                store, {}, opp_roster, ps, ps, pe, cats, season,
                active_ids=best_active_set(store, opp_roster, pool, ps),
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

    scenarios = {
        "keep": dict(trade=False),
        "trade": dict(trade=True),
    }
    results = {k: {} for k in scenarios}
    for w in weeks:
        for key, cfg in scenarios.items():
            results[key][w["period_index"]] = weekly(
                w, cfg["trade"], args.drop, args.add)

    labels = {w["period_index"]: f"{w['period_start']}..{w['period_end']} vs "
              f"{player_name(store, w['team_a'] if w['team_a'] != team else w['team_b'])}"
              for w in weeks}

    print(f"\n{'week':34s} {'keep':>14s} {'trade':>14s}")
    for w in weeks:
        i = w["period_index"]
        cells = []
        for key in ("keep", "trade"):
            pw = results[key][i]
            won = sum(1 for c in cats if pw[c] > 0.5)
            exp = sum(pw[c] for c in cats)
            cells.append(f"{won}/9 ({exp:4.2f})".rjust(14))
        print(f"{labels[i]:34s} " + " ".join(cells))

    print("\n# totals over remaining matchups")
    for key in ("keep", "trade"):
        won = sum(sum(1 for c in cats if results[key][w][c] > 0.5)
                  for w in results[key])
        exp = sum(sum(results[key][w][c] for c in cats) for w in results[key])
        print(f"{key:28s} cat-wins {won:4d}  expected {exp:6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
