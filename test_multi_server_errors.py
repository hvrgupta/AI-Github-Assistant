import unittest
from types import SimpleNamespace
from unittest.mock import patch

import anyio
import httpx2

from multi_server_agent import (
    MCPRequestError,
    MCPConnectionManager,
    call_mcp_tool
)


class FakeMCPClient:
    def __init__(self, result=None, error=None, action=None):
        self.result = result
        self.error = error
        self.action = action
        self.calls = 0
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.closed = True

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
        self.closed = True

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

        self.assertIs(connection.client, replacement)

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

        with patch("multi_server_agent.MCP_REQUEST_TIMEOUT_SECONDS", 0.001):
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
