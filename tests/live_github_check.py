import asyncio
import os

import httpx2
from dotenv import load_dotenv
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def main() -> None:
    load_dotenv()
    token = os.getenv("MCP_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("MCP_ACCESS_TOKEN is missing")

    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ) as http_client:
        async with Client(
            streamable_http_client(
                url=os.getenv(
                    "GITHUB_MCP_URL",
                    "http://127.0.0.1:8000/mcp",
                ),
                http_client=http_client,
            )
        ) as client:
            result = await client.list_tools()
            print([tool.name for tool in result.tools])
            result = await client.call_tool(
                "search_repo",
                {"query": "LangGraph", "limit": 1},
            )
            print("IS_ERROR", result.is_error)
            print(str(result.structured_content)[:1000])


if __name__ == "__main__":
    asyncio.run(main())
