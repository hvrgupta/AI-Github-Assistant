import asyncio
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters
from mcp.types import TextResourceContents


async def main():
    project_root = Path(__file__).resolve().parents[1]
    source_path = str(project_root / "src")
    server_env = os.environ.copy()
    server_env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (source_path, server_env.get("PYTHONPATH")))
    )

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "assistant.github_mcp_server"],
        env=server_env
    )

    async with Client(server_params) as client:

        # Discover fixed resources
        resources = await client.list_resources()

        print("Static resources:")
        for resource in resources.resources:
            print(resource.uri)

        # Discover resource templates
        templates = await client.list_resource_templates()

        print("\nResource templates:")
        for template in templates.resource_templates:
            print(template.uri_template)

        # Read a specific repository README
        # result = await client.read_resource(
        #     "github://repos/hvrgupta/ai-github-assistant/readme"
        # )

        # print("\nREADME content:")

        # for content in result.contents:
        #     if isinstance(content, TextResourceContents):
        #         print(content.text)

        prompts = await client.list_prompts()

        print("\nAvailable prompts:")
        for prompt in prompts.prompts:
            print(prompt.name)

        result = await client.get_prompt(
            "review_pull_request",
            {
                "owner": "hvrgupta",
                "repo": "portfolio",
                "pull_number": "1"
            })

        for message in result.messages:
            print(message.content)


if __name__ == "__main__":
    asyncio.run(main())