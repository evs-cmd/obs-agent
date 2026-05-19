# obs-agent

Multi-agent observability assistant. A router LLM classifies queries, dispatches
to specialized agents that pull data through MCP, compress signals via a retrieval
layer, and synthesize answers grounded in cited evidence.

## Quickstart

```bash
cp .env.example .env             # set OPENAI_API_KEY
docker compose up -d             # app + dashboard + mcp

# Ask the system something:
curl -N -X POST http://localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"message":"Why is checkout failing?"}'
```

Then:

- **API:** http://localhost:8000/docs
- **Dashboard:** http://localhost:4213 (DuckDB UI over `events.jsonl`)
- **MCP:** http://localhost:9000/sse

## Architecture

```
                  POST /ask  (SSE stream)
                       │
                       ▼
                    Router       ← LLM-based classification (gpt-4o-mini)
                       │           Pydantic-validated, confidence-thresholded
            ┌──────────┼──────────┬──────────┬──────────┐
            ▼          ▼          ▼          ▼          ▼
         Traces      Logs     Metrics   Incident
         Agent       Agent     Agent    Investigator
                                        ┌──────────┐
                                        │ subgraph │ ← gather_context
                                        │  (cond.) │ ← analyze
                                        │ + parallel│  → deep_dive
                                        │  drill-down│ → check_related
                                        │  synthesis │
                                        └──────────┘
                       │
                       ▼
           Query Planner      ← maps intent (error/latency/health/incident)
           (src/retrieval/)     to data budgets per source
                       │
                       ▼
                MCP Server     ← unified FastMCP service over SSE
              (src/mcp/server)    one process, tool groups per agent
                       │
                ┌──────┴──────────────────────┐
                ▼                              ▼
        Domain tools                  Correlation tool
    (logs/metrics/traces/             get_incident_context
     errors)                          joins by trace_id + correlation_id
                       │
                       ▼
            Retrieval Layer     ← pure functions, no I/O, no LLM
            (src/retrieval/)      summarize_logs / summarize_metrics /
                                  build_trace_skeleton / detect_anomalies
                       │
                       ▼
                  LLM call      ← OpenAI via litellm.acompletion
              (synthesis)         JSON-structured output (Pydantic schema)
                       │
                       ▼
            Citation validation ← exact-string check that every cited
            (src/llm/validation)  trace_id/log_id/service exists in context
                       │
                       ▼
                SSE response
```

### Layer responsibilities (hard rules)

| Layer | Does | Does NOT |
|---|---|---|
| **MCP** | fetch + filter + limit | aggregate, rank, embed, LLM-call |
| **Correlation** | join logs/traces/metrics/errors by trace_id | analyze, cluster |
| **Retrieval** | pure-function signal compression | I/O, LLM, MCP calls |
| **Query Planner** | map query intent → data budgets | LLM call |
| **Agent** | orchestrate the above + prompt the LLM | own correlation logic |
| **LLM** | reason over compressed signals | direct data access |

## Design decisions

The choices that shape this system, with the trade-off each one accepts:

- **Router is an LLM with structured output, not regex.** `gpt-4o-mini`
  returns `{route, reasoning, confidence}` validated by a Pydantic schema.
  Below a confidence threshold (`router.confidence_threshold` in
  `config/app.yaml`), it falls back to `out_of_scope` so the system
  refuses rather than confabulating an answer on a query it barely
  understood. One JSON-mode call per request, retry on parse failure,
  fixed cost. Pattern-match routers can't classify novel phrasings; an
  LLM can.

- **MCP is the only seam between agents and data.** Tools live behind a
  FastMCP server (SSE transport); the app talks to it via
  `langchain-mcp-adapters`. This means swapping mock JSON for real
  Prometheus / Loki / Jaeger is a backend change, not an agent change.
  `config/tools.yaml` is a hard allowlist — the hub refuses to start if
  the server advertises a tool that isn't pinned (schema-poisoning
  defense).

- **Retrieval layer between MCP and LLM.** `src/retrieval/signals.py` is
  pure functions: cluster logs by message, percentile-summarize
  metrics, extract trace skeletons, detect anomalies. No I/O, no LLM.
  The LLM never sees raw rows — only compressed, cited signals. This
  is what keeps token spend bounded as data volume grows.

- **One correlation tool, not N agent steps.** `get_incident_context`
  (an MCP tool) pre-joins logs + traces + metrics + errors by
  `trace_id` / `correlation_id` and returns one object. The
  `IncidentAgent` makes one LLM call to synthesize an answer instead of
  an N-step ReAct loop. ~4× cheaper than the agent doing the join
  itself, and the join logic lives where data lives (MCP), not in
  agent code.

- **Deterministic agents where the brief is narrow.** `LogsAgent`
  doesn't ReAct — it runs `plan → parallel MCP fetch → summarize → 1
  LLM synth`. The query planner (`src/retrieval/planner.py`) maps
  intent (error / latency / health) to data budgets, so the same path
  works for any logs-shaped question. ReAct is the fallback for
  metrics + traces agents, not the default. Faster, cheaper, easier to
  debug.

- **Citation validation, not just hope.** Every `trace_id`, `log_id`,
  and `service` cited in the synthesized answer is exact-string-matched
  against the context that was passed to the LLM. Mismatched citations
  are stripped before the answer goes out
  (`src/llm/validation.py`). Cheap, prevents the most embarrassing
  class of hallucination.

- **Pydantic + reducers for the incident sub-graph.** The
  `IncidentAgent` runs its own `StateGraph` with a Pydantic
  `IncidentState`. Parallel fan-out (one `Send` per error trace, one
  per related service) accumulates into `drill_down` via an `add`
  list-reducer — branches don't clobber each other. Studio shows each
  branch as a distinct node; OTEL spans are per-branch.

- **MemorySaver, not Redis.** Sessions persist in-process for the
  duration of the FastAPI lifetime. Multi-replica deploys would swap
  in a real backend; the seam is `build_graph(checkpointer=...)`. The
  demo doesn't need it, and adding Redis just to demo it would be
  ceremony.

- **Configuration over code.** Adding an agent is a `config/app.yaml`
  edit + a class file. The router prompt is `templates/router_system.j2`.
  Per-agent tool sets, models, prompt template names, latency budgets
  all declarative.

- **Per-node latency budget with degraded fallback.**
  `src/graph/builder.py` wraps each agent in `asyncio.wait_for` with a
  configurable timeout. On timeout the graph returns a partial answer
  rather than hanging the SSE — the response degrades, the request
  doesn't fail.

## Demo path vs production slots

**Demo path** (works with `docker compose up`):

- Router LLM classification + confidence threshold + retries
- 4 specialized agents (traces, logs, metrics, incident)
- Unified MCP server with 17 tools (SSE transport, real connection pooling)
- Correlation tool that pre-joins logs/traces/metrics/errors
- Retrieval layer (log clustering, p99 stats, trace skeletons, anomaly detection)
- Query planner with intent-based data budgets — **keyword default + optional LLM planner** (`LLM_PLANNER_ENABLED=true`)
- **Deterministic LogsAgent**: `plan → parallel MCP fetch → summarize → 1 LLM synth` (~4× cheaper than the ReAct fallback it supersedes)
- IncidentAgent investigation graph (conditional routing, parallel drill-down, capped self-critique)
- Pydantic-structured synthesis with citation hallucination detection
- LangGraph in-process checkpointer (`MemorySaver`) for session continuity
- OpenTelemetry tracing on every LLM/MCP/agent call
- **Structured event log** per request → `events.jsonl` (model/tokens/cost, MCP tool/ms/bytes, latency, degraded flag)
- **Full replay snapshot** per request → `replays/{request_id}.json` (fs default, S3 via `REPLAY_BUCKET`)
- **Per-session cost cap** with hard refuse (`MAX_COST_USD`, default $1.00)
- **Per-node latency budget** with degraded fallback (`NODE_TIMEOUT_MS`, default 8000)
- **Tool allowlist** at the MCP boundary (`config/tools.yaml`) — refuses unknown tools at startup
- **ReAct recursion cap** (`react_recursion_limit=6`) + **bounded message history** (20 msgs)
- **Input sanitization** (length cap + control-char strip) + **PII redact seam** at MCP responses
- **DuckDB UI** over `events.jsonl` (`scripts/dashboard.py` boots views + opens the UI at http://localhost:4213)
- JSON logs with auto-injected trace_id ↔ span_id
- deepeval-based pytest evals (GEval correctness + actionability + FaithfulnessMetric)

**Production-ready slots.** MCP backends, the checkpointer, and the trace
exporter are pluggable behind clean interfaces — swap mock JSON for
Prometheus/Loki/Jaeger, `MemorySaver` for `RedisSaver`, etc. without touching
agent code.

## Project layout

```
src/
├── agents/        # BaseAgent + 4 specialized agents (logs.py is deterministic;
│                  #   metrics/traces still ReAct, capped at 6 iterations)
├── api/           # FastAPI app + SSE routes + Pydantic request models
├── core/          # settings, telemetry (OTEL spans; structured events live in obs/)
├── graph/         # LangGraph state, router, builder w/ per-node latency budget
├── llm/           # LLM client (records cost), schemas, citation validation, prompts
├── mcp/
│   ├── server.py  # unified FastMCP server (stdio or SSE)
│   ├── hub.py     # ToolHub + always_on tool selection + allowlist enforcement
│   └── tools/     # logs.py, metrics.py, traces.py, errors.py, correlation.py
├── obs/           # Cross-cutting observability (~250 LOC, single-team scoped)
│   ├── base.py    #   RequestCtx + ContextVar lifecycle; record_mcp/set_route/record_timeout
│   ├── events.py  #   append → events.jsonl
│   ├── replay.py  #   snapshot → replays/{request_id}.json (S3 via REPLAY_BUCKET)
│   ├── cost.py    #   PRICES, record_llm, BudgetExceeded
│   ├── guard.py   #   sanitize_query (length cap + control-char strip)
│   └── redact.py  #   noop default; Presidio swap site
└── retrieval/
    ├── signals.py # summarize_logs / summarize_metrics / build_trace_skeleton / detect_anomalies
    └── planner.py # Plan + plan_query (keyword) + plan_query_llm + plan() dispatcher + detect_service

templates/         # All prompts as Jinja2 — one source of truth
config/
├── app.yaml       # Agent registry + routing config (no inline prompts)
└── tools.yaml     # MCP tool allowlist (refuses drift at hub.setup())
data/              # Mock observability data (logs, metrics, traces, errors)
scripts/
├── seed_events.py # write N realistic event rows to events.jsonl
├── init.sql       # bootstraps DuckDB views over events.jsonl (route_stats, model_costs, ...)
└── dashboard.py   # `python scripts/dashboard.py` → DuckDB UI on :4213
evals/             # deepeval pytest suite (routing + synthesis quality)
tests/             # Unit tests (no LLM calls)
```

## Setup

```bash
# Local dev (no Docker):
pip install -e ".[dev,telemetry]"
export OPENAI_API_KEY=sk-...
make dev                    # FastAPI on :8000

# Or:
make up                     # Full stack via docker compose
```

## Useful commands

```bash
make test                   # Unit tests (no LLM, ~10s)
make eval-quick             # Router accuracy eval (~6 LLM calls, ~$0.01)
make eval                   # Full eval suite with deepeval (~20 LLM calls)
make up                     # Demo stack via docker compose
make down                   # Tear down
make logs / app-logs        # Tail logs

# Telemetry — the `dashboard` service is up by default at :4213.
# For host-local dev without Docker:
python scripts/seed_events.py 200     # optional: synthetic events
python scripts/dashboard.py           # bootstraps views + opens DuckDB UI on :4213
```

## Configuration

All behavior is controlled by:

- **`.env`** — secrets and feature toggles (see `.env.example`)
- **`config/app.yaml`** — agent registry, routing thresholds, prompt template names
- **`templates/*.j2`** — every LLM prompt in the system (Jinja)

## Try it

`docker compose up -d` first. Every `curl` below uses `-N` so the
SSE stream flushes token-by-token. `Ctrl-C` to stop.

### Example requests demonstrating each route

**1. `incident` — multi-signal investigation (correlated join)**

```bash
curl -N -X POST http://localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"message":"Why is checkout failing?"}'
```

**2. `logs` — log analysis (deterministic agent, no ReAct loop)**

```bash
curl -N -X POST http://localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"message":"Show me errors on payment-service in the last hour"}'
```

**3. `metrics` — latency percentile**

```bash
curl -N -X POST http://localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is the p99 latency for checkout-service?"}'
```

**4. `metrics` — error rate (same route, different query intent)**

```bash
curl -N -X POST http://localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is the current error rate for the gateway-service?"}'
```

**5. `traces` — trace skeleton extraction**

```bash
curl -N -X POST http://localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"message":"Trace 7f3a is slow — where is the bottleneck?"}'
```

**6. `out_of_scope` — off-topic query (rejected, no LLM synthesis)**

```bash
curl -N -X POST http://localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"message":"Why is the earth blue?"}'
```

Returns a fixed refusal explaining the system handles only
observability questions. No MCP fetch, no incident fabrication.

### Multi-turn session (same `session_id` → context carries over)

```bash
SESSION_ID=$(python -c "import uuid; print(uuid.uuid4())")

curl -N -X POST http://localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d "{\"message\":\"Investigate checkout failures\",\"session_id\":\"$SESSION_ID\"}"

curl -N -X POST http://localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d "{\"message\":\"Now drill into the payment errors\",\"session_id\":\"$SESSION_ID\"}"
```

### Route classification summary

The router classifies into 4 specialized agents plus an `out_of_scope`
rejection path. Each agent handles a **family of query types**, so the
system covers well over the brief's "≥5 distinct query types" bar:

| Query                                            | Routes to     | Query type                       |
|--------------------------------------------------|---------------|----------------------------------|
| `Why is checkout failing?`                       | incident      | multi-signal investigation       |
| `Investigate the Kafka consumer lag incident`    | incident      | named-incident investigation     |
| `Show me errors on payment-service`              | logs          | log search by service            |
| `Which logs mention "connection refused"?`       | logs          | log search by message            |
| `What is the p99 latency for checkout-service?`  | metrics       | latency percentile               |
| `What is the current error rate for gateway?`    | metrics       | rate metric                      |
| `Trace 7f3a is slow`                             | traces        | trace skeleton                   |
| `Find slow spans in the search-service trace`    | traces        | span analysis                    |
| `Why is the earth blue?`                         | out_of_scope  | non-observability — rejected     |
| `Tell me a joke`                                 | out_of_scope  | non-observability — rejected     |

The `out_of_scope` route is the system's trust boundary: the router
prompt teaches it to recognize non-observability queries, and the graph
short-circuits to a fixed refusal message — no MCP call, no LLM
synthesis, no fabricated incident report.

The full router golden set lives in
[`evals/golden.json`](evals/golden.json) (36 cases including 6 explicit
out-of-scope checks); `make eval-quick` runs it against `gpt-4o-mini`
(~$0.01, ~36 LLM calls).

## Tests

```bash
make test          # ~50 unit tests — pure logic, no LLM
make eval-quick    # router accuracy on golden cases
make eval          # full eval via deepeval (GEval + FaithfulnessMetric)
```

## Telemetry & storage

Every `POST /ask` writes one structured row to `events.jsonl` and a full
snapshot to `replays/{request_id}.json`. Shape matches `src/obs/RequestCtx`:

```json
{
  "request_id": "...", "session_id": "...", "query": "...",
  "route": "logs", "answer": "...", "total_ms": 1631.4, "cost_usd": 0.00029,
  "llm_calls": [{"model":"gpt-4o-mini","in":1240,"out":180,"ms":612.4,"usd":0.000288}],
  "mcp_calls": [{"tool":"search_logs","ms":88.2,"bytes":4501},
                {"tool":"get_errors","ms":67.1,"bytes":1820}],
  "degraded": false, "timeouts": []
}
```

### Where the data lives

| Volume | Backend | Why |
|---|---|---|
| dev / demo / <100k events/day | `events.jsonl` flat file (current) | zero infra; one append per request |
| ad-hoc analytics | **DuckDB** over `events.jsonl` | `read_json('events.jsonl')` — no ingestion, no schema |
| >100k events/day | **ClickHouse** | columnar; native shape; one-line ingest from JSONL |

Loki is the wrong shape (line-oriented logs); Postgres works but is slow on
analytical aggregates at volume; BigQuery / Snowflake are overkill until
multi-TB. The row schema is the same across all three swap-out paths — the
storage decision doesn't change the agent code.

### Dashboard — DuckDB UI

```bash
python scripts/seed_events.py 200     # optional: synthetic data for the demo
python scripts/dashboard.py           # bootstraps views + opens DuckDB UI
# → http://localhost:4213
```

`scripts/init.sql` defines the analytical views the UI loads with:

| View | What it shows |
|---|---|
| `events` | one row per request (raw) |
| `llm_calls_flat`, `mcp_calls_flat` | unnested per-call rows for joins |
| `route_stats` | requests + p50/p95/p99 latency + cost per route |
| `model_costs` | spend + tokens + p95 latency per model |
| `tool_stats` | calls + p50/p95 + payload bytes per MCP tool |
| `degraded_events` | requests that hit `NODE_TIMEOUT_MS` |
| `top_cost_requests` | top 25 by `cost_usd` (debug stragglers) |

`SELECT * FROM route_stats` in the UI gets you the dashboard panel —
charts, notebooks, and history live in `obs.duckdb` next to it.

### Configuration knobs

Beyond `.env` / `config/app.yaml` / `templates/`, the obs layer reads:

| Env var | Default | Effect |
|---|---|---|
| `MAX_COST_USD` | `1.0` | per-session budget cap; `record_llm` raises `BudgetExceeded` over the cap |
| `NODE_TIMEOUT_MS` | `8000` | per-agent latency budget; `_make_node` returns a degraded answer on timeout |
| `LLM_PLANNER_ENABLED` | `false` | flip to use the LLM-based query planner (+1 LLM call) instead of keyword heuristics |
| `LLM_PLANNER_MODEL` | `gpt-4o-mini` | model for the LLM planner |
| `REPLAY_BUCKET` | _(unset)_ | when set, `replay.save` switches from `replays/` to S3 (currently a NotImplementedError stub) |

Per-agent overrides live in `config/app.yaml`:
`react_recursion_limit`, `node_timeout_ms`, `tool_k`, `always_on_tools`.
