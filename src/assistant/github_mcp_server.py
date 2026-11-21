import asyncio
import hmac
import os
from collections.abc import Awaitable, Callable
from typing import Annotated, TypeVar

import httpx
from dotenv import load_dotenv
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, BaseModel, Field

from assistant.services.github_service import GithubService

load_dotenv()

SERVER_NAME = "GitHub Developer Assistant"
SERVER_HOST = os.getenv("MCP_SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("MCP_SERVER_PORT", "8000"))
RESOURCE_URL = os.getenv(
    "MCP_RESOURCE_URL",
    f"http://{SERVER_HOST}:{SERVER_PORT}/mcp"
)
AUTH_ISSUER_URL = os.getenv(
    "MCP_AUTH_ISSUER_URL",
    "https://auth.example.com"
)
MCP_ACCESS_TOKEN = os.getenv("MCP_ACCESS_TOKEN")
MCP_ACCESS_SCOPES = tuple(
    scope.strip()
    for scope in os.getenv(
        "MCP_ACCESS_SCOPES",
        "github:read"
    ).split(",")
    if scope.strip()
)
GITHUB_REQUEST_TIMEOUT_SECONDS = 20
GITHUB_MAX_ATTEMPTS = 3
GITHUB_RETRY_BASE_DELAY_SECONDS = 0.5

T = TypeVar("T")


# Tool input/output schemas
class PullRequestOutput(BaseModel):
    number: int
    title: str
    state: str
    author: str
    description: str | None = None
    url: str
    created_at: str
    updated_at: str
    merged: bool
    base_branch: str
    head_branch: str


class RepositoryOutput(BaseModel):
    name: str
    description: str | None = None
    url: str
    stars: int
    language: str | None = None


class RepositorySearchOutput(BaseModel):
    total_count: int
    repositories: list[RepositoryOutput]


class RepositoryDetailsOutput(BaseModel):
    name: str
    description: str | None = None
    url: str
    stars: int
    forks: int
    language: str | None = None
    default_branch: str
    updated_at: str


class CommitOutput(BaseModel):
    sha: str
    message: str
    author: str | None = None
    url: str
    committed_at: str | None = None


class CommitListOutput(BaseModel):
    commits: list[CommitOutput]


class IssueOutput(BaseModel):
    number: int
    title: str
    url: str
    state: str
    author: str
    created_at: str
    updated_at: str


class IssueListOutput(BaseModel):
    issues: list[IssueOutput]


class FileContentOutput(BaseModel):
    path: str
    name: str
    sha: str
    size: int
    content: str
    url: str


class EnvironmentTokenVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        if not MCP_ACCESS_TOKEN or not hmac.compare_digest(
            token.encode("utf-8"),
            MCP_ACCESS_TOKEN.encode("utf-8")
        ):
            return None

        return AccessToken(
            token=token,
            client_id="configured-mcp-client",
            scopes=list(MCP_ACCESS_SCOPES),
            resource=RESOURCE_URL
        )


def require_scope(required_scope: str) -> None:
    access_token = get_access_token()
    if access_token is None:
        raise PermissionError("Authentication required")
    if required_scope not in access_token.scopes:
        raise PermissionError(f"Missing required permission: {required_scope}")


# MCP server and service dependencies
mcp = MCPServer(
    SERVER_NAME,
    token_verifier=EnvironmentTokenVerifier(),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(AUTH_ISSUER_URL),
        resource_server_url=AnyHttpUrl(RESOURCE_URL),
        required_scopes=["github:read"],
        validate_token_resource=True
    )
)

github: GithubService = GithubService()


def _github_http_error_message(error: httpx.HTTPStatusError) -> str:
    status_code = error.response.status_code
    try:
        response_body = error.response.json()
    except ValueError:
        response_body = None

    detail = (
        response_body.get("message")
        if isinstance(response_body, dict)
        else None
    )
    if detail:
        return f"GitHub returned HTTP {status_code}: {detail}"
    return f"GitHub returned HTTP {status_code}"


async def call_github(
    operation: str,
    request: Callable[[], Awaitable[T]],
    *,
    retry_safe: bool
) -> T:
    """Bound GitHub API calls and retry only safe, transient failures."""
    attempts = GITHUB_MAX_ATTEMPTS if retry_safe else 1

    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.wait_for(
                request(),
                timeout=GITHUB_REQUEST_TIMEOUT_SECONDS
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            message = _github_http_error_message(exc)

            if not retry_safe or status_code < 500 or attempt == attempts:
                raise RuntimeError(
                    f"GitHub {operation} failed: {message}"
                ) from exc

            await asyncio.sleep(
                GITHUB_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            )
        except (httpx.TimeoutException, httpx.TransportError, TimeoutError) as exc:
            if not retry_safe:
                raise RuntimeError(
                    f"GitHub {operation} timed out or lost its connection. "
                    "It was not retried because it may have changed GitHub "
                    f"state; its outcome may be unknown. Details: {exc}"
                ) from exc

            if attempt == attempts:
                raise RuntimeError(
                    f"GitHub {operation} failed after {attempts} attempts. "
                    f"Check the GitHub API connection. Details: {exc}"
                ) from exc

            await asyncio.sleep(
                GITHUB_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            )

    raise RuntimeError(f"GitHub {operation} failed without a result")


# MCP tools
@mcp.tool(
    annotations=ToolAnnotations(
        title="Search GitHub repositories",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True
    )
)
async def search_repo(
    query: str,
    limit: Annotated[int, Field(ge=1, le=10)] = 5
) -> RepositorySearchOutput:
    """
    Search GitHub repositories using a search query.

    Args:
        query: GitHub repository search query.
        limit: Maximum number of repositories to return.
    """
    require_scope("github:read")
    if not query.strip():
        raise ValueError("Search query cannot be empty")

    if not 1 <= limit <= 10:
        raise ValueError("Limit must be between 1 and 10")

    result = await call_github(
        "repository search",
        lambda: github.search_repo(
            query=query,
            limit=limit
        ),
        retry_safe=True
    )

    return RepositorySearchOutput(**result)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get pull request details",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True
    )
)
async def get_pull_request(
    owner: str,
    repo: str,
    pull_number: int
) -> PullRequestOutput:
    """Get details of a GitHub pull request."""

    require_scope("github:read")

    if not owner.strip() or not repo.strip():
        raise ValueError("Owner and repository are required")

    if pull_number < 1:
        raise ValueError("Pull request number must be positive")

    result = await call_github(
        "pull request lookup",
        lambda: github.get_pull_request(
            owner=owner,
            repo=repo,
            pull_number=pull_number
        ),
        retry_safe=True
    )

    return PullRequestOutput(**result)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get repository details",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True
    )
)
async def get_repository(
    owner: str,
    repo: str
) -> RepositoryDetailsOutput:
    """Get metadata for a GitHub repository."""
    require_scope("github:read")
    if not owner.strip() or not repo.strip():
        raise ValueError("Owner and repository are required")
    result = await call_github(
        "repository details",
        lambda: github.get_repository(owner, repo),
        retry_safe=True
    )
    return RepositoryDetailsOutput(**result)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get repository file contents",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True
    )
)
async def get_file_contents(
    owner: str,
    repo: str,
    path: str,
    ref: str | None = None
) -> FileContentOutput:
    """Retrieve text file contents from a GitHub repository."""
    require_scope("github:read")
    if not owner.strip() or not repo.strip() or not path.strip():
        raise ValueError("Owner, repository, and path are required")
    result = await call_github(
        "repository file contents",
        lambda: github.get_file_contents(owner, repo, path, ref),
        retry_safe=True
    )
    return FileContentOutput(**result)


@mcp.tool(
    annotations=ToolAnnotations(
        title="List repository commits",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True
    )
)
async def list_commits(
    owner: str,
    repo: str,
    branch: str | None = None,
    limit: int = 10
) -> CommitListOutput:
    """List recent commits in a GitHub repository."""
    require_scope("github:read")
    if not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100")
    result = await call_github(
        "commit list",
        lambda: github.list_commits(owner, repo, branch, limit),
        retry_safe=True
    )
    return CommitListOutput(**result)


@mcp.tool(
    annotations=ToolAnnotations(
        title="List repository issues",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True
    )
)
async def list_issues(
    owner: str,
    repo: str,
    state: str = "open",
    limit: int = 10
) -> IssueListOutput:
    """List issues in a GitHub repository."""
    require_scope("github:read")
    if state not in {"open", "closed", "all"}:
        raise ValueError("State must be open, closed, or all")
    if not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100")
    result = await call_github(
        "issue list",
        lambda: github.list_issues(owner, repo, state, limit),
        retry_safe=True
    )
    return IssueListOutput(**result)


# MCP resources and prompts
@mcp.resource("assistant://guidelines")
def get_assistant_guidelines() -> str:
    """
    Provide development guidelines for the GitHub assistant.
    """

    return """
    GitHub Developer Assistant Guidelines

    1. Never invent GitHub repository information.
    2. This server is strictly read-only.
    3. Never claim that an issue, file, commit, or repository was modified.
    4. Clearly distinguish retrieved facts from explanations.
    5. Include repository names and GitHub URLs when available.
    6. Prefer factual information from GitHub API responses.
    7. State explicitly when GitHub information could not be retrieved.
    """


@mcp.resource("github://repos/{owner}/{repo}/readme")
async def get_repository_readme(owner: str, repo: str) -> str:
    return await call_github(
        "repository README lookup",
        lambda: github.get_repository_readme(owner, repo),
        retry_safe=True
    )


@mcp.prompt()
def review_pull_request(owner: str, repo: str, pull_number: int) -> str:
    return f"""
    Review pull request #{pull_number}
    in repository {owner}/{repo}.

    Analyze:
    1. Code quality
    2. Potential bugs
    3. Security vulnerabilities
    4. Performance issues
    5. Suggested improvements

    Provide actionable feedback with explanations.
    """


def run_server() -> None:
    if not MCP_ACCESS_TOKEN:
        raise RuntimeError(
            "MCP_ACCESS_TOKEN must be configured before starting the server."
        )
    unsupported_scopes = set(MCP_ACCESS_SCOPES) - {"github:read"}
    if unsupported_scopes:
        raise RuntimeError(
            "MCP_ACCESS_SCOPES contains unsupported scope(s): "
            + ", ".join(sorted(unsupported_scopes))
        )
    if "github:read" not in MCP_ACCESS_SCOPES:
        raise RuntimeError(
            "MCP_ACCESS_SCOPES must include github:read."
        )
    mcp.run(
        transport="streamable-http",
        host=SERVER_HOST,
        port=SERVER_PORT
    )


if __name__ == "__main__":
    run_server()
