.PHONY: help install test test-watch eval eval-quick dev fmt clean \
        up down restart logs ps build pull \
        app-logs replay \
        mcp mcp-sse obs-files

PYTHON := .venv/bin/python
PYTEST := .venv/bin/python -m pytest

help:  ## show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage: make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ─── Local dev ──────────────────────────────────────────────────────────────

install:  ## install deps (editable + dev + telemetry)
	uv pip install -e ".[dev,telemetry]"

dev:  ## run FastAPI locally (no Docker)
	$(PYTHON) -m uvicorn src.api.app:app --reload --port 8000

mcp:  ## run MCP server locally over stdio (for Claude Desktop / LangGraph Studio)
	$(PYTHON) -m src.mcp.server

mcp-sse:  ## run MCP server locally over SSE on port 9000 (http://localhost:9000/sse)
	$(PYTHON) -m src.mcp.server --sse

test:  ## unit tests only — no LLM calls
	$(PYTEST) tests/ -v

test-watch:  ## run tests in watch mode (requires pytest-watch)
	$(PYTHON) -m pytest_watch tests/

eval-quick:  ## router accuracy eval (cheap, ~6 mini-model calls)
	$(PYTEST) evals/ -v -m "not expensive"

eval:  ## full eval suite — routing + synthesis quality via deepeval (expensive, ~20 LLM calls)
	$(PYTEST) evals/ -v

fmt:  ## format code with ruff (if installed)
	ruff format src tests evals || true
	ruff check --fix src tests evals || true

gen-data:  ## regenerate mock observability data (5 incident scenarios)
	$(PYTHON) scripts/generate_mock_data.py

replay:  ## re-issue a saved request through /ask: make replay ID=<request_id>
ifndef ID
	@echo "usage: make replay ID=<request_id>  [URL=http://localhost:8000]" >&2
	@echo "       find IDs with: ls replays/ | sed 's/.json$$//' | head" >&2
	@exit 2
endif
	$(PYTHON) scripts/replay.py $(ID) $(if $(URL),--url $(URL))

clean:  ## remove caches and build artifacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name "*.egg-info" -prune -exec rm -rf {} +

# ─── Docker stack ───────────────────────────────────────────────────────────

.env:  ## bootstrap .env from .env.example if missing
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env from .env.example — edit OPENAI_API_KEY before running."; fi

obs-files:  ## ensure bind-mount targets exist as files (not auto-created dirs)
	@touch events.jsonl
	@mkdir -p replays

up: .env obs-files  ## start demo stack (app + mcp)
	docker compose up -d
	@echo ""
	@echo "  app:        http://localhost:8000/docs"
	@echo "  mcp:        http://localhost:9000/sse"
	@echo ""

down:  ## stop stack and remove containers
	docker compose down

restart:  ## restart stack
	docker compose restart

build:  ## rebuild app image
	docker compose build app

pull:  ## pull latest images
	docker compose pull

ps:  ## show running containers
	docker compose ps

logs:  ## tail all logs
	docker compose logs -f --tail=100

app-logs:  ## tail app logs only
	docker compose logs -f --tail=100 app
