"""Structured event sink — one JSON line per request.

Default sink: ``<project_root>/events.jsonl``. The path is project-relative
(not CWD-relative) so it lands in the same place whether the app is run
via ``make dev``, pytest, or a docker entrypoint.

Override with ``OBS_EVENTS_PATH`` — the docker-compose stack points the
app and the dashboard service at the same bind-mounted file so both see
the same event log.

Production swap path: change ``write`` to push to ClickHouse / Kafka / etc.
Call sites stay unchanged because they only see ``events.write(ctx)``.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from src.obs.base import RequestCtx

# events.py lives at <root>/src/obs/events.py — parents[2] is <root>.
_PATH = Path(os.environ.get(
    "OBS_EVENTS_PATH",
    str(Path(__file__).resolve().parents[2] / "events.jsonl"),
))


def write(ctx: RequestCtx) -> None:
    """Append one event to the sink. Telemetry must never break a request,
    so all errors are swallowed."""
    try:
        with _PATH.open("a") as f:
            f.write(json.dumps(asdict(ctx), default=str) + "\n")
    except Exception:
        pass
