import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

os.environ.setdefault("GITHUB_TOKEN", "test-token")

from server import call_github


class GitHubServerErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_read_failure_is_retried(self):
        attempts = 0

        async def request():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ReadTimeout("temporary timeout")
            return {"ok": True}

        with patch(
            "server.asyncio.sleep",
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

    async def test_issue_creation_timeout_is_not_retried(self):
        attempts = 0

        async def request():
            nonlocal attempts
            attempts += 1
            raise httpx.ReadTimeout("response timed out")

        with self.assertRaisesRegex(RuntimeError, "outcome may be unknown"):
            await call_github(
                "issue creation",
                request,
                retry_safe=False
            )

        self.assertEqual(attempts, 1)

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
