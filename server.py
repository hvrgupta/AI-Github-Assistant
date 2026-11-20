import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx
from typing import Annotated
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Elicit, Resolve
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, BaseModel, Field

from services.github_service import GithubService


SERVER_NAME = "GitHub Developer Assistant"
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8000
RESOURCE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}/mcp"
AUTH_ISSUER_URL = "https://auth.example.com"
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


class IssueOutput(BaseModel):
    number: int
    title: str
    url: str
    state: str
    created_at: str


class CreateIssueOutput(BaseModel):
    created: bool
    message: str
    issue: IssueOutput | None = None


class IssueConfirmation(BaseModel):
    confirm: bool = Field(
        description="Do you want to create this GitHub issue?"
    )


# Authentication
KNOWN_TOKENS: dict[str, AccessToken] = {
    "dev-read-token": AccessToken(
        token="dev-read-token",
        client_id="read-client",
        scopes=["github:read"],
        resource=RESOURCE_URL
    ),
    "dev-write-token": AccessToken(
        token="dev-write-token",
        client_id="write-client",
        scopes=["github:read", "github:write"],
        resource=RESOURCE_URL
    )
}


class StaticTokenVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        return KNOWN_TOKENS.get(token)


def require_scope(required_scope: str) -> None:
    access_token = get_access_token()
    if access_token is None:
        raise PermissionError("Authentication required")
    if required_scope not in access_token.scopes:
        raise PermissionError(f"Missing required permission: {required_scope}")


# MCP server and service dependencies
mcp = MCPServer(
    SERVER_NAME,
    token_verifier=StaticTokenVerifier(),
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
async def search_repo(query: str, limit: int = 5) -> RepositorySearchOutput:
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


async def confirm_issue(
    owner: str,
    repo: str,
    title: str
) -> IssueConfirmation | Elicit[IssueConfirmation]:
    require_scope("github:write")

    return Elicit(
        f"Do you want to create the issue '{title}' in {owner}/{repo}?",
        IssueConfirmation
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create a GitHub issue",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True
    )
)
async def create_issue(
    owner: str,
    repo: str,
    title: str,
    body: str,
    confirmation: Annotated[
        IssueConfirmation,
        Resolve(confirm_issue)
    ]
) -> CreateIssueOutput:
    """Create a new issue in a GitHub repository."""
    require_scope("github:write")

    if not confirmation.confirm:
        return CreateIssueOutput(
            created=False,
            message="Issue creation cancelled by user."
        )

    if not owner.strip() or not repo.strip():
        raise ValueError("Owner and repository are required")

    if not title.strip():
        raise ValueError("Issue title cannot be empty")

    result = await call_github(
        "issue creation",
        lambda: github.create_issue(
            owner=owner,
            repo=repo,
            title=title,
            body=body
        ),
        retry_safe=False
    )
    return CreateIssueOutput(
        created=True,
        message="Issue created successfully.",
        issue=IssueOutput(**result)
    )


# MCP resources and prompts
@mcp.resource("assistant://guidelines")
def get_assistant_guidelines() -> str:
    """
    Provide development guidelines for the GitHub assistant.
    """

    return """
    GitHub Developer Assistant Guidelines

    1. Never invent GitHub repository information.
    2. Verify repository owner and name before write operations.
    3. Explain proposed changes clearly.
    4. Never create an issue without human approval.
    5. Use clear, descriptive issue titles.
    6. Include reproduction steps when reporting bugs.
    7. Prefer factual information from GitHub API responses.
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
    mcp.run(
        transport="streamable-http",
        host=SERVER_HOST,
        port=SERVER_PORT
    )


if __name__ == "__main__":
    run_server()
