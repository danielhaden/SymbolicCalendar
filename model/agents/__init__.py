"""Local agent layer: LangGraph + Ollama agents that answer astrology and
weather questions using the app's own data.

``ollama_status`` / ``AgentContext`` are dependency-light (usable to gate the
feature); ``build_agent`` / ``stream_reply`` pull in LangGraph only when called.
"""

from __future__ import annotations

from .context import (
    AGENT_KINDS,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    AgentContext,
    OllamaStatus,
    ollama_status,
)
from .graph import build_agent, stream_reply

__all__ = [
    "AGENT_KINDS",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "AgentContext",
    "OllamaStatus",
    "ollama_status",
    "build_agent",
    "stream_reply",
]
