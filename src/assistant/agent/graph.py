import os
from typing import Any

from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver

from assistant.agent.prompts import SYSTEM_PROMPT


def create_read_only_agent(
    tools: list[Any],
    checkpointer: InMemorySaver,
    model: str | None = None,
):
    return create_agent(
        model=model or os.getenv(
            "OPENAI_MODEL",
            "openai:gpt-4.1-mini",
        ),
        tools=tools,
        checkpointer=checkpointer,
        system_prompt=SYSTEM_PROMPT,
    )
