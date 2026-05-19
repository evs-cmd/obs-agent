"""Settings — environment + YAML config loading.

Two layers:
- `Settings` (pydantic-settings): runtime env vars (secrets, endpoints, feature flags)
- `get_app_config()`: structural config from `config/app.yaml` (agents, prompts, routing rules)

Env vars override YAML where they overlap.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime env vars. Loaded from .env or process env."""

    model_config = ConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ─── LLM providers ────────────────────────────────────────────────
    openai_api_key: str = ""

    # ─── OpenTelemetry ────────────────────────────────────────────────
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "obs-agent"
    otel_console: bool = False

    # ─── MCP server (SSE transport) ───────────────────────────────────
    # When set, app talks to MCP server over SSE. Otherwise spawns stdio subprocess.
    mcp_sse_url: str = ""

    # ─── Telemetry backend (data source for MCP tools) ────────────────
    # "mock"  → reads data/*.json fixtures (default; what tests + evals use)
    # "otel"  → live queries against Jaeger / Prometheus / (optional) Loki
    #
    # Switch with env: OBS_BACKEND=otel. Each backend URL is overridable
    # too; defaults match the upstream OTel demo's docker-compose ports.
    # Logs are degraded to [] when OTEL_LOKI_URL is empty so the minimal
    # demo (no Loki) still works end-to-end.
    obs_backend: str = "mock"
    otel_jaeger_url: str = "http://localhost:16686"
    otel_prometheus_url: str = "http://localhost:9090"
    otel_loki_url: str = ""

    # ─── Self-critique (IncidentAgent) ────────────────────────────────
    # When synthesis confidence < threshold, run a second LLM call to audit
    # the analysis and append findings. No extra cost on high-confidence cases.
    enable_self_critique: bool = True
    self_critique_threshold: float = 0.7

    # ─── Per-session budget cap (USD) ─────────────────────────────────
    # src/obs/cost.py raises BudgetExceeded when the running total for the
    # current request scope crosses this number. Set to 0 to disable.
    max_cost_usd: float = 1.0

    # ─── Per-agent-node latency budget (ms) ───────────────────────────
    # src/graph/builder.py wraps each agent's handle() in asyncio.wait_for.
    # On timeout the request degrades gracefully (returns a partial answer
    # instead of hanging the SSE). Override per agent via
    # `node_timeout_ms:` in config/app.yaml. Set to 0 to disable.
    node_timeout_ms: int = 80000

    # ─── LLM-based query planner ──────────────────────────────────────
    # Off by default: keyword planner is free and good enough for the demo.
    # Turn on for production where query phrasing varies wildly. Adds one
    # LLM call (~$0.0002, ~300ms) before each deterministic agent run.
    # Failures silently fall back to the keyword planner.
    llm_planner_enabled: bool = False
    llm_planner_model: str = "gpt-4o-mini"

    @property
    def tracing_enabled(self) -> bool:
        return bool(self.otel_exporter_otlp_endpoint) or self.otel_console


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_app_config() -> dict[str, Any]:
    """Load structural config from config/app.yaml."""
    config_path = PROJECT_ROOT / "config" / "app.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)
