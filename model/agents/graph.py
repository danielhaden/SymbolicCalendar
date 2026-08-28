"""LangGraph agent construction and streaming.

Right now each agent is LangGraph's prebuilt ReAct graph (an LLM + tool loop).
That's the seam meant to grow: swap ``create_react_agent`` here for a custom
``StateGraph`` (planning nodes, memory, sub-agents, human-in-the-loop) without
touching the tools, prompts, or UI. LangChain/LangGraph are imported lazily so
importing this module never requires them — only building an agent does.
"""

from __future__ import annotations

from datetime import date
from typing import Iterator

from ..daylight import current_location
from .context import AgentContext
from .prompts import system_prompt
from .tools import make_tools


def _grounding() -> str:
    """A live date/location preamble so even a small model resolves relative
    dates against reality instead of guessing from its training data."""
    loc = current_location()
    return (f"Right now it is {date.today().isoformat()} and the location is "
            f"{loc.name} (latitude {loc.latitude:.4f}, longitude "
            f"{loc.longitude:.4f}, timezone {loc.tz_name}). Use these for any "
            f"relative date or 'here'.")


def build_agent(kind: str, ctx: AgentContext):
    """Compile the agent graph for ``kind`` against the local Ollama model. The
    returned object is a LangGraph app you drive with :func:`stream_reply`."""
    from langchain_ollama import ChatOllama
    from langgraph.prebuilt import create_react_agent

    llm = ChatOllama(model=ctx.model_name, base_url=ctx.base_url, temperature=0.2)
    prompt = f"{_grounding()}\n\n{system_prompt(kind)}"
    return create_react_agent(llm, make_tools(ctx, kind), prompt=prompt)


def stream_reply(agent, message: str) -> Iterator[str]:
    """Run ``message`` through the agent, yielding the assistant's reply text in
    chunks as the model generates it (tool-call chatter is skipped)."""
    from langchain_core.messages import AIMessage, HumanMessage

    for chunk, _meta in agent.stream(
            {"messages": [HumanMessage(content=message)]},
            stream_mode="messages"):
        if isinstance(chunk, AIMessage):
            content = chunk.content
            if isinstance(content, str) and content:
                yield content
