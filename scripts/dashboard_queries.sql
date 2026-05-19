-- Starter queries for the DuckDB dashboard.
--
-- Copy any block into a notebook cell (UI: "+" → New notebook), run it,
-- then click the chart icon on the result tab to pick a visualization.
-- Views are bootstrapped by scripts/init.sql.

-- ─── 1. Traffic mix: requests per route ──────────────────────────────────
-- Suggested chart: bar (x=route, y=requests).
SELECT
    route,
    count(*)                    AS requests,
    round(avg(total_ms), 1)     AS avg_ms,
    round(sum(cost_usd), 4)     AS total_usd
FROM events
GROUP BY route
ORDER BY requests DESC;


-- ─── 2. Latency percentiles per route ────────────────────────────────────
-- Suggested chart: grouped bar (x=route, series=[p50,p95,p99]).
SELECT route, p50_ms, p95_ms, p99_ms, requests
FROM route_stats
ORDER BY p95_ms DESC;


-- ─── 3. Cost by model ────────────────────────────────────────────────────
-- Where is the spend going? Suggested chart: bar (x=model, y=total_usd).
SELECT *
FROM model_costs
ORDER BY total_usd DESC;


-- ─── 4. Tool latency + payload size ──────────────────────────────────────
-- Which MCP tools are the slow / heavy ones? Bar chart, sort by p95_ms.
SELECT *
FROM tool_stats
ORDER BY p95_ms DESC;


-- ─── 5. Degraded request rate (timeouts) ─────────────────────────────────
-- How often is NODE_TIMEOUT_MS firing? Suggested: KPI / bar by route.
SELECT
    route,
    count(*)                                              AS requests,
    sum(CAST(degraded AS INTEGER))                        AS degraded,
    round(100.0 * sum(CAST(degraded AS INTEGER)) / count(*), 1) AS degraded_pct
FROM events
GROUP BY route
ORDER BY degraded_pct DESC;


-- ─── 6. Eval vs. prod (drift detector) ───────────────────────────────────
-- Compares mean cost / latency between eval-tagged rows and live traffic.
SELECT *
FROM eval_vs_prod;


-- ─── 7. Cost outliers — top 25 most expensive requests ───────────────────
-- Drill-down candidates. Open `replays/{request_id}.json` for full snapshot,
-- or `make replay ID=<request_id>` to re-run.
SELECT request_id, route, total_ms, cost_usd, llm_calls, mcp_calls
FROM top_cost_requests;


-- ─── 8. LLM token mix (input vs. output) ─────────────────────────────────
-- Are we paying for prompt bloat or for long answers?
SELECT
    model,
    count(*)               AS calls,
    sum(in_tokens)         AS in_tokens,
    sum(out_tokens)        AS out_tokens,
    round(sum(call_usd), 4) AS total_usd
FROM llm_calls_flat
GROUP BY model
ORDER BY total_usd DESC;


-- ─── 9. Tool fan-out per request ─────────────────────────────────────────
-- How many MCP calls does each request average? High fan-out = candidate
-- for hub-level (tool, args) caching.
SELECT
    route,
    round(avg(len(mcp_calls)), 1) AS avg_mcp_calls,
    max(len(mcp_calls))           AS max_mcp_calls,
    round(avg(len(llm_calls)), 1) AS avg_llm_calls
FROM events
GROUP BY route
ORDER BY avg_mcp_calls DESC;


-- ─── 10. Duplicate MCP tool invocations within one request ───────────────
-- Counts how often the same tool is called more than once per request.
-- If this number is high, a (tool, args) cache in hub.call() would pay off.
WITH per_request AS (
    SELECT
        request_id,
        c.tool AS tool,
        count(*) AS calls_in_request
    FROM events, UNNEST(mcp_calls) AS t(c)
    GROUP BY request_id, c.tool
)
SELECT
    tool,
    count(*) AS requests_hitting_this_tool,
    round(avg(calls_in_request), 2) AS avg_calls_per_request,
    max(calls_in_request) AS max_calls_per_request
FROM per_request
GROUP BY tool
ORDER BY avg_calls_per_request DESC;
