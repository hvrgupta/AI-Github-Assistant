import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters
from mcp.types import TextResourceContents


async def main():

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("server.py"))]
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