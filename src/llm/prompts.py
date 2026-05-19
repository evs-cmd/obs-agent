"""Prompt registry — single source of truth for every LLM prompt.

All prompts live as Jinja templates in `templates/`. This module loads the
environment once and exposes `render(name, **context)` for callers.

Why Jinja + files (not inline Python constants):
  - Prompts diff cleanly in git
  - Variables substitute naturally (we already do this for synthesis)
  - One place to look when iterating on prompts
  - Long prompts don't pollute Python source files
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


def render(name: str, **context) -> str:
    """Render a prompt template by name. `name` is the file stem (without .j2)."""
    return _env().get_template(f"{name}.j2").render(**context)
