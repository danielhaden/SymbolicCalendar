"""Shared context + Ollama detection for the agent layer.

Deliberately dependency-light (stdlib only) so the app can probe whether the
agents are usable — is Ollama running? is the model pulled? — without importing
LangGraph/LangChain. The heavy imports live behind functions in ``graph.py``.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "llama3.2"
DEFAULT_BASE_URL = "http://localhost:11434"

# The agent "kinds" the drawer's picker offers. "Auto" gets every tool; the
# others get their domain's tools plus the shared ones.
AGENT_KINDS = ("Auto", "Astrology", "Weather")


@dataclass(frozen=True)
class AgentContext:
    """Everything an agent needs from the app: where its data lives and which
    local model to run. Location is read live via ``current_location()`` at
    tool-call time, so it always reflects the app's current setting."""

    data_folder: Path
    model_name: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL


@dataclass(frozen=True)
class OllamaStatus:
    """Whether the local Ollama server is reachable and has the wanted model."""

    running: bool
    models: tuple[str, ...]
    has_model: bool
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.running and self.has_model


def _base_name(model: str) -> str:
    return model.split(":")[0]


def ollama_status(model_name: str = DEFAULT_MODEL,
                  base_url: str = DEFAULT_BASE_URL,
                  timeout: float = 3.0) -> OllamaStatus:
    """Probe the local Ollama server for reachability and the wanted model.
    Never raises — an offline/unreachable server just reports ``running=False``."""
    try:
        request = urllib.request.Request(f"{base_url}/api/tags")
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001 - report any failure as "not running"
        return OllamaStatus(False, (), False, str(exc))
    models = tuple(str(m.get("name", "")) for m in data.get("models", []))
    wanted = _base_name(model_name)
    has = any(_base_name(m) == wanted for m in models)
    return OllamaStatus(True, models, has)
