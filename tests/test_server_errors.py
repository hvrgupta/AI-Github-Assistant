import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

os.environ.setdefault("GITHUB_TOKEN", "test-token")

from assistant.github_mcp_server import EnvironmentTokenVerifier, call_github
from assistant.services.github_service import GithubService
import assistant.github_mcp_server as github_mcp_server


class GitHubServerErrorTests(unittest.IsolatedAsyncioTestCase):
    def test_write_operations_are_not_exposed_or_implemented(self):
        self.assertFalse(hasattr(github_mcp_server, "create_issue"))
        self.assertFalse(hasattr(GithubService, "create_issue"))
        self.assertFalse(hasattr(github_mcp_server, "confirm_issue"))

    async def test_token_verifier_uses_configured_token_and_scopes(self):
        verifier = EnvironmentTokenVerifier()
        with (
            patch(
                "assistant.github_mcp_server.MCP_ACCESS_TOKEN",
                "configured-test-token"
            ),
            patch(
                "assistant.github_mcp_server.MCP_ACCESS_SCOPES",
                ("github:read",)
            )
        ):
            access_token = await verifier.verify_token(
                "configured-test-token"
            )

        self.assertIsNotNone(access_token)
        self.assertEqual(
            access_token.scopes,
            ["github:read"]
        )

    async def test_token_verifier_rejects_unconfigured_token(self):
        verifier = EnvironmentTokenVerifier()
        with patch(
            "assistant.github_mcp_server.MCP_ACCESS_TOKEN",
            "configured-test-token"
        ):
            access_token = await verifier.verify_token("wrong-token")

        self.assertIsNone(access_token)

    async def test_transient_read_failure_is_retried(self):
        attempts = 0

        async def request():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ReadTimeout("temporary timeout")
            return {"ok": True}

        with patch(
            "assistant.github_mcp_server.asyncio.sleep",
            new_callable=AsyncMock
        ) as sleep:
            result = await call_github(
                "repository search",
                request,
                retry_safe=True
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(attempts, 2)
        sleep.assert_awaited_once_with(0.5)

    async def test_github_error_message_is_reported(self):
        request = httpx.Request("GET", "https://api.github.com/search/repositories")
        response = httpx.Response(
            404,
            json={"message": "Not Found"},
            request=request
        )
        error = httpx.HTTPStatusError(
            "request failed",
            request=request,
            response=response
        )

        async def request_call():
            raise error

        with self.assertRaisesRegex(
            RuntimeError,
            "GitHub returned HTTP 404: Not Found"
        ):
            await call_github(
                "pull request lookup",
                request_call,
                retry_safe=True
            )


if __name__ == "__main__":
    unittest.main()
