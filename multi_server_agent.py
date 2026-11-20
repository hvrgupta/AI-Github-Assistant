
import asyncio
import json
import os
from typing import Any

import httpx2

from dotenv import load_dotenv
from pydantic import Field, create_model

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.client.stdio import StdioServerParameters

from langchain_core.tools import StructuredTool
from langchain.agents import create_agent


load_dotenv()


# --------------------------------------------------
# Configuration
# --------------------------------------------------

GITHUB_MCP_URL = "http://127.0.0.1:8000/mcp"

FILESYSTEM_ROOT = r"D:\Dev\learn-agentic-ai"

filesystem_params = StdioServerParameters(
    command="npx",
    args=[
        "-y",
        "@modelcontextprotocol/server-filesystem",
        FILESYSTEM_ROOT
    ],
    env=None
)


# --------------------------------------------------
# JSON Schema -> Pydantic conversion
# --------------------------------------------------

def json_schema_to_python_type(schema: dict, model_name: str):
    """
    Convert a JSON Schema definition into a Python type.

    Supports:
    - string
    - integer
    - number
    - boolean
    - array
    - object
    - nested objects
    - nested arrays
    """

    schema_type = schema.get("type")

    if schema_type == "string":
        return str

    if schema_type == "integer":
        return int

    if schema_type == "number":
        return float

    if schema_type == "boolean":
        return bool

    if schema_type == "array":
        item_schema = schema.get("items", {})

        item_type = json_schema_to_python_type(
            item_schema,
            f"{model_name}Item"
        )

        return list[item_type]

    if schema_type == "object":
        properties = schema.get("properties", {})

        if not properties:
            return dict[str, Any]

        return create_pydantic_model(
            model_name,
            schema
        )

    return Any


def create_pydantic_model(model_name: str, schema: dict):
    """
    Dynamically create a Pydantic model from JSON Schema.
    """

    fields = {}

    properties = schema.get("properties", {})
    required_fields = schema.get("required", [])

    for field_name, field_schema in properties.items():

        python_type = json_schema_to_python_type(
            field_schema,
            f"{model_name}_{field_name}"
        )

        description = field_schema.get("description", "")

        if field_name in required_fields:

            field_definition = Field(
                ...,
                description=description
            )

        else:

            default_value = field_schema.get("default", None)

            field_definition = Field(
                default=default_value,
                description=description
            )

        fields[field_name] = (
            python_type,
            field_definition
        )

    return create_model(
        model_name,
        **fields
    )


def create_args_schema(mcp_tool):
    """
    Convert the MCP tool's input JSON Schema into a Pydantic model.
    """

    return create_pydantic_model(
        f"{mcp_tool.name}Arguments",
        mcp_tool.input_schema
    )


# --------------------------------------------------
# MCP -> LangChain tool adapter
# --------------------------------------------------

def create_langchain_tool(mcp_client, mcp_tool):

    async def dynamic_tool(**kwargs):

        print(
            f"\nCalling MCP tool: {mcp_tool.name}"
        )

        result = await mcp_client.call_tool(
            mcp_tool.name,
            kwargs
        )

        if result.is_error:

            error_text = "\n".join(
                getattr(block, "text", "")
                for block in result.content
            ).strip()

            raise RuntimeError(
                f"MCP tool '{mcp_tool.name}' failed: "
                f"{error_text or 'No error details returned'}"
            )

        if result.structured_content is not None:

            return json.dumps(
                result.structured_content,
                ensure_ascii=False
            )

        return "\n".join(
            getattr(block, "text", "")
            for block in result.content
        )

    return StructuredTool.from_function(
        coroutine=dynamic_tool,
        name=mcp_tool.name,
        description=mcp_tool.description or "",
        args_schema=create_args_schema(mcp_tool)
    )


# --------------------------------------------------
# Main application
# --------------------------------------------------

async def main():

    token = os.getenv("MCP_ACCESS_TOKEN")

    if not token:
        raise RuntimeError(
            "MCP_ACCESS_TOKEN is missing from .env"
        )

    async with httpx2.AsyncClient(
        headers={
            "Authorization": f"Bearer {token}"
        },
        timeout=httpx2.Timeout(
            30,
            read=300
        )
    ) as http_client:

        github_transport = streamable_http_client(
            url=GITHUB_MCP_URL,
            http_client=http_client
        )

        async with Client(github_transport) as github_client:

            async with Client(filesystem_params) as filesystem_client:

                print("Connected to both MCP servers!")

                # ----------------------------------
                # Discover GitHub tools
                # ----------------------------------

                github_tools = await github_client.list_tools()

                print("\nGitHub MCP tools:")

                for mcp_tool in github_tools.tools:
                    print(f"- {mcp_tool.name}")

                # ----------------------------------
                # Discover filesystem tools
                # ----------------------------------

                filesystem_tools = await filesystem_client.list_tools()

                print("\nFilesystem MCP tools:")

                for mcp_tool in filesystem_tools.tools:
                    print(f"- {mcp_tool.name}")

                # ----------------------------------
                # Convert MCP tools into LangChain
                # ----------------------------------

                langchain_tools = []

                for mcp_tool in github_tools.tools:

                    langchain_tools.append(
                        create_langchain_tool(
                            github_client,
                            mcp_tool
                        )
                    )

                for mcp_tool in filesystem_tools.tools:

                    langchain_tools.append(
                        create_langchain_tool(
                            filesystem_client,
                            mcp_tool
                        )
                    )

                print(
                    f"\nTotal LangChain tools: {len(langchain_tools)}"
                )

                for langchain_tool in langchain_tools:
                    print(f"- {langchain_tool.name}")

                # ----------------------------------
                # Create LangChain agent
                # ----------------------------------

                agent = create_agent(
                    model="openai:gpt-4.1-mini",
                    tools=langchain_tools,
                    system_prompt="""
You are a GitHub Developer Assistant.

You can use GitHub tools to:
- Search repositories.
- Inspect pull requests.
- Create GitHub issues.

You can use filesystem tools to:
- Read files.
- Write files.
- Search files.
- List directories.
- Manage project files.

Choose the appropriate tools based on the user's request.

For filesystem operations, use the exact paths supplied by
the user or discovered through filesystem tools.

Never claim an operation succeeded unless the tool confirms it.

Never invent file contents or tool results.
"""
                )

                # ----------------------------------
                # Test agent
                # ----------------------------------

                response = await agent.ainvoke(
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": (
                                    "List the files in my "
                                    "ai-github-assistant project directory. "
                                    "The directory is "
                                    r"D:\Dev\learn-agentic-ai\ai-github-assistant"
                                )
                            }
                        ]
                    }
                )

                print("\nAgent response:")

                print(
                    response["messages"][-1].content
                )


# --------------------------------------------------
# Entry point
# --------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
