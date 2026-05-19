from __future__ import annotations

import json
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src import obs
from src.api.models import AskRequest
from src.graph.state import AgentState

router = APIRouter()


async def _stream_response(
    graph, user_query: str, session_id: str, request_id: str
) -> AsyncGenerator[str, None]:
    """SSE stream. Multi-mode astream emits:
      - mode="updates"  → node-level events (router decision, final node output)
      - mode="messages" → per-token LLM deltas from LangChain-native nodes
                          (router + incident synthesis use litellm/JSON-mode and
                           therefore don't emit token deltas; clients only see
                           their final `response` event).

    Observability: ``obs.start`` is called inside the generator so the
    ContextVar lives in the task Starlette uses to drive the iterator
    (setting it in the endpoint scope is not guaranteed to propagate).
    ``obs.finish`` runs in ``finally`` so the event is written even on
    early disconnect or exception.
    """
    # Raw query stays in ctx (audit trail); a sanitized copy goes to the graph.
    obs.start(request_id=request_id, session_id=session_id, query=user_query)
    sanitized_query = obs.sanitize_query(user_query)
    final_answer = ""

    initial_state: AgentState = {
        "messages": [],
        "route": "",
        "route_reasoning": "",
        "user_query": sanitized_query,
        "agent_output": "",
        "investigation": {},
        "memory_context": [],
        "rag_context": [],
    }
    config = {"configurable": {"thread_id": session_id}}

    yield f"data: {json.dumps({'type': 'session', 'session_id': session_id, 'request_id': request_id})}\n\n"

    try:
        async for mode, payload in graph.astream(
            initial_state, config=config, stream_mode=["updates", "messages"]
        ):
            if mode == "updates":
                for node_name, node_output in payload.items():
                    if node_name == "router":
                        route = node_output.get("route", "")
                        obs.set_route(route)
                        yield f"data: {json.dumps({'type': 'route', 'agent': route, 'reasoning': node_output.get('route_reasoning', '')})}\n\n"
                    else:
                        output = node_output.get("agent_output", "")
                        if output:
                            final_answer = output
                            yield f"data: {json.dumps({'type': 'response', 'agent': node_name, 'content': output})}\n\n"
            elif mode == "messages":
                chunk, meta = payload
                node = meta.get("langgraph_node", "") if isinstance(meta, dict) else ""
                if not node or node == "router":
                    continue
                content = getattr(chunk, "content", "")
                delta = content if isinstance(content, str) else ""
                if delta:
                    yield f"data: {json.dumps({'type': 'token', 'agent': node, 'delta': delta})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
    finally:
        obs.finish(final_answer)


@router.post("/ask")
async def ask(request: Request, body: AskRequest):
    graph = request.app.state.graph
    session_id = body.session_id or str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    return StreamingResponse(
        _stream_response(graph, body.message, session_id, request_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Session-Id": session_id,
            "X-Request-Id": request_id,
        },
    )


@router.get("/health")
async def health():
    return {"status": "ok"}
