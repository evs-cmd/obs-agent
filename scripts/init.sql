-- Bootstrap views over events.jsonl for ad-hoc analysis in the DuckDB UI.

-- ─── Raw events ───────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW events AS
SELECT *
FROM read_json('events.jsonl', format='newline_delimited');


-- ─── Per-LLM-call rows ────────────────────────────────────────────────────
CREATE OR REPLACE VIEW llm_calls_flat AS
SELECT
    request_id,
    session_id,
    route,
    total_ms,
    cost_usd AS request_cost_usd,
    degraded,
    c.model AS model,
    c['in'] AS in_tokens,
    c.out AS out_tokens,
    c.ms AS call_ms,
    c.usd AS call_usd
FROM events,
UNNEST(llm_calls) AS t(c);


-- ─── Per-MCP-call rows ────────────────────────────────────────────────────
CREATE OR REPLACE VIEW mcp_calls_flat AS
SELECT
    request_id,
    route,
    degraded,
    c.tool AS tool,
    c.ms AS call_ms,
    c.bytes AS call_bytes
FROM events,
UNNEST(mcp_calls) AS t(c);


-- ─── Route summary ────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW route_stats AS
SELECT
    route,
    count(*) AS requests,
    round(quantile_cont(total_ms, 0.5), 1) AS p50_ms,
    round(quantile_cont(total_ms, 0.95), 1) AS p95_ms,
    round(quantile_cont(total_ms, 0.99), 1) AS p99_ms,
    round(sum(cost_usd), 4) AS total_usd,
    round(avg(cost_usd), 6) AS avg_usd_per_req,
    sum(CAST(degraded AS INTEGER)) AS degraded,
    round(
        100.0 * sum(CAST(degraded AS INTEGER)) / count(*),
        1
    ) AS degraded_pct
FROM events
GROUP BY route;


-- ─── Model spend ──────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW model_costs AS
SELECT
    model,
    count(*) AS calls,
    sum(in_tokens) AS in_tokens,
    sum(out_tokens) AS out_tokens,
    round(sum(call_usd), 4) AS total_usd,
    round(avg(call_usd), 6) AS avg_usd_per_call,
    round(quantile_cont(call_ms, 0.95), 1) AS p95_ms
FROM llm_calls_flat
GROUP BY model;


-- ─── MCP tool latency ─────────────────────────────────────────────────────
CREATE OR REPLACE VIEW tool_stats AS
SELECT
    tool,
    count(*) AS calls,
    round(quantile_cont(call_ms, 0.5), 1) AS p50_ms,
    round(quantile_cont(call_ms, 0.95), 1) AS p95_ms,
    sum(call_bytes) AS total_bytes,
    round(avg(call_bytes), 0) AS avg_bytes_per_call
FROM mcp_calls_flat
GROUP BY tool;


-- ─── Degraded requests timeline ───────────────────────────────────────────
CREATE OR REPLACE VIEW degraded_events AS
SELECT
    to_timestamp(t0) AS at,
    request_id,
    route,
    total_ms,
    timeouts,
    cost_usd
FROM events
WHERE degraded = TRUE;


-- ─── Top expensive requests ───────────────────────────────────────────────
CREATE OR REPLACE VIEW top_cost_requests AS
SELECT
    to_timestamp(t0) AS at,
    request_id,
    route,
    query,
    total_ms,
    cost_usd,
    len(llm_calls) AS n_llm,
    len(mcp_calls) AS n_mcp
FROM events
LIMIT 25;


-- ─── Eval runs only ───────────────────────────────────────────────────────
CREATE OR REPLACE VIEW eval_runs AS
SELECT
    to_timestamp(t0) AS at,
    request_id,
    session_id AS test_id,
    route,
    query,
    total_ms,
    cost_usd,
    degraded,
    len(llm_calls) AS n_llm,
    len(mcp_calls) AS n_mcp
FROM events
WHERE route LIKE 'eval-%';


-- ─── Prod vs eval split ───────────────────────────────────────────────────
CREATE OR REPLACE VIEW eval_vs_prod AS
SELECT
    CASE
        WHEN route LIKE 'eval-%' THEN 'eval'
        ELSE 'prod'
    END AS kind,
    count(*) AS requests,
    round(sum(cost_usd), 4) AS total_usd,
    round(avg(cost_usd), 6) AS avg_usd_per_req,
    round(quantile_cont(total_ms, 0.95), 1) AS p95_ms
FROM events
GROUP BY kind;