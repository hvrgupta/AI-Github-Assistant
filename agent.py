import asyncio
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from mcp import Client, StdioServerParameters
from langchain.agents import create_agent
from langchain.tools import tool
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
import uuid

load_dotenv()

github_token = os.getenv("GITHUB_TOKEN")

def format_result(result) -> str:

    if result.is_error:
        error_text = "\n".join(
            getattr(block, "text", "")
            for block in result.content
        )
        return f"GitHub tool error: {error_text}"

    if result.structured_content is not None:
        return json.dumps(
            result.structured_content,
            ensure_ascii=False
        )

    return "\n".join(
        getattr(block, "text", "")
        for block in result.content
    )

async def main():

    github_token = os.getenv("GITHUB_TOKEN")

    if not github_token:
        raise RuntimeError("GITHUB_TOKEN is missing from .env")

    # Configure the MCP server subprocess.
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("server.py"))],
        env={"GITHUB_TOKEN": github_token}
    )

    # Connect to the MCP server.
    async with Client(server_params) as mcp_client:

        # Discover available MCP tools.
        available_tools = await mcp_client.list_tools()

        print("Discovered MCP tools:")
        for item in available_tools.tools:
            print("-", item.name)

        # Convert MCP operations into LangChain tools.

        @tool
        async def search_repositories(
            query: str,
            limit: int = 5
        ) -> str:
            """Search GitHub repositories using a query."""

            result = await mcp_client.call_tool(
                "search_repo",
                {"query": query, "limit": limit}
            )

            # print(f"result: {result}")

            return format_result(result)

        @tool
        async def get_pull_request(
            owner: str,
            repo: str,
            pull_number: int
        ) -> str:
            """Get details of a GitHub pull request."""

            result = await mcp_client.call_tool(
                "get_pull_request",
                {
                    "owner": owner,
                    "repo": repo,
                    "pull_number": pull_number
                }
            )

            return format_result(result)

        @tool
        async def create_issue(
            owner: str,
            repo: str,
            title: str,
            body: str
        ) -> str:
            """Create an issue in a GitHub repository."""

            result = await mcp_client.call_tool(
                "create_issue",
                {
                    "owner": owner,
                    "repo": repo,
                    "title": title,
                    "body": body
                }
            )

            return format_result(result)

        tools = [
            search_repositories,
            get_pull_request,
            create_issue
        ]

        # Create the LangChain agent.
        agent = create_agent(
            model="openai:gpt-4.1-mini",
            tools=tools,
            middleware=[
                HumanInTheLoopMiddleware(
                    interrupt_on={
                        "search_repositories": False,
                        "get_pull_request": False,
                        "create_issue": {
                            "allowed_decisions": [
                                "approve",
                                "edit",
                                "reject"
                            ]
                        }
                    }
                )
            ],
            checkpointer=InMemorySaver(),
            system_prompt=(
                "You are a GitHub developer assistant. "
                "Use GitHub tools to answer questions. "
                "Never invent GitHub data. "
                "Before creating an issue, prepare a clear title "
                "and description for human review."
            )
        )

        # Ask the agent a natural-language question.
        search_config = {
            "configurable": {
                "thread_id": str(uuid.uuid4())
            }
        }
        response = await agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Find the top 5 Python repositories "
                            "related to LangGraph. Summarize their "
                            "names, stars, and descriptions"
                        )
                    }
                ]
            },
            config=search_config
        )

        print("\nAgent response:\n")
        print(response["messages"][-1].content)

        config = {
            "configurable": {
                    "thread_id": str(uuid.uuid4())
                }
        }

        response = await agent.ainvoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Create an issue in my portfolio repository "
                        "To update the UI and add recent projects and employment details"
                        "my username is hvrgupta"
                    )
                }
            ]
        },
        config=config,
        version="v2"
        )

        # Check whether human approval is required.
        if response.interrupts:

            for interrupt in response.interrupts:
                details = interrupt.value

                for action in details["action_requests"]:
                    print("\nTool:", action["name"])
                    print("Arguments:", action["args"])
                    print("Description:", action["description"])

            choice = input(
                "\nChoose: (a)pprove, (e)dit, or (r)eject: "
            ).strip().lower()

            if choice == "a":
                human_decision = {"type": "approve"}

            elif choice == "r":
                human_decision = {
                    "type": "reject",
                    "message": "Human rejected this action."
                }

            elif choice == "e":
                new_title = input("Enter corrected title: ")
                new_body = input("Enter corrected description: ")

                action = details["action_requests"][0]

                edited_args = dict(action["args"])
                edited_args["title"] = new_title
                edited_args["body"] = new_body

                human_decision = {
                    "type": "edit",
                    "edited_action": {
                        "name": action["name"],
                        "args": edited_args
                    }
                }

            else:
                raise ValueError("Invalid choice. Run the agent again.")

            response = await agent.ainvoke(
                Command(
                    resume={
                        "decisions": [human_decision]
                    }
                ),
                config=config,
                version="v2"
            )

        print("\nAgent response:")
        print(response.value["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
