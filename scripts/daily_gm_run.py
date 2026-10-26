"""Daily in-season GM run (Tim, fantasy-nba-gm, 2026-10-26).

Runs once a day after the day's games end (launchd com.tim.buzz.fantasy-gm-daily,
14:30 HKT -- the last NBA game ends ~13:00 HKT). One run:

  1. refreshes every wire (statuses, news, roster moves, scheduled lineups,
     ESPN schedule, the day's box-score backfill) -- the same wires the draft
     watcher runs, through the shared run_refresh_wires();
  2. projects the current matchup per category (all nine, banked + remaining,
     Yahoo scheduled lineups priced);
  3. runs the waiver add analysis (WireAnalyzer);
  4. publishes: a markdown digest posted to the league channel, plus
     daily.md / daily_state.json in the watch dir for the record.

The channel post dedups per date via daily_state.json, so a manual re-run never
double-posts. Everything else is idempotent upserts, so a re-run just refreshes.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WATCH_DIR = Path("/Users/tim/.buzz/OUTBOX/fantasy-gm-watch")
BUZZ = "/Users/tim/.local/bin/buzz"
CIRI_ENV = "/Users/tim/.buzz/.buzz-ciri.env"
DEFAULT_CHANNEL = "8e4cd896-62a9-4a6f-93c8-df4da6f0d6a3"  # #fantasy-nba-gm


def send_to_channel(channel: str, text: str) -> bool:
    """Post the digest as Ciri. Content goes through a temp file so nothing is lost
    to shell quoting; env comes from the 600 env file (launchd has no BUZZ_* vars)."""
    if not (os.path.exists(BUZZ) and os.path.exists(CIRI_ENV)):
        print(f"buzz send skipped: missing {BUZZ} or {CIRI_ENV}", file=sys.stderr)
        return False
    tmp = WATCH_DIR / ".daily_digest_msg.txt"
    tmp.write_text(text)
    cmd = (
        f'. "{CIRI_ENV}" && exec "{BUZZ}" messages send '
        f"--channel {channel} --content - < '{tmp}'"
    )
    try:
        proc = subprocess.run(
            ["/bin/bash", "-c", cmd], capture_output=True, text=True, timeout=120
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"buzz send failed: {exc}", file=sys.stderr)
        return False
    if proc.returncode != 0:
        print(f"buzz send failed rc={proc.returncode}: {proc.stderr.strip()}",
              file=sys.stderr)
        return False
    tmp.unlink(missing_ok=True)
    return True


def render_digest(proj, wa, wire_lines: list[str], opp_name: str, my_name: str) -> str:
    cats = sorted(proj.categories.values(), key=lambda p: -p.win_prob)
    lines = [
        f"🏀 **Daily GM run — {proj.as_of}** "
        f"(week {proj.period_index}: {my_name} vs {opp_name})",
        "",
        "**Win projections — all 9 cats** (banked through "
        f"{proj.as_of} + remaining games priced off Yahoo's scheduled lineups):",
        "",
        "| cat | mine | opp | win | call |",
        "|---|---|---|---|---|",
    ]
    for p in cats:
        lines.append(
            f"| {p.category} | {p.mine_total:.1f} | {p.opp_total:.1f} "
            f"| {p.win_prob:.2f} | {p.label} |"
        )
    contested = proj.contested()
    if contested:
        lines += ["", f"Live cats: **{', '.join(contested)}** — the rest are decided."]
    else:
        lines += ["", "No contested cats left this week."]
    lines += ["", "**Waiver add analysis** (WireAnalyzer, contested cats first):", ""]
    if wa.options:
        for o in wa.options:
            if o.verdict == "infeasible":
                lines.append(f"- `{o.category}` — no available add improves it")
            else:
                conc = ", ".join(f"{c} {d:+.2f}" for c, d in o.concedes.items()) or "nothing"
                lines.append(
                    f"- `{o.category}` {o.verdict}: add **{o.add_name}** (+{o.gain:.2f}); "
                    f"concedes {conc}; net {o.net_categories:+d} cats"
                )
    else:
        lines.append("- no contested categories — nothing on the wire is worth a drop")
    if wire_lines:
        lines += ["", "<details><summary>refresh log</summary>", "", "```"]
        lines += wire_lines
        lines += ["```", "</details>"]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", default="yahoo-478.l.25733")
    ap.add_argument("--team", default="t.8", help="short (t.8) or full team id")
    ap.add_argument("--channel", default=DEFAULT_CHANNEL)
    ap.add_argument("--as-of", default=None, help="decision date (default: today UTC)")
    ap.add_argument("--no-send", action="store_true", help="refresh + digest, no post")
    ap.add_argument("--force-send", action="store_true", help="post even if already sent today")
    args = ap.parse_args()

    os.chdir(REPO)
    sys.path.insert(0, str(REPO))
    from fantasy_gm.config import Config
    from fantasy_gm.data.store import Store
    from fantasy_gm.engine.projection import Projector
    from fantasy_gm.engine.wire import WireAnalyzer

    os.environ.setdefault("TZ", "UTC")
    time.tzset()
    today = args.as_of or time.strftime("%Y-%m-%d", time.gmtime())
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    state_path = WATCH_DIR / "daily_state.json"
    state: dict = {}
    if state_path.exists():
        state = json.loads(state_path.read_text())

    config = Config()
    store = Store(config.db_path)
    try:
        team = store.resolve_team_id(args.league, args.team)
        if team is None:
            print(f"unknown team {args.team!r} in {args.league!r}", file=sys.stderr)
            return 1

        # 1. refresh every wire (shared with the draft watcher)
        wire_lines: list[str] = []
        result = _run_refresh(store, args.league, wire_lines)

        # 2. per-cat projections (all nine)
        proj = Projector().project(store, args.league, team, today)
        if not proj.opponent_id:
            print(f"no active matchup for {team} on {today}", file=sys.stderr)
            digest = f"🏀 Daily GM run — {today}: no active matchup in the store."
        else:
            # 3. waiver add analysis
            wa = WireAnalyzer(config).analyze(store, args.league, team, today)
            names = {
                r["team_id"]: r["team_name"]
                for r in store.conn.execute(
                    "SELECT team_id, team_name FROM teams WHERE league_id = ?",
                    (args.league,),
                ).fetchall()
            }
            digest = render_digest(
                proj, wa, wire_lines,
                names.get(proj.opponent_id, proj.opponent_id),
                names.get(proj.team_id, proj.team_id),
            )

        # 4. publish: files for the record, then the channel post (deduped per date)
        (WATCH_DIR / "daily.md").write_text(digest)
        state.update(
            {
                "last_run": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "as_of": today,
                "period_index": proj.period_index,
                "wire_lines": wire_lines[-20:],
                "schedule_window_end": result["schedule_window_end"],
                "news_sources": result["news_sources"],
            }
        )
        already = state.get("last_digest_date") == today
        if args.no_send:
            sent = None
        elif already and not args.force_send:
            sent = False
            print(f"digest for {today} already posted; refreshed in place only")
        else:
            sent = send_to_channel(args.channel, digest)
            if sent:
                state["last_digest_date"] = today
        state_path.write_text(json.dumps(state, indent=2))
        print(digest)
        return 0
    finally:
        store.close()


def _run_refresh(store, league: str, wire_lines: list[str]) -> dict:
    """Delegate to the watcher's shared wire pass, collecting its log lines."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "draft_watch_service", REPO / "scripts" / "draft_watch_service.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.run_refresh_wires(store, wire_lines.append, league)


if __name__ == "__main__":
    raise SystemExit(main())
