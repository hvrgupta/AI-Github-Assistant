import asyncio
import json
import os
import uuid

import httpx2
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.tools import tool
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from mcp import Client
from mcp.client.session import ClientRequestContext
from mcp.client.streamable_http import streamable_http_client
from mcp.types import (
    CallToolResult,
    ElicitRequestFormParams,
    ElicitRequestParams,
    ElicitResult,
)

MCP_SERVER_URL = "http://127.0.0.1:8000/mcp"
MODEL_NAME = "openai:gpt-4.1-mini"
SYSTEM_PROMPT = (
    "You are a GitHub developer assistant. "
    "Use GitHub tools to answer questions. "
    "Never invent GitHub data. "
    "Before creating an issue, prepare a clear title "
    "and description for human review."
)
SEARCH_REQUEST = (
    "Find the top 5 Python repositories related to LangGraph. "
    "Summarize their names, stars, and descriptions and give their first PR as well."
)
CREATE_ISSUE_REQUEST = (
    "Create an issue in my portfolio repository to update the UI and add "
    "recent projects and employment details. My username is hvrgupta."
)


async def handle_elicitation(
    _context: ClientRequestContext,
    params: ElicitRequestParams
) -> ElicitResult:
    if not isinstance(params, ElicitRequestFormParams):
        print("Unsupported MCP elicitation mode; declining the request.")
        return ElicitResult(action="decline")

    confirm_field = (
        params.requested_schema.get("properties", {}).get("confirm", {})
    )
    if confirm_field.get("type") != "boolean":
        print("Unsupported MCP elicitation schema; declining the request.")
        return ElicitResult(action="decline")

    print(f"\nMCP server asks: {params.message}")
    answer = await asyncio.to_thread(input, "Confirm? [y/N]: ")
    confirmed = answer.strip().lower() in {"y", "yes"}
    return ElicitResult(
        action="accept",
        content={"confirm": confirmed}
    )


def format_result(result: CallToolResult) -> str:
    if result.is_error:
        error_text = "\n".join(
            getattr(block, "text", "")
            for block in result.content
        ).strip()

        if not error_text:
            error_text = "MCP server returned an error without details."
        raise RuntimeError(f"MCP tool call failed: {error_text}")

    if result.structured_content is not None:
        return json.dumps(
            result.structured_content,
            ensure_ascii=False
        )

    return "\n".join(
        getattr(block, "text", "")
        for block in result.content
    )


def create_tools(
    mcp_client: Client,
    issue_creation_state: dict[str, bool]
) -> list[BaseTool]:
    @tool
    async def search_repositories(query: str, limit: int = 5) -> str:
        """Search GitHub repositories using a query."""
        result = await mcp_client.call_tool(
            "search_repo",
            {"query": query, "limit": limit}
        )
        return format_result(result)

    @tool
    async def get_pull_request(owner: str, repo: str, pull_number: int) -> str:
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
    async def create_issue(owner: str, repo: str, title: str, body: str) -> str:
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

        if isinstance(result.structured_content, dict):
            created = result.structured_content.get("created")
            if isinstance(created, bool):
                issue_creation_state["created"] = created

        return format_result(result)

    return [search_repositories, get_pull_request, create_issue]


def create_github_agent(tools: list[BaseTool]):
    return create_agent(
        model=MODEL_NAME,
        tools=tools,
        middleware=[
            HumanInTheLoopMiddleware(
                interrupt_on={
                    "search_repositories": False,
                    "get_pull_request": False,
                    "create_issue": {
                        "allowed_decisions": ["approve", "edit", "reject"]
                    }
                }
            )
        ],
        checkpointer=InMemorySaver(),
        system_prompt=SYSTEM_PROMPT
    )


def new_thread_config() -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


async def run_repository_search(agent) -> None:
    response = await agent.ainvoke(
        {"messages": [{"role": "user", "content": SEARCH_REQUEST}]},
        config=new_thread_config()
    )
    print("\nAgent response:\n")
    print(response["messages"][-1].content)


async def run_issue_creation(
    agent,
    issue_creation_state: dict[str, bool]
) -> None:
    config = new_thread_config()
    response = await agent.ainvoke(
        {"messages": [{"role": "user", "content": CREATE_ISSUE_REQUEST}]},
        config=config,
        version="v2"
    )

    approval_rejected = False
    if response.interrupts:
        details = response.interrupts[0].value
        for action in details["action_requests"]:
            print("\nTool:", action["name"])
            print("Arguments:", action["args"])
            print("Description:", action["description"])

        choice = (
            await asyncio.to_thread(
                input,
                "\nChoose: (a)pprove, (e)dit, or (r)eject: "
            )
        ).strip().lower()

        if choice in {"a", "approve"}:
            human_decision = {"type": "approve"}
        elif choice in {"r", "reject"}:
            approval_rejected = True
            human_decision = {
                "type": "reject",
                "message": "Human rejected this action."
            }
        elif choice in {"e", "edit"}:
            new_title = await asyncio.to_thread(input, "Enter corrected title: ")
            new_body = await asyncio.to_thread(
                input,
                "Enter corrected description: "
            )
            action = details["action_requests"][0]
            edited_args = dict(action["args"])
            edited_args.update({"title": new_title, "body": new_body})
            human_decision = {
                "type": "edit",
                "edited_action": {
                    "name": action["name"],
                    "args": edited_args
                }
            }
        else:
            raise ValueError(
                "Invalid choice. Enter a, approve, e, edit, r, or reject."
            )

        response = await agent.ainvoke(
            Command(resume={"decisions": [human_decision]}),
            config=config,
            version="v2"
        )

    print("\nAgent response:")
    if approval_rejected:
        print(
            "Issue creation was rejected during agent approval. "
            "No issue was created."
        )
    elif issue_creation_state.get("created") is False:
        print(
            "Issue creation was declined during MCP server confirmation. "
            "No issue was created."
        )
    else:
        print(response.value["messages"][-1].content)


async def main() -> None:
    load_dotenv()
    token = os.getenv("MCP_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("MCP_ACCESS_TOKEN is missing")

    http_client = httpx2.AsyncClient(
        headers={
            "Authorization": f"Bearer {token}"
        },
        timeout=httpx2.Timeout(30, read=300)
    )

    async with http_client:
        async with Client(
            streamable_http_client(
                url=MCP_SERVER_URL,
                http_client=http_client
            ),
            elicitation_callback=handle_elicitation
        ) as mcp_client:

            # Discover available MCP tools.
            available_tools = await mcp_client.list_tools()

            print("Discovered MCP tools:")
            for item in available_tools.tools:
                print("-", item.name)

            issue_creation_state: dict[str, bool] = {}
            agent = create_github_agent(
                create_tools(mcp_client, issue_creation_state)
            )
            await run_repository_search(agent)
            await run_issue_creation(agent, issue_creation_state)


if __name__ == "__main__":
    asyncio.run(main())
