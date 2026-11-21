import os
import httpx
import base64

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

    async def get_repository(self, owner: str, repo: str) -> dict:
        url = f"{BASE_URL}/repos/{owner}/{repo}"
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            repository = response.json()
        return {
            "name": repository["full_name"],
            "description": repository["description"],
            "url": repository["html_url"],
            "stars": repository["stargazers_count"],
            "forks": repository["forks_count"],
            "language": repository["language"],
            "default_branch": repository["default_branch"],
            "updated_at": repository["updated_at"]
        }

    async def get_file_contents(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str | None
    ) -> dict:
        url = f"{BASE_URL}/repos/{owner}/{repo}/contents/{path}"
        params = {"ref": ref} if ref else {}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                url,
                headers=self.headers,
                params=params
            )
            response.raise_for_status()
            item = response.json()
        if isinstance(item, list):
            raise RuntimeError("The requested GitHub path is a directory.")
        encoding = item.get("encoding")
        if encoding != "base64":
            raise RuntimeError("The requested GitHub file is not text-readable.")
        try:
            content = base64.b64decode(item["content"]).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError(
                "The requested GitHub file is not valid UTF-8 text."
            ) from exc
        return {
            "path": item["path"],
            "name": item["name"],
            "sha": item["sha"],
            "size": item["size"],
            "content": content,
            "url": item["html_url"]
        }

    async def list_commits(
        self,
        owner: str,
        repo: str,
        branch: str | None,
        limit: int
    ) -> dict:
        url = f"{BASE_URL}/repos/{owner}/{repo}/commits"
        params = {"per_page": limit}
        if branch:
            params["sha"] = branch
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                url,
                headers=self.headers,
                params=params
            )
            response.raise_for_status()
            commits = response.json()
        return {
            "commits": [
                {
                    "sha": commit["sha"],
                    "message": commit["commit"]["message"],
                    "author": (
                        commit["author"]["login"]
                        if commit["author"] is not None
                        else commit["commit"]["author"]["name"]
                    ),
                    "url": commit["html_url"],
                    "committed_at": commit["commit"]["committer"]["date"]
                }
                for commit in commits
            ]
        }

    async def list_issues(
        self,
        owner: str,
        repo: str,
        state: str,
        limit: int
    ) -> dict:
        url = f"{BASE_URL}/repos/{owner}/{repo}/issues"
        params = {
            "state": state,
            "per_page": limit
        }
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                url,
                headers=self.headers,
                params=params
            )
            response.raise_for_status()
            issues = response.json()
        filtered = [
            issue for issue in issues
            if "pull_request" not in issue
        ]
        return {
            "issues": [
                {
                    "number": issue["number"],
                    "title": issue["title"],
                    "url": issue["html_url"],
                    "state": issue["state"],
                    "author": issue["user"]["login"],
                    "created_at": issue["created_at"],
                    "updated_at": issue["updated_at"]
                }
                for issue in filtered
            ]
        }

    async def get_repository_readme(self, owner: str, repo: str) -> str:
        url = f"{BASE_URL}/repos/{owner}/{repo}/readme"

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                url,
                headers=self.headers
            )

            response.raise_for_status()

            data = response.json()

            encoded_content = data["content"]

            decoded_content = base64.b64decode(
                encoded_content
            ).decode("utf-8")

            return decoded_content