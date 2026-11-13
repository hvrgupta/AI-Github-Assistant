
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt, Command
from mcp import Client, StdioServerParameters

load_dotenv()

class IssueDraft(BaseModel):
    title: str = Field(description="Short, clear GitHub issue title")
    body: str = Field(description="Detailed issue description")


# The data shared between graph nodes.
class WorkflowState(TypedDict, total=False):
    request: str
    owner: str
    repo: str
    title: str
    body: str
    approved: bool
    result: str
    status: str


# The model with structured output.
llm = ChatOpenAI(
    model="gpt-4.1-mini",
    temperature=0
)

issue_generator = llm.with_structured_output(IssueDraft)

async def main():

    github_token = os.getenv("GITHUB_TOKEN")

    if not github_token:
        raise RuntimeError("GITHUB_TOKEN is missing from .env")

    # Start the existing GitHub MCP server as a subprocess.
    server_env = os.environ.copy()
    server_env["GITHUB_TOKEN"] = github_token

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("server.py"))],
        env=server_env
    )

    async with Client(server_params) as mcp_client:

        # NODE 1: Prepare the issue.
        async def draft_issue(state: WorkflowState):

            draft = await issue_generator.ainvoke(
                f"""
                Prepare a professional GitHub issue based on
                this user request:

                {state['request']}

                Write a clear title and a useful body.
                Do not invent facts that the user did not provide.
                """
            )

            print("\nIssue drafted successfully.")

            return {
                "title": draft.title,
                "body": draft.body
            }

        # NODE 2: Ask the human for approval.
        def approval_node(state: WorkflowState):

            decision = interrupt({
                "message": "Please review this GitHub issue.",
                "repository": (
                    f"{state['owner']}/{state['repo']}"
                ),
                "title": state["title"],
                "body": state["body"],
                "actions": ["approve", "reject"]
            })

            return {
                "approved": decision is True
            }

        # NODE 3: Create the issue using MCP.
        async def create_issue_node(state: WorkflowState):

            result = await mcp_client.call_tool(
                "create_issue",
                {
                    "owner": state["owner"],
                    "repo": state["repo"],
                    "title": state["title"],
                    "body": state["body"]
                }
            )

            if result.is_error:
                error_text = "\n".join(
                    getattr(block, "text", "")
                    for block in result.content
                )
                raise RuntimeError(
                    f"GitHub issue creation failed: {error_text}"
                )

            if result.structured_content is not None:
                output = json.dumps(result.structured_content)
            else:
                output = "\n".join(
                    getattr(block, "text", "")
                    for block in result.content
                )

            return {
                "result": output,
                "status": "created"
            }

        # NODE 4: Handle rejection.
        def rejected_node(state: WorkflowState):

            return {
                "status": "rejected",
                "result": "Issue was not created."
            }

        # Decide which node runs after approval.
        def route_after_approval(state: WorkflowState):

            if state["approved"]:
                return "create_issue"

            return "rejected"

        # 4. Build the LangGraph workflow.
        builder = StateGraph(WorkflowState)

        builder.add_node("draft_issue", draft_issue)
        builder.add_node("approval", approval_node)
        builder.add_node("create_issue", create_issue_node)
        builder.add_node("rejected", rejected_node)

        builder.add_edge(START, "draft_issue")
        builder.add_edge("draft_issue", "approval")

        builder.add_conditional_edges(
            "approval",
            route_after_approval,
            {
                "create_issue": "create_issue",
                "rejected": "rejected"
            }
        )

        builder.add_edge("create_issue", END)
        builder.add_edge("rejected", END)

        # 5. Add checkpointing so the graph can pause and resume.
        checkpointer = InMemorySaver()

        graph = builder.compile(
            checkpointer=checkpointer
        )

        # Every workflow execution gets its own thread ID.
        config = {
            "configurable": {
                "thread_id": str(uuid.uuid4())
            }
        }

        # 6. Start the workflow.
        initial_state = {
            "request": (
                "The login API returns a 500 error when "
                "an incorrect password is entered. "
                "Expected behavior is a 401 response."
            ),
            "owner": input("GitHub owner: ").strip(),
            "repo": input("GitHub repository: ").strip()
        }

        paused_result = await graph.ainvoke(
            initial_state,
            config
        )

        # 7. Check whether LangGraph paused for approval.
        interrupts = paused_result.get("__interrupt__", ())

        if not interrupts:
            print("Workflow completed:", paused_result)
            return

        approval_details = interrupts[0].value

        print("\n========== ISSUE REVIEW ==========")
        print("Repository:", approval_details["repository"])
        print("Title:", approval_details["title"])
        print("\nDescription:")
        print(approval_details["body"])
        print("\n==================================")

        # 8. Get the human's decision.
        answer = input(
            "\nApprove issue creation? (yes/no): "
        ).strip().lower()

        approved = answer in ("yes", "y")

        # 9. Resume the same graph execution.
        final_result = await graph.ainvoke(
            Command(resume=approved),
            config
        )

        print("\n========== FINAL RESULT ==========")
        print("Status:", final_result.get("status"))
        print("Result:", final_result.get("result"))


if __name__ == "__main__":
    asyncio.run(main())
