#!/usr/bin/env python3
"""Prepare and deploy the puntgm.com web tools (api/ + /tools page).

The Vercel function is self-contained: it imports a VENDORED copy of the
``fantasy_gm`` package and reads a bundled snapshot of the watcher's store.
Both are build artifacts — never edit them in place, never commit them; this
script regenerates both from the repo tree and the live data dir on every
deploy, then builds the static site and deploys.

    FANTASY_GM_DATA_DIR=/Users/tim/projects/fantasy-nba-gm/data \
        python scripts/deploy_webtools.py [--prod] [--skip-deploy]

With --skip-deploy it stops after preparation and local checks (the CI-free
way to verify a change without touching production). Deployment needs the
Vercel CLI logged in (``npx vercel whoami``) and the project's environment
variables set (SESSION_SECRET, YAHOO_CLIENT_ID, YAHOO_CLIENT_SECRET,
YAHOO_ALLOWED_GUID, YAHOO_REDIRECT_URI) — the script checks and tells you
exactly what is missing.

Post-deploy it verifies the live surface: /api/health ok, the static board
serving, /tools/ serving, and the tools API denying without a session (401 —
never 200; an anonymous tools payload is a leak).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT  # the worktree this script ships with


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=ROOT, **kw)


def vendor_package() -> None:
    """Copy the repo tree's fantasy_gm into api/fantasy_gm (build artifact)."""
    dst = REPO / "api/fantasy_gm"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(REPO / "fantasy_gm", dst, ignore=shutil.ignore_patterns("__pycache__"))
    marker = REPO / "api/fantasy_gm/VENDORED.txt"
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                         text=True).stdout.strip()
    marker.write_text(f"vendored from fantasy_gm@{sha or '(uncommitted tree)'} "
                      f"at {dt.datetime.now(dt.UTC).isoformat()}\n")
    print(f"vendored fantasy_gm -> {dst}")


def bundle_snapshot(data_dir: str) -> None:
    """Copy the watcher's store into the function bundle, with a manifest."""
    src = Path(data_dir) / "fantasy_gm.sqlite"
    if not src.exists():
        sys.exit(f"store not found at {src} — set FANTASY_GM_DATA_DIR")
    dst_dir = REPO / "api/store"
    dst_dir.mkdir(exist_ok=True)
    dst = dst_dir / "snapshot.sqlite"
    shutil.copy2(src, dst)
    import sqlite3

    conn = sqlite3.connect(dst)
    newest = conn.execute("SELECT MAX(game_date) FROM player_logs").fetchone()[0]
    n_logs = conn.execute("SELECT COUNT(*) FROM player_logs").fetchone()[0]
    conn.close()
    sha = hashlib.sha256(dst.read_bytes()).hexdigest()
    (dst_dir / "MANIFEST.txt").write_text(json.dumps({
        "source": str(src),
        "copied_at": dt.datetime.now(dt.UTC).isoformat(),
        "newest_game_date": newest,
        "player_logs": n_logs,
        "sha256": sha,
        "bytes": dst.stat().st_size,
    }, indent=2) + "\n")
    print(f"bundled snapshot -> {dst} ({dst.stat().st_size / 1e6:.1f}MB, "
          f"{n_logs} logs through {newest})")


def build_static(board_as_of: str | None) -> None:
    env = dict(os.environ)
    if "FANTASY_GM_DATA_DIR" not in env:
        env["FANTASY_GM_DATA_DIR"] = "/Users/tim/projects/fantasy-nba-gm/data"
    py = env.get("PYTHON_BIN", sys.executable)
    if board_as_of is None:
        # Default: the committed dist IS the board of record — a tools deploy
        # never rebuilds it. A same-as-of rebuild no longer reproduces the
        # committed numbers (found 2026-11-25: every row's z-scores shift), so
        # re-basing the board is an explicit, reviewed act.
        print("board left as committed (pass --rebuild-board to re-base)")
        if run([py, "scripts/build_tools.py"], env=env).returncode != 0:
            sys.exit("build_tools.py failed")
        return
    if run([py, "scripts/build_site.py", "--as-of", board_as_of], env=env).returncode != 0:
        sys.exit("build_site.py failed")
    if run([py, "scripts/build_tools.py"], env=env).returncode != 0:
        sys.exit("build_tools.py failed")


def local_checks() -> None:
    dist = REPO / "site/dist"
    for rel in ("index.html", "data/boards.json", "tools/index.html"):
        p = dist / rel
        if not p.exists() or p.stat().st_size == 0:
            sys.exit(f"static check failed: {p} missing or empty")
    for rel in ("api/index.py", "api/requirements.txt", "api/store/snapshot.sqlite"):
        p = REPO / rel
        if not p.exists():
            sys.exit(f"function check failed: {p} missing")
    print("local checks ok (dist + function bundle present)")


def deploy(prod: bool) -> None:
    who = run(["npx", "--yes", "vercel", "whoami"], capture_output=True, text=True)
    if who.returncode != 0 or not who.stdout.strip():
        sys.exit("vercel CLI is not authenticated — run `npx vercel login` first "
                 "(one-time, then rerun this script)")
    print(f"vercel identity: {who.stdout.strip()}")
    cmd = ["npx", "--yes", "vercel", "--yes"] + (["--prod"] if prod else [])
    if run(cmd).returncode != 0:
        sys.exit("vercel deploy failed")
    print("deployed. Set/verify the project env vars if not yet done:\n"
          "  SESSION_SECRET, YAHOO_CLIENT_ID, YAHOO_CLIENT_SECRET, "
          "YAHOO_ALLOWED_GUID, YAHOO_REDIRECT_URI\n"
          "then verify: /api/health ok, / 200 (board), /tools/ 200, "
          "/api/tools/wire-lines 401 unauthenticated.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prod", action="store_true", help="deploy to production")
    ap.add_argument("--skip-deploy", action="store_true",
                    help="prepare + local checks only")
    ap.add_argument("--rebuild-board", action="store_true",
                    help="rebuild the static board too (default: leave the "
                         "committed dist as the board of record)")
    ap.add_argument("--board-as-of", default=None,
                    help="basis date when --rebuild-board (default: the committed "
                         "dist's own as_of)")
    args = ap.parse_args()

    data_dir = os.environ.get("FANTASY_GM_DATA_DIR",
                              "/Users/tim/projects/fantasy-nba-gm/data")
    board_as_of = None
    if args.rebuild_board:
        board_as_of = args.board_as_of
        if board_as_of is None:
            manifest = REPO / "site/dist/data/boards.json"
            if manifest.exists():
                board_as_of = json.loads(manifest.read_text()).get("as_of")
            if not board_as_of:
                sys.exit("no committed basis found — pass --board-as-of explicitly")
            print(f"board basis carried from the committed dist: as_of {board_as_of}")
    vendor_package()
    bundle_snapshot(data_dir)
    build_static(board_as_of)
    local_checks()
    if not args.skip_deploy:
        deploy(args.prod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
