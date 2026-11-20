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

    try:
        result = await github.search_repo(
            query=query,
            limit=limit
        )
    except Exception as e:
        raise RuntimeError(
            f"GitHub repository search failed: {e}"
        ) from e

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

    result = await github.get_pull_request(
        owner=owner,
        repo=repo,
        pull_number=pull_number
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

    result = await github.create_issue(
        owner=owner,
        repo=repo,
        title=title,
        body=body
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
    return await github.get_repository_readme(owner, repo)


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
