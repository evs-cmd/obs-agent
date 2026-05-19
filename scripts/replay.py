"""Replay a saved request snapshot through POST /ask.

Reads ``replays/{request_id}.json`` (written by ``src/obs/replay.py`` on every
``POST /ask``), extracts the original ``query`` + ``session_id``, and re-issues
the same request against a running app instance — useful for reproducing a
flaky answer, A/B-ing a code change, or warming the dashboard with traffic.

Usage:
    .venv/bin/python scripts/replay.py <request_id>
    # or, via Makefile:
    make replay ID=<request_id>

The replay shares the original ``session_id`` so prior thread state on the
checkpointer is reused (mimics "what happens if the user re-asks now"). Pass
``--new-session`` to start fresh.

Environment:
    OBS_AGENT_URL   target endpoint (default: http://localhost:8000)

Exit codes:
    0  replay completed (200 from /ask, stream consumed)
    1  snapshot not found / unreadable
    2  /ask returned non-2xx
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
REPLAY_DIR = ROOT / "replays"


def _load(request_id: str) -> dict:
    path = REPLAY_DIR / f"{request_id}.json"
    if not path.exists():
        sys.stderr.write(f"replay: no snapshot at {path}\n")
        sys.stderr.write(f"  hint: ls {REPLAY_DIR}/ | head\n")
        sys.exit(1)
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"replay: cannot read {path}: {exc}\n")
        sys.exit(1)


def _post(url: str, body: dict) -> int:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                line = raw.decode(errors="replace").rstrip("\n")
                if line:
                    print(line)
            return resp.status
    except urllib.error.HTTPError as exc:
        sys.stderr.write(f"replay: HTTP {exc.code} from {url}: {exc.reason}\n")
        return exc.code
    except urllib.error.URLError as exc:
        sys.stderr.write(f"replay: cannot reach {url}: {exc.reason}\n")
        sys.stderr.write("  is the app running? `make up` or `make dev`\n")
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("request_id", help="request_id from replays/{id}.json")
    ap.add_argument(
        "--new-session",
        action="store_true",
        help="start a fresh session_id instead of reusing the snapshot's",
    )
    ap.add_argument(
        "--url",
        default=os.environ.get("OBS_AGENT_URL", "http://localhost:8000"),
        help="target host (default: $OBS_AGENT_URL or http://localhost:8000)",
    )
    args = ap.parse_args()

    snap = _load(args.request_id)
    query = snap.get("query")
    if not query:
        sys.stderr.write(f"replay: snapshot has no 'query' field: {args.request_id}\n")
        sys.exit(1)

    session_id = str(uuid.uuid4()) if args.new_session else snap.get("session_id") or str(uuid.uuid4())

    print(f"# replay request_id={args.request_id}")
    print(f"#   query:      {query}")
    print(f"#   session_id: {session_id}{' (new)' if args.new_session else ' (reused)'}")
    print(f"#   POST {args.url}/ask")
    print()

    status = _post(f"{args.url}/ask", {"message": query, "session_id": session_id})
    sys.exit(0 if 200 <= status < 300 else 2)


if __name__ == "__main__":
    main()
