# Compatibility import: the implementation remains in the existing
# multi-server module until connection lifecycle extraction is completed.
from assistant.mcp.mcp_client_runtime import (
    MCPConnectionManager,
    MCPRequestError,
    MCPServerDisconnectedError,
)

__all__ = [
    "MCPConnectionManager",
    "MCPRequestError",
    "MCPServerDisconnectedError",
]
