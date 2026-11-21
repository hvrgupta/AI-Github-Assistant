import asyncio
import os
from pathlib import Path

from mcp import Client, StdioServerParameters


async def main() -> None:
    root = Path(
        os.environ.get(
            "COMPANY_KNOWLEDGE_ROOT",
            str(Path(__file__).resolve().parents[1] / "company-knowledge"),
        )
    ).resolve()
    params = StdioServerParameters(
        command="npx",
        args=[
            "-y",
            "@modelcontextprotocol/server-filesystem",
            "--read-only",
            str(root),
        ],
        env=None,
    )
    async with Client(params) as client:
        listed = await client.list_tools()
        print([tool.name for tool in listed.tools])
        path = root / "architecture" / "fictional-auth-platform.md"
        result = await client.call_tool(
            "read_text_file",
            {"path": str(path)},
        )
        print("IS_ERROR", result.is_error)
        print(
            "".join(
                getattr(block, "text", "")
                for block in result.content
            )[:500]
        )
        outside = await client.call_tool(
            "read_text_file",
            {"path": str(Path(root.anchor) / "Windows" / "win.ini")},
        )
        print("OUTSIDE_IS_ERROR", outside.is_error)
        print(
            "".join(
                getattr(block, "text", "")
                for block in outside.content
            )[:300]
        )


if __name__ == "__main__":
    asyncio.run(main())
