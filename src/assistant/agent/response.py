import json
from typing import Any


def format_agent_response(result: Any) -> str:
    value = getattr(result, "value", result)
    messages = value.get("messages", []) if isinstance(value, dict) else []
    for message in reversed(messages):
        if getattr(message, "type", None) == "ai":
            content = getattr(message, "content", "")
            if isinstance(content, str):
                return content
            return json.dumps(content, ensure_ascii=False, default=str)
    raise RuntimeError(
        "Agent result did not contain a final AI message."
    )
