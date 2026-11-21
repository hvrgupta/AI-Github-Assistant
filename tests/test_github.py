
import asyncio
from assistant.services.github_service import GithubService


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


if __name__ == "__main__":
    asyncio.run(main())
