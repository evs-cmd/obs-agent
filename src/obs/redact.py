"""PII / secret redaction seam at the MCP response boundary.

Default implementation is a no-op pass-through. The signature is the
contract — when you need real redaction, swap the body of ``redact``
for a Presidio / regex / vendor-specific implementation; call sites
in ``src/mcp/hub.py`` stay unchanged.

Async because realistic implementations (vendor APIs, large-doc NLP)
will be I/O- or compute-heavy. Today's noop just returns immediately.

Why the MCP boundary and not pre-LLM:
  - One chokepoint: every observability signal crosses ``hub.call``.
  - A future agent that bypasses the LLM (e.g. dumps logs to the user
    directly) still gets redacted output.
  - Cost: redact runs once per MCP response, not once per LLM call —
    cheaper at high turn counts.
"""
from __future__ import annotations

from typing import Any


async def redact(payload: Any) -> Any:
    """No-op default. Replace this body when you need real redaction."""
    return payload
