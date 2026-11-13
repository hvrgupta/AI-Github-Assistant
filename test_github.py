
import asyncio
from services.github_service import GithubService


async def main():

    github = GithubService()

    result = await github.search_repo(
        query="langgraph language:python",
        limit=5
    )

    for repo in result["repositories"]:

        print("\nRepository:", repo["name"])
        print("Stars:", repo["stars"])
        print("Language:", repo["language"])
        print("URL:", repo["url"])


asyncio.run(main())
