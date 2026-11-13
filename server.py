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

if __name__ == "__main__":
    mcp.run()
