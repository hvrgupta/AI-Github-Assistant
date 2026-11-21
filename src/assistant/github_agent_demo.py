import asyncio
import argparse
import json
import os

import httpx2
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from assistant.agent.prompts import SYSTEM_PROMPT


MCP_SERVER_URL = os.getenv("GITHUB_MCP_URL", "http://127.0.0.1:8000/mcp")
MODEL_NAME = "openai:gpt-4.1-mini"


def format_result(result: CallToolResult) -> str:
    if result.is_error:
        error_text = "\n".join(
            getattr(block, "text", "")
            for block in result.content
        ).strip()
        raise RuntimeError(
            error_text or "MCP server returned an error without details."
        )
    if result.structured_content is not None:
        return json.dumps(result.structured_content, ensure_ascii=False)
    return "\n".join(
        getattr(block, "text", "")
        for block in result.content
    )


def create_tools(mcp_client: Client) -> list[BaseTool]:
    @tool
    async def search_repositories(query: str, limit: int = 5) -> str:
        """Search GitHub repositories."""
        result = await mcp_client.call_tool(
            "search_repo",
            {"query": query, "limit": limit},
        )
        return format_result(result)

    @tool
    async def get_pull_request(
        owner: str,
        repo: str,
        pull_number: int,
    ) -> str:
        """Get details for a GitHub pull request."""
        result = await mcp_client.call_tool(
            "get_pull_request",
            {"owner": owner, "repo": repo, "pull_number": pull_number},
        )
        return format_result(result)

    @tool
    async def get_repository(owner: str, repo: str) -> str:
        """Get repository metadata."""
        result = await mcp_client.call_tool(
            "get_repository",
            {"owner": owner, "repo": repo},
        )
        return format_result(result)

    @tool
    async def list_commits(
        owner: str,
        repo: str,
        branch: str | None = None,
        limit: int = 10,
    ) -> str:
        """List recent repository commits."""
        result = await mcp_client.call_tool(
            "list_commits",
            {"owner": owner, "repo": repo, "branch": branch, "limit": limit},
        )
        return format_result(result)

    @tool
    async def list_issues(
        owner: str,
        repo: str,
        state: str = "open",
        limit: int = 10,
    ) -> str:
        """List repository issues."""
        result = await mcp_client.call_tool(
            "list_issues",
            {"owner": owner, "repo": repo, "state": state, "limit": limit},
        )
        return format_result(result)

    return [
        search_repositories,
        get_pull_request,
        get_repository,
        list_commits,
        list_issues,
    ]


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Run the read-only GitHub agent."
    )
    parser.add_argument(
        "--message",
        required=True,
        help="User request to send to the agent.",
    )
    args = parser.parse_args()
    token = os.getenv("MCP_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("MCP_ACCESS_TOKEN is missing")

    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(30, read=300),
    ) as http_client:
        async with Client(
            streamable_http_client(
                url=MCP_SERVER_URL,
                http_client=http_client,
            )
        ) as mcp_client:
            tools = create_tools(mcp_client)
            agent = create_agent(
                model=MODEL_NAME,
                tools=tools,
                checkpointer=InMemorySaver(),
                system_prompt=SYSTEM_PROMPT,
            )
            response = await agent.ainvoke(
                {
                    "messages": [{
                        "role": "user",
                        "content": args.message,
                    }]
                },
                config={"configurable": {"thread_id": str(os.getpid())}},
            )
            print(response["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
