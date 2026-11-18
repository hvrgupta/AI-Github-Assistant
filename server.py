from mcp.server import MCPServer

from services.github_service import GithubService

mcp = MCPServer("Github Developer Assistant")
github = GithubService()

@mcp.tool()
async def search_repo(query: str, limit: int = 5) -> dict:
    """ 
    Search GitHub repositories using a search query.
    Args:
        query: GitHub repository search query.
        limit: Maximum number of repositories to return.
    """
    if not query.strip():
        raise ValueError("Search query cannot be empty")

    if not 1 <= limit <= 10:
        raise ValueError("Limit must be between 1 and 10")

    try:
        result = await github.search_repo(
            query=query,
            limit=limit
        )

        return result

    except Exception as e:
        raise RuntimeError(
            f"GitHub repository search failed: {e}"
        ) from e

@mcp.tool()
async def get_pull_request(owner: str, repo: str, pull_number: int) -> dict:
    """Get details of a GitHub pull request."""

    if not owner.strip() or not repo.strip():
        raise ValueError("Owner and repository are required")

    if pull_number < 1:
        raise ValueError("Pull request number must be positive")

    return await github.get_pull_request(
        owner=owner,
        repo=repo,
        pull_number=pull_number
    )

@mcp.tool()
async def create_issue(owner: str, repo: str, title: str, body: str) -> dict:
    """Create a new issue in a GitHub repository."""

    if not owner.strip() or not repo.strip():
        raise ValueError("Owner and repository are required")

    if not title.strip():
        raise ValueError("Issue title cannot be empty")

    return await github.create_issue(
        owner=owner,
        repo=repo,
        title=title,
        body=body
    )

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
    return await github.get_repository_readme(
        owner,
        repo
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

if __name__ == "__main__":
     mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8000
    )
