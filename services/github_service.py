import os
import httpx

from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
BASE_URL = "https://api.github.com"

class GithubService:

    def __init__(self):
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }

        if GITHUB_TOKEN:
            self.headers["Authorization"] = (
                f"Bearer {GITHUB_TOKEN}"
            )

    async def search_repo(self, query:str, limit: int = 5) -> dict:
        url = f"{BASE_URL}/search/repositories"

        params = {
                "q": query,
                "per_page": limit
            }

        async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url, headers=self.headers, params=params)

                response.raise_for_status()
                data = response.json()

                return {
                "total_count": data["total_count"],
                "repositories": [
                    {
                        "name": repo["full_name"],
                        "description": repo["description"],
                        "url": repo["html_url"],
                        "stars": repo["stargazers_count"],
                        "language": repo["language"]
                    }
                    for repo in data["items"]
                ]}  

    async def get_pull_request(self, owner: str, repo: str, pull_number: int) -> dict:
        url = f"{BASE_URL}/repos/{owner}/{repo}/pulls/{pull_number}"

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()

            pr = response.json()

            return {
                "number": pr["number"],
                "title": pr["title"],
                "state": pr["state"],
                "author": pr["user"]["login"],
                "description": pr["body"],
                "url": pr["html_url"],
                "created_at": pr["created_at"],
                "updated_at": pr["updated_at"],
                "merged": pr["merged"],
                "base_branch": pr["base"]["ref"],
                "head_branch": pr["head"]["ref"]
            } 

    async def create_issue(self, owner: str, repo: str, title: str, body: str) -> dict:

        url = f"{BASE_URL}/repos/{owner}/{repo}/issues"

        payload = {
            "title": title,
            "body": body
        }

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                url,
                headers=self.headers,
                json=payload
            )
            response.raise_for_status()

            issue = response.json()

            return {
                "number": issue["number"],
                "title": issue["title"],
                "url": issue["html_url"],
                "state": issue["state"],
                "created_at": issue["created_at"]
            }