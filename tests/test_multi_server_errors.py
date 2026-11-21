import unittest
from types import SimpleNamespace
from unittest.mock import patch

import anyio
import httpx2

from assistant.mcp.mcp_client_runtime import (
    MCPConnectionManager,
    MCPRequestError,
    call_mcp_tool,
    create_args_schema,
    create_langchain_tool,
)


class FakeMCPClient:
    def __init__(self, result=None, error=None, action=None, close_error=None):
        self.result = result
        self.error = error
        self.action = action
        self.close_error = close_error
        self.calls = 0
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error

    async def list_tools(self):
        return SimpleNamespace(tools=[])

    async def call_tool(self, tool_name, arguments, read_timeout_seconds=None):
        self.calls += 1
        if self.action is not None:
            return await self.action()
        if self.error is not None:
            raise self.error
        return self.result


class ManagedFakeMCPClient(FakeMCPClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await super().__aexit__(exc_type, exc_value, traceback)

    async def list_tools(self):
        return SimpleNamespace(tools=[])


class FakeConnection(MCPConnectionManager):
    def __init__(self, client, replacement=None, reconnect_error=None):
        self._clients = iter([client, replacement])
        self.reconnect_error = reconnect_error
        self.reconnect_calls = 0
        super().__init__("Filesystem", self._create_client)

    def _create_client(self):
        client = next(self._clients)
        if client is None:
            raise RuntimeError("No fake client configured")
        return client

    async def _reconnect(self):
        self.reconnect_calls += 1
        if self.reconnect_error is not None:
            raise self.reconnect_error
        await super()._reconnect()


def tool_result(*, is_error=False, text=""):
    return SimpleNamespace(
        is_error=is_error,
        content=[SimpleNamespace(text=text)],
        structured_content=None
    )


class MCPReconnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_recreates_session_after_disconnect(self):
        original = ManagedFakeMCPClient(error=anyio.EndOfStream())
        replacement = ManagedFakeMCPClient(result=tool_result())
        clients = iter([original, replacement])
        connection = MCPConnectionManager(
            "Filesystem",
            lambda: next(clients)
        )
        self.addAsyncCleanup(connection.close)

        await connection.connect()
        result = await call_mcp_tool(
            connection,
            "list_directory",
            {"path": "."},
            retry_safe=True
        )

        self.assertFalse(result.is_error)
        self.assertIs(connection.client, replacement)
        self.assertTrue(original.closed)
        self.assertEqual(original.calls, 1)
        self.assertEqual(replacement.calls, 1)

    async def test_transport_error_during_shutdown_is_reported_not_raised(self):
        request = httpx2.Request("POST", "http://127.0.0.1:8000/mcp")
        connection_error = httpx2.ConnectError(
            "All connection attempts failed",
            request=request
        )
        client = ManagedFakeMCPClient(
            result=tool_result(),
            close_error=ExceptionGroup(
                "transport task failed",
                [connection_error]
            )
        )
        connection = MCPConnectionManager(
            "GitHub",
            lambda: client
        )
        self.addAsyncCleanup(connection.close)

        await connection.connect()
        await connection.close()

        self.assertTrue(client.closed)

    async def test_unexpected_shutdown_error_is_not_suppressed(self):
        client = ManagedFakeMCPClient(
            result=tool_result(),
            close_error=RuntimeError("unexpected cleanup failure")
        )
        connection = MCPConnectionManager(
            "Filesystem",
            lambda: client
        )

        await connection.connect()
        with self.assertRaisesRegex(
            RuntimeError,
            "unexpected cleanup failure"
        ):
            await connection.close()

    async def test_server_tool_error_text_is_preserved(self):
        client = FakeMCPClient(
            result=tool_result(is_error=True, text="Permission denied")
        )
        connection = FakeConnection(client)
        self.addAsyncCleanup(connection.close)

        with self.assertRaisesRegex(MCPRequestError, "Permission denied"):
            await call_mcp_tool(
                connection,
                "write_file",
                {"path": "report.md"},
                retry_safe=False
            )

        self.assertEqual(client.calls, 1)
        self.assertEqual(connection.reconnect_calls, 0)

    async def test_mcp_tool_errors_are_returned_to_the_agent(self):
        class ErrorConnection:
            server_name = "GitHub"

            async def call_tool(self, *_args, **_kwargs):
                return tool_result(
                    is_error=True,
                    text="Limit must be between 1 and 10"
                )

        mcp_tool = SimpleNamespace(
            name="search_repo",
            description="Search repositories",
            annotations=SimpleNamespace(read_only_hint=True),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"}
                },
                "required": ["query"]
            }
        )
        langchain_tool = create_langchain_tool(
            ErrorConnection(),
            mcp_tool
        )

        result = await langchain_tool.ainvoke(
            {"query": "example"}
        )

        self.assertIn("Tool error:", result)
        self.assertIn("Limit must be between 1 and 10", result)

    async def test_argument_validation_errors_are_returned_to_the_agent(self):
        class UnusedConnection:
            async def call_tool(self, *_args, **_kwargs):
                self.fail("Invalid arguments must not reach the MCP server")

        def reject_empty_path(_tool_name, _arguments):
            raise ValueError("path: Filesystem paths must be non-empty strings.")

        mcp_tool = SimpleNamespace(
            name="read_file",
            description="Read a file",
            annotations=SimpleNamespace(read_only_hint=True),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"}
                },
                "required": ["path"]
            }
        )
        langchain_tool = create_langchain_tool(
            UnusedConnection(),
            mcp_tool,
            argument_validator=reject_empty_path
        )

        result = await langchain_tool.ainvoke({"path": ""})

        self.assertIn("Tool error:", result)
        self.assertIn("non-empty strings", result)

    async def test_numeric_tool_limits_are_preserved_from_json_schema(self):
        mcp_tool = SimpleNamespace(
            name="search_repo",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 5
                    }
                }
            }
        )
        args_schema = create_args_schema(mcp_tool)

        with self.assertRaises(ValueError):
            args_schema(limit=11)

    async def test_read_only_transport_failure_reconnects_and_retries(self):
        original = FakeMCPClient(error=httpx2.ReadTimeout("temporary timeout"))
        replacement = FakeMCPClient(result=tool_result())
        connection = FakeConnection(original, replacement=replacement)
        self.addAsyncCleanup(connection.close)

        result = await call_mcp_tool(
            connection,
            "list_directory",
            {"path": "."},
            retry_safe=True
        )

        self.assertFalse(result.is_error)
        self.assertEqual(connection.reconnect_calls, 1)
        self.assertEqual(original.calls, 1)
        self.assertEqual(replacement.calls, 1)
        self.assertTrue(original.closed)

    async def test_read_only_deadline_reconnects_and_retries(self):
        async def delayed_result():
            await anyio.sleep(0.02)
            return tool_result()

        original = FakeMCPClient(action=delayed_result)
        replacement = FakeMCPClient(result=tool_result())
        connection = FakeConnection(original, replacement=replacement)
        self.addAsyncCleanup(connection.close)

        with patch(
            "assistant.mcp.mcp_client_runtime.MCP_REQUEST_TIMEOUT_SECONDS",
            0.001
        ):
            result = await call_mcp_tool(
                connection,
                "list_directory",
                {"path": "."},
                retry_safe=True
            )

        self.assertFalse(result.is_error)
        self.assertEqual(connection.reconnect_calls, 1)
        self.assertEqual(original.calls, 1)
        self.assertEqual(replacement.calls, 1)

    async def test_mutating_timeout_reconnects_without_replay(self):
        original = FakeMCPClient(error=httpx2.ReadTimeout("response timed out"))
        replacement = FakeMCPClient(result=tool_result())
        connection = FakeConnection(original, replacement=replacement)
        self.addAsyncCleanup(connection.close)

        with self.assertRaisesRegex(MCPRequestError, "outcome may be unknown"):
            await call_mcp_tool(
                connection,
                "write_file",
                {"path": "report.md"},
                retry_safe=False
            )

        self.assertEqual(connection.reconnect_calls, 1)
        self.assertEqual(original.calls, 1)
        self.assertEqual(replacement.calls, 0)

    async def test_reconnect_failure_has_actionable_message(self):
        original = FakeMCPClient(error=anyio.EndOfStream())
        connection = FakeConnection(
            original,
            replacement=None,
            reconnect_error=OSError("server not running")
        )
        self.addAsyncCleanup(connection.close)

        with self.assertRaisesRegex(
            MCPRequestError,
            "could not reconnect.*Check that the server is running"
        ):
            await call_mcp_tool(
                connection,
                "list_directory",
                {"path": "."},
                retry_safe=True
            )

        self.assertEqual(connection.reconnect_calls, 1)


if __name__ == "__main__":
    unittest.main()
