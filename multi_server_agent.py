
import asyncio
import os
from contextlib import AsyncExitStack
from typing import Any, Callable, Awaitable

import anyio
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

PROJECT_DIRECTORY = (
    r"D:\Dev\learn-agentic-ai\ai-github-assistant"
)

REPORT_DIRECTORY = PROJECT_DIRECTORY + r"\reports"

REPORT_PATH = REPORT_DIRECTORY + r"\langgraph-repository-report.md"

MCP_REQUEST_TIMEOUT_SECONDS = 45
MCP_MAX_ATTEMPTS = 3
MCP_RETRY_BASE_DELAY_SECONDS = 0.5

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

def json_schema_to_python_type(
    schema: dict,
    model_name: str
):
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


def create_pydantic_model(
    model_name: str,
    schema: dict
):
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
    return create_pydantic_model(
        f"{mcp_tool.name}Arguments",
        mcp_tool.input_schema
    )


# --------------------------------------------------
# MCP errors
# --------------------------------------------------

class MCPRequestError(RuntimeError):
    pass


class MCPServerDisconnectedError(MCPRequestError):
    pass


def _contains_exception(
    error: Exception,
    exception_types: tuple[type[BaseException], ...]
) -> bool:

    nested_exceptions = getattr(error, "exceptions", None)

    if nested_exceptions is not None:
        return any(
            _contains_exception(nested, exception_types)
            for nested in nested_exceptions
            if isinstance(nested, Exception)
        )

    return isinstance(error, exception_types)


def _is_disconnection(error: Exception) -> bool:

    disconnected_errors = (
        anyio.BrokenResourceError,
        anyio.ClosedResourceError,
        anyio.EndOfStream,
        httpx2.TimeoutException,
        httpx2.TransportError,
        TimeoutError
    )

    return _contains_exception(
        error,
        disconnected_errors
    )


def _error_text(result) -> str:
    return "\n".join(
        str(getattr(block, "text", "") or "")
        for block in (result.content or [])
    ).strip()


# --------------------------------------------------
# Centralized MCP request handling
# --------------------------------------------------

async def run_mcp_request(
    server_name: str,
    operation: str,
    request: Callable[[], Awaitable[Any]],
    *,
    retry_safe: bool,
    timeout_seconds: float = MCP_REQUEST_TIMEOUT_SECONDS
):

    max_attempts = (
        MCP_MAX_ATTEMPTS if retry_safe else 1
    )

    for attempt in range(1, max_attempts + 1):

        try:
            return await asyncio.wait_for(
                request(),
                timeout=timeout_seconds
            )

        except Exception as exc:

            if _is_disconnection(exc):
                raise MCPServerDisconnectedError(
                    f"{server_name} MCP connection became unusable during "
                    f"'{operation}': {exc}"
                ) from exc

            if not retry_safe:
                raise MCPRequestError(
                    f"{server_name} MCP operation '{operation}' failed: "
                    f"{exc}"
                ) from exc

            if attempt == max_attempts:
                raise MCPRequestError(
                    f"{server_name} MCP operation '{operation}' failed "
                    f"after {max_attempts} attempts: {exc}"
                ) from exc

            delay = MCP_RETRY_BASE_DELAY_SECONDS * (
                2 ** (attempt - 1)
            )

            print(
                f"{server_name}: transient error during '{operation}'. "
                f"Retrying in {delay:g}s "
                f"({attempt + 1}/{max_attempts})."
            )

            await asyncio.sleep(delay)


# --------------------------------------------------
# MCP Connection Manager
# --------------------------------------------------

class MCPConnectionManager:
    """Keep MCP transport contexts owned by one task for their full lifetime."""

    def __init__(
        self,
        server_name: str,
        client_factory: Callable
    ):

        self.server_name = server_name
        self.client_factory = client_factory

        self.client = None
        self.tools = []

        self._stack = None
        self._requests = asyncio.Queue()
        self._worker_task = None
        self._ready = None
        self._start_lock = asyncio.Lock()
        self._closed = False

    async def connect(self):
        async with self._start_lock:
            if self._closed:
                raise MCPRequestError(
                    f"{self.server_name} MCP connection manager is closed."
                )

            if (
                self._worker_task is not None
                and self._worker_task.done()
            ):
                self._worker_task = None
                self._ready = None

            if self._worker_task is None:
                loop = asyncio.get_running_loop()
                self._ready = loop.create_future()

                self._worker_task = asyncio.create_task(
                    self._run_connection_worker()
                )

            ready = self._ready

        await asyncio.shield(ready)

    async def _open_connection(self):
        stack = AsyncExitStack()

        try:
            client = await stack.enter_async_context(
                self.client_factory()
            )

            result = await run_mcp_request(
                self.server_name,
                "discover tools",
                client.list_tools,
                retry_safe=True
            )

            self._stack = stack
            self.client = client
            self.tools = result.tools

            print(
                f"{self.server_name} MCP connected. "
                f"Discovered {len(self.tools)} tools."
            )

        except Exception:
            await stack.aclose()
            raise

    async def _close_connection(self):
        stack = self._stack
        self.client = None
        self._stack = None
        self.tools = []

        if stack is not None:
            await stack.aclose()

    async def _reconnect(self):
        print(
            f"{self.server_name} MCP disconnected. "
            "Attempting reconnection..."
        )
        await self._close_connection()
        await self._open_connection()
        print(f"{self.server_name} MCP reconnected successfully.")

    async def _call_tool(self, tool_name, arguments, retry_safe):
        client = self.client
        if client is None:
            await self._open_connection()
            client = self.client

        async def request():
            return await client.call_tool(
                tool_name,
                arguments,
                read_timeout_seconds=MCP_REQUEST_TIMEOUT_SECONDS
            )

        try:
            return await run_mcp_request(
                self.server_name,
                f"tool '{tool_name}'",
                request,
                retry_safe=retry_safe,
                timeout_seconds=MCP_REQUEST_TIMEOUT_SECONDS
            )
        except MCPServerDisconnectedError as exc:
            try:
                await self._reconnect()
            except Exception as reconnect_error:
                raise MCPRequestError(
                    f"{self.server_name} MCP could not reconnect after "
                    f"'{tool_name}' failed. Check that the server is "
                    f"running and retry: {reconnect_error}"
                ) from reconnect_error

            if not retry_safe:
                raise MCPRequestError(
                    f"{self.server_name} disconnected during "
                    f"'{tool_name}'. The outcome may be unknown. "
                    "The server was reconnected, but the operation was "
                    "not replayed."
                ) from exc

            print(
                f"Retrying safe operation '{tool_name}' "
                "on the reconnected server."
            )

            client = self.client

            async def retry_request():
                return await client.call_tool(
                    tool_name,
                    arguments,
                    read_timeout_seconds=MCP_REQUEST_TIMEOUT_SECONDS
                )

            try:
                return await run_mcp_request(
                    self.server_name,
                    f"tool '{tool_name}' after reconnection",
                    retry_request,
                    retry_safe=False,
                    timeout_seconds=MCP_REQUEST_TIMEOUT_SECONDS
                )
            except MCPServerDisconnectedError as retry_error:
                try:
                    await self._reconnect()
                except Exception as reconnect_error:
                    raise MCPRequestError(
                        f"{self.server_name} MCP disconnected again during "
                        f"'{tool_name}', and reconnection failed: "
                        f"{reconnect_error}"
                    ) from reconnect_error
                raise MCPRequestError(
                    f"{self.server_name} MCP reconnected, but the safe "
                    f"retry of '{tool_name}' also failed: {retry_error}"
                ) from retry_error

    async def _run_connection_worker(self):
        try:
            try:
                await self._open_connection()
            except Exception as exc:
                if not self._ready.done():
                    self._ready.set_exception(
                        MCPRequestError(
                            f"{self.server_name} MCP connection failed: {exc}"
                        )
                    )
                return

            if not self._ready.done():
                self._ready.set_result(None)

            while True:
                request = await self._requests.get()
                if request is None:
                    break

                tool_name, arguments, retry_safe, result_future = request
                if result_future.cancelled():
                    continue

                try:
                    result = await self._call_tool(
                        tool_name,
                        arguments,
                        retry_safe
                    )
                except Exception as exc:
                    if not result_future.done():
                        result_future.set_exception(exc)
                else:
                    if not result_future.done():
                        result_future.set_result(result)
        finally:
            await self._close_connection()

    async def call_tool(self, tool_name, arguments, *, retry_safe):
        await self.connect()
        result_future = asyncio.get_running_loop().create_future()
        await self._requests.put(
            (tool_name, arguments, retry_safe, result_future)
        )
        return await result_future

    async def close(self):
        async with self._start_lock:
            if self._closed:
                worker_task = self._worker_task
            else:
                self._closed = True
                worker_task = self._worker_task
                if worker_task is not None and not worker_task.done():
                    await self._requests.put(None)

        if worker_task is not None:
            await worker_task


# --------------------------------------------------
# MCP Tool Calling with Reconnection
# --------------------------------------------------

async def call_mcp_tool(
    connection: MCPConnectionManager,
    tool_name: str,
    arguments: dict,
    *,
    retry_safe: bool
):
    result = await connection.call_tool(
        tool_name,
        arguments,
        retry_safe=retry_safe
    )

    if result.is_error:

        raise MCPRequestError(
            f"{connection.server_name} MCP tool '{tool_name}' failed: "
            f"{_error_text(result) or 'No error details returned'}"
        )

    return result


# --------------------------------------------------
# MCP -> LangChain Adapter
# --------------------------------------------------

def _tool_is_read_only(mcp_tool) -> bool:

    annotations = getattr(
        mcp_tool,
        "annotations",
        None
    )

    return (
        getattr(
            annotations,
            "read_only_hint",
            False
        ) is True
    )


def create_langchain_tool(
    connection: MCPConnectionManager,
    mcp_tool
):

    async def dynamic_tool(**kwargs):

        clean_kwargs = {
            key: value
            for key, value in kwargs.items()
            if value is not None
        }

        result = await call_mcp_tool(
            connection,
            mcp_tool.name,
            clean_kwargs,
            retry_safe=_tool_is_read_only(mcp_tool)
        )

        structured_content = getattr(result, "structured_content", None)

        return (
            str(structured_content)
            if structured_content is not None
            else _error_text(result) or str(result.content)
        )

    return StructuredTool.from_function(
        coroutine=dynamic_tool,
        name=mcp_tool.name,
        description=mcp_tool.description or "",
        args_schema=create_args_schema(mcp_tool)
    )


# --------------------------------------------------
# Main Application
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
        timeout=MCP_REQUEST_TIMEOUT_SECONDS
    ) as http_client:

        github_connection = MCPConnectionManager(
            "GitHub",
            lambda: Client(
                streamable_http_client(
                    url=GITHUB_MCP_URL,
                    http_client=http_client
                )
            )
        )

        filesystem_connection = MCPConnectionManager(
            "Filesystem",
            lambda: Client(filesystem_params)
        )

        try:

            # ----------------------------------
            # Connect to both MCP servers
            # ----------------------------------

            await github_connection.connect()
            await filesystem_connection.connect()

            github_tools = github_connection.tools
            filesystem_tools = filesystem_connection.tools

            print("\nConnected to both MCP servers!")

            print(
                "GitHub MCP tools:",
                [tool.name for tool in github_tools]
            )

            print(
                "Filesystem MCP tools:",
                [tool.name for tool in filesystem_tools]
            )

            # ----------------------------------
            # Convert MCP tools into LangChain tools
            # ----------------------------------

            langchain_tools = [
                create_langchain_tool(
                    github_connection,
                    tool
                )
                for tool in github_tools
            ]

            langchain_tools.extend([
                create_langchain_tool(
                    filesystem_connection,
                    tool
                )
                for tool in filesystem_tools
            ])

            print(
                f"Total LangChain tools: {len(langchain_tools)}"
            )

            # ----------------------------------
            # Create LangChain agent
            # ----------------------------------

            agent = create_agent(
                model="openai:gpt-4.1-mini",
                tools=langchain_tools,
                system_prompt="""
                You are a GitHub Developer Assistant with access to
                two MCP servers:

                1. GitHub MCP server
                2. Filesystem MCP server

                Your task is to coordinate tools from both servers.

                GITHUB OPERATIONS:
                - Search GitHub repositories.
                - Inspect pull requests.
                - Create GitHub issues.

                FILESYSTEM OPERATIONS:
                - List directories.
                - Create directories.
                - Write files.
                - Read files.
                - Search files.

                IMPORTANT WORKFLOW RULES:

                1. Always retrieve GitHub information using GitHub MCP tools.
                2. Never invent repository information.
                3. Use actual results returned by GitHub tools.
                4. Check whether the reports directory exists.
                5. Create the Markdown report using filesystem tools.
                6. Read the report back after writing.
                7. Confirm that read-back contains expected content.
                8. Never claim a file was saved without write confirmation.
                9. Never claim verification without reading the file.
                10. Explain which operation failed if a tool fails.

                When creating a report:
                - Use clear Markdown headings.
                - Include repository names.
                - Include descriptions when available.
                - Include stars when available.
                - Include repository URLs when available.
                - Include programming languages when available.
                - Do not invent missing information.
                """
            )

            # ----------------------------------
            # Cross-server workflow
            # ----------------------------------

            user_request = f"""
            Perform the following complete workflow.

            STEP 1 - GITHUB

            Use GitHub MCP to search for the top 5
            Python repositories related to LangGraph.

            Use the search_repo tool.

            Retrieve repository information from GitHub.
            Do not invent repository information.

            STEP 2 - PREPARE REPORT

            Prepare a Markdown report containing:

            # LangGraph Python Repository Report

            ## Introduction

            Briefly explain what the report contains.

            ## Repositories

            For each repository, include:
            - Repository name
            - Description
            - Stars, if available
            - Primary language, if available
            - GitHub URL

            ## Observations

            Summarize the actual search results.

            STEP 3 - FILESYSTEM

            Project directory:

            {PROJECT_DIRECTORY}

            Report directory:

            {REPORT_DIRECTORY}

            Report file:

            {REPORT_PATH}

            Check whether the reports directory exists.
            If it does not exist, create it.

            Write the Markdown report to the exact report path.

            STEP 4 - VERIFY

            Read the report from the same path.

            Check that it contains the repository information.

            Finally, tell me:
            - How many repositories were retrieved.
            - Which file was created.
            - Whether read-back verification succeeded.

            Do not skip any step.
            """

            print(
                "\nStarting GitHub -> Filesystem workflow..."
            )

            response = await agent.ainvoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": user_request
                        }
                    ]
                }
            )

            print("\nAgent response:\n")

            print(
                response["messages"][-1].content
            )

        finally:

            await github_connection.close()
            await filesystem_connection.close()


# --------------------------------------------------
# Entry Point
# --------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
