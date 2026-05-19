"""Seed events.jsonl with realistic-looking rows so the dashboard has data.

Each row is identical in shape to what the live app writes (matches the
RequestCtx dataclass). Variance covers all routes, both deterministic
LogsAgent and ReAct paths, occasional degraded requests, and a price mix.

Usage:
    .venv/bin/python scripts/seed_events.py 100   # 100 events
"""
from __future__ import annotations

import json
import random
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from src.obs.base import RequestCtx
from src.obs.cost import PRICES

ROOT = Path(__file__).resolve().parents[1]
SINK = ROOT / "events.jsonl"

ROUTES = ["logs", "metrics", "traces", "docs", "incident"]
ROUTE_WEIGHTS = [0.30, 0.20, 0.15, 0.10, 0.25]
SERVICES = ["payment-service", "checkout-service", "gateway-service",
            "inventory-service", "user-service"]

QUERIES = [
    "why is payment failing?",
    "checkout latency p99",
    "errors in gateway last hour",
    "search runbook for jwks rotation",
    "investigate inventory outage",
    "user-service health",
    "show me slow spans in checkout",
    "what's the error rate on payment",
    "is the platform healthy?",
    "trace 7f3a is slow",
]

MODELS = list(PRICES.keys())


def _llm_call(model: str, in_tok: int, out_tok: int, ms: float) -> dict:
    pi, po = PRICES[model]
    usd = (in_tok * pi + out_tok * po) / 1_000_000
    return {"model": model, "in": in_tok, "out": out_tok,
            "ms": round(ms, 1), "usd": round(usd, 6)}


def _mcp_call(tool: str, ms: float, n_bytes: int) -> dict:
    return {"tool": tool, "ms": round(ms, 1), "bytes": n_bytes}


def synth_event(now: float) -> dict:
    route = random.choices(ROUTES, weights=ROUTE_WEIGHTS, k=1)[0]
    query = random.choice(QUERIES)
    rid = str(uuid.uuid4())

    # Route-specific shape
    if route == "incident":
        llm_calls = [
            _llm_call("gpt-4o-mini", random.randint(800, 1500), random.randint(80, 200),
                      random.uniform(300, 700)),    # router
            _llm_call("gpt-4o", random.randint(2500, 4500), random.randint(400, 900),
                      random.uniform(1500, 3500)),  # synthesis
        ]
        mcp_calls = [
            _mcp_call("get_incident_context", random.uniform(120, 400), random.randint(4000, 12000)),
        ] + [
            _mcp_call(random.choice(["get_logs_by_trace", "get_traces_by_correlation"]),
                      random.uniform(40, 180), random.randint(800, 3000))
            for _ in range(random.randint(2, 5))
        ]
    elif route == "logs":
        # 70% deterministic, 30% ReAct fallback
        if random.random() < 0.7:
            llm_calls = [
                _llm_call("gpt-4o-mini", random.randint(800, 1500), 80, random.uniform(280, 600)),
                _llm_call("gpt-4o-mini", random.randint(1200, 2200), random.randint(150, 350),
                          random.uniform(500, 1200)),
            ]
            mcp_calls = [
                _mcp_call("search_logs", random.uniform(35, 120), random.randint(1500, 4000)),
                _mcp_call("get_errors", random.uniform(28, 110), random.randint(500, 2500)),
            ]
        else:
            n_steps = random.randint(3, 5)
            llm_calls = [
                _llm_call("gpt-4o-mini", random.randint(900, 1800), random.randint(60, 200),
                          random.uniform(280, 700))
                for _ in range(n_steps)
            ]
            mcp_calls = [
                _mcp_call(random.choice(["search_logs", "get_error_logs", "get_log_context"]),
                          random.uniform(40, 150), random.randint(800, 3500))
                for _ in range(n_steps - 1)
            ]
    elif route == "metrics":
        llm_calls = [
            _llm_call("gpt-4o-mini", random.randint(800, 1300), 80, random.uniform(250, 500)),
            _llm_call("gpt-4o-mini", random.randint(1000, 1800), random.randint(100, 250),
                      random.uniform(400, 900)),
        ]
        mcp_calls = [
            _mcp_call("get_metrics", random.uniform(30, 90), random.randint(1000, 3000)),
        ]
    elif route == "traces":
        llm_calls = [
            _llm_call("gpt-4o-mini", random.randint(900, 1400), 80, random.uniform(260, 520)),
            _llm_call("gpt-4o-mini", random.randint(1500, 2500), random.randint(150, 350),
                      random.uniform(450, 1100)),
        ]
        mcp_calls = [
            _mcp_call("search_traces", random.uniform(40, 130), random.randint(2000, 6000)),
            _mcp_call("get_slow_spans", random.uniform(35, 110), random.randint(800, 2500)),
        ]
    else:  # docs
        llm_calls = [
            _llm_call("gpt-4o-mini", random.randint(700, 1100), 80, random.uniform(220, 450)),
            _llm_call("gpt-4o-mini", random.randint(800, 1500), random.randint(120, 300),
                      random.uniform(350, 800)),
        ]
        mcp_calls = [
            _mcp_call("search_runbooks", random.uniform(20, 70), random.randint(400, 1500)),
        ]

    cost_usd = sum(c["usd"] for c in llm_calls)
    total_ms = sum(c["ms"] for c in llm_calls) + sum(c["ms"] for c in mcp_calls)
    # 8% degraded
    degraded = random.random() < 0.08
    timeouts = [route] if degraded else []
    if degraded:
        total_ms = 8000.0  # capped at budget

    ctx = RequestCtx(
        request_id=rid,
        session_id=str(uuid.uuid4()),
        query=query,
        t0=now,
        route=route,
        answer="(synthetic)",
        total_ms=round(total_ms, 1),
        llm_calls=llm_calls,
        mcp_calls=mcp_calls,
        cost_usd=round(cost_usd, 6),
        degraded=degraded,
        timeouts=timeouts,
    )
    return asdict(ctx)


def main(n: int) -> None:
    random.seed()
    # Spread events over the last 24 hours
    now = time.time()
    start = now - 24 * 3600
    with SINK.open("w") as f:
        for _ in range(n):
            ev = synth_event(random.uniform(start, now))
            f.write(json.dumps(ev) + "\n")
    print(f"wrote {n} events → {SINK}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    main(n)
