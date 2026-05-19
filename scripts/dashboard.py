"""Launch the DuckDB UI over events.jsonl.

Bootstraps the analytical views in ``scripts/init.sql`` and starts the
DuckDB UI server (a real web UI, not a hand-rolled HTML file). The UI
opens at http://localhost:4213 — query, plot, save notebooks.

Usage:
    .venv/bin/python scripts/dashboard.py
    # then open http://localhost:4213 (DuckDB usually opens it for you)

Stops cleanly on Ctrl-C. State (notebooks, history) persists in obs.duckdb.

Prereqs:
    pip install duckdb            # already in dev deps if you ran make dev
    .venv/bin/python scripts/seed_events.py 200   # optional: synthetic data
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.environ.get("OBS_DUCKDB_PATH", str(ROOT / "obs.duckdb")))
INIT = ROOT / "scripts" / "init.sql"
EVENTS = Path(os.environ.get("OBS_EVENTS_PATH", str(ROOT / "events.jsonl")))
QUERIES = ROOT / "scripts" / "dashboard_queries.sql"


def _print_starter_queries() -> None:
    """Print a discoverable list of starter chart queries on startup.

    The DuckDB UI stores notebooks inside ``obs.duckdb`` itself and has no
    public API for pre-seeding them, so the next-best thing is to surface
    the titles + path in the startup banner. Operators copy a block into a
    notebook cell, run it, and pick a chart in the result tab.
    """
    if not QUERIES.exists():
        return
    titles: list[str] = []
    for line in QUERIES.read_text().splitlines():
        # Headers look like:  -- ─── 1. Traffic mix: requests per route ──
        s = line.strip()
        if s.startswith("-- ─── ") and s.rstrip("─ -").endswith(("─", "")):
            titles.append(s.lstrip("- ─").rstrip(" ─-"))
    print(f"starter queries — {QUERIES.relative_to(ROOT)}")
    for t in titles:
        print(f"  • {t}")
    print("  → paste any block into a notebook cell, then click the chart icon.")
    print()


def main() -> None:
    if not EVENTS.exists():
        print(f"warning: events file not found at {EVENTS}")
        print("  → run `python scripts/seed_events.py 200` first, or hit /ask")
        print("    a few times to produce real telemetry.")
        print()
    con = duckdb.connect(str(DB))

    # The UI extension ships with the duckdb pip package as a downloadable
    # extension — INSTALL is a one-shot fetch, idempotent afterwards.
    con.execute("INSTALL ui")
    con.execute("LOAD ui")

    # Bootstrap analytical views. init.sql references `events.jsonl` as a
    # bare filename — substitute the real (possibly absolute) path so the
    # same SQL works for local dev and for the docker dashboard service
    # where the file lives on a shared named volume.
    init_sql = INIT.read_text().replace("'events.jsonl'", f"'{EVENTS}'")
    con.execute(init_sql)

    print(f"obs.duckdb ready @ {DB}")
    print("views: events, llm_calls_flat, mcp_calls_flat,")
    print("       route_stats, model_costs, tool_stats,")
    print("       degraded_events, top_cost_requests,")
    print("       eval_runs, eval_vs_prod")
    print()
    _print_starter_queries()
    print("starting DuckDB UI …")
    con.execute("CALL start_ui()")
    print("→ http://localhost:4213")
    print("Ctrl-C to stop.")

    # start_ui() launches an HTTP server in a background thread tied to this
    # process; if we return, the server dies. Block until SIGINT.
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    try:
        signal.pause()
    except AttributeError:
        # Windows has no signal.pause(); fall back to a blocking read.
        try:
            input()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
