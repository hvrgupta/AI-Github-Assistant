import asyncio
import hmac
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langgraph.checkpoint.memory import InMemorySaver
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.client.stdio import StdioServerParameters

from assistant.agent import create_read_only_agent, format_agent_response
from assistant.mcp.connection_manager import MCPConnectionManager
from assistant.mcp.mcp_client_runtime import (
    COMPANY_KNOWLEDGE_ROOT,
    MCP_REQUEST_TIMEOUT_SECONDS,
    create_langchain_tool,
)
from assistant.models.requests import (
    APISettings,
    AgentRequest,
    AgentResponse,
)
from assistant.services.filesystem import (
    FILESYSTEM_READ_ONLY_TOOLS,
    validate_filesystem_arguments as _validate_filesystem_arguments,
)


load_dotenv()

GITHUB_READ_ONLY_TOOLS = frozenset({
    "search_repo",
    "get_pull_request",
    "get_repository",
    "get_file_contents",
    "list_commits",
    "list_issues",
})

logger = logging.getLogger("ai_github_assistant.api")
security = HTTPBearer(auto_error=False)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                "level": record.levelname,
                "event": getattr(record, "event", "application"),
                "request_id": getattr(record, "request_id", None),
                "error_type": getattr(record, "error_type", None),
                "error_detail": getattr(record, "error_detail", None),
                "github_tool_count": getattr(
                    record,
                    "github_tool_count",
                    None,
                ),
                "filesystem_tool_count": getattr(
                    record,
                    "filesystem_tool_count",
                    None,
                ),
                "total_tool_count": getattr(
                    record,
                    "total_tool_count",
                    None,
                ),
            },
            separators=(",", ":"),
        )


def configure_logging() -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def is_read_only_annotation(tool: Any) -> bool:
    annotations = getattr(tool, "annotations", None)
    return getattr(annotations, "read_only_hint", None) is True


def validate_filesystem_arguments(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return _validate_filesystem_arguments(
        tool_name,
        arguments,
        root=Path(
            os.getenv(
                "COMPANY_KNOWLEDGE_ROOT",
                str(COMPANY_KNOWLEDGE_ROOT),
            )
        ),
    )


class AgentRuntime:
    def __init__(self, settings: APISettings) -> None:
        self.settings = settings
        self.checkpointer = InMemorySaver()
        self.http_client: httpx2.AsyncClient | None = None
        self.github_connection: MCPConnectionManager | None = None
        self.filesystem_connection: MCPConnectionManager | None = None
        self.langchain_tools: list[Any] = []
        self.mcp_tools: list[Any] = []
        self.agent_semaphore = asyncio.Semaphore(
            settings.max_concurrent_agent_runs
        )
        self.ready = False

    async def _connect_with_retry(
        self,
        connection: MCPConnectionManager,
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(
            1,
            self.settings.startup_max_attempts + 1,
        ):
            try:
                await connection.connect()
                return
            except Exception as exc:
                last_error = exc
                if attempt == self.settings.startup_max_attempts:
                    break
                await asyncio.sleep(
                    self.settings.startup_retry_delay_seconds
                    * (2 ** (attempt - 1))
                )
        raise RuntimeError(
            f"{connection.server_name} MCP did not become available "
            f"after {self.settings.startup_max_attempts} attempts "
            f"({type(last_error).__name__})."
        ) from last_error

    async def start(self) -> None:
        os.environ["OPENAI_API_KEY"] = self.settings.openai_api_key
        http_client = httpx2.AsyncClient(
            headers={
                "Authorization":
                f"Bearer {self.settings.mcp_access_token}"
            },
            timeout=httpx2.Timeout(
                30,
                read=MCP_REQUEST_TIMEOUT_SECONDS,
            ),
        )
        self.http_client = http_client

        github_connection = MCPConnectionManager(
            "GitHub",
            lambda: Client(
                streamable_http_client(
                    url=self.settings.github_mcp_url,
                    http_client=http_client,
                )
            ),
        )
        filesystem_server_executable = os.getenv(
            "FILESYSTEM_SERVER_EXECUTABLE"
        )
        filesystem_args = (
            ["--read-only", self.settings.company_knowledge_root]
            if filesystem_server_executable
            else [
                "-y",
                "@modelcontextprotocol/server-filesystem",
                "--read-only",
                self.settings.company_knowledge_root,
            ]
        )
        filesystem_connection = MCPConnectionManager(
            "Filesystem",
            lambda: Client(
                StdioServerParameters(
                    command=filesystem_server_executable or "npx",
                    args=filesystem_args,
                    env=None,
                )
            ),
        )
        self.github_connection = github_connection
        self.filesystem_connection = filesystem_connection

        try:
            results = await asyncio.gather(
                self._connect_with_retry(github_connection),
                self._connect_with_retry(filesystem_connection),
                return_exceptions=True,
            )
            failures = [
                result
                for result in results
                if isinstance(result, BaseException)
            ]
            if failures:
                failed_names = [
                    connection.server_name
                    for connection, result in zip(
                        (github_connection, filesystem_connection),
                        results,
                    )
                    if isinstance(result, BaseException)
                ]
                raise RuntimeError(
                    "Could not connect to required MCP server(s): "
                    + ", ".join(failed_names)
                ) from failures[0]

            github_tools = [
                tool
                for tool in github_connection.tools
                if tool.name in GITHUB_READ_ONLY_TOOLS
                and is_read_only_annotation(tool)
            ]
            filesystem_tools = [
                tool
                for tool in filesystem_connection.tools
                if tool.name in FILESYSTEM_READ_ONLY_TOOLS
                and is_read_only_annotation(tool)
            ]
            rejected_names = [
                tool.name
                for tool in github_connection.tools
                if tool not in github_tools
            ] + [
                tool.name
                for tool in filesystem_connection.tools
                if tool not in filesystem_tools
            ]
            if rejected_names:
                logger.warning(
                    "Rejected MCP tools outside the read-only allowlist",
                    extra={
                        "event": "mcp_allowlist_rejection",
                        "request_id": None,
                    },
                )

            for tool in github_tools:
                self.langchain_tools.append(
                    create_langchain_tool(github_connection, tool)
                )
            for tool in filesystem_tools:
                self.langchain_tools.append(
                    create_langchain_tool(
                        filesystem_connection,
                        tool,
                        argument_validator=lambda tool_name, arguments:
                        validate_filesystem_arguments(
                            tool_name,
                            arguments,
                        ),
                    )
                )

            self.mcp_tools = github_tools + filesystem_tools
            names = [tool.name for tool in self.mcp_tools]
            if len(names) != len(set(names)):
                raise RuntimeError(
                    "MCP servers exposed duplicate approved tool names."
                )
            self.ready = True
            logger.info(
                "Read-only MCP connections ready",
                extra={
                    "event": "mcp_ready",
                    "request_id": None,
                    "github_tool_count": len(github_tools),
                    "filesystem_tool_count": len(filesystem_tools),
                    "total_tool_count": len(self.langchain_tools),
                },
            )
        except BaseException as startup_error:
            try:
                await self.close()
            except BaseException as cleanup_error:
                raise BaseExceptionGroup(
                    "MCP startup and cleanup both failed",
                    [startup_error, cleanup_error],
                ) from None
            raise

    def is_ready(self) -> bool:
        return bool(
            self.ready
            and self.github_connection is not None
            and self.filesystem_connection is not None
            and self.github_connection.client is not None
            and self.filesystem_connection.client is not None
        )

    def create_agent(self):
        return create_read_only_agent(
            self.langchain_tools,
            checkpointer=self.checkpointer,
        )

    async def close(self) -> None:
        self.ready = False
        close_operations = []
        if self.github_connection is not None:
            close_operations.append(self.github_connection.close())
        if self.filesystem_connection is not None:
            close_operations.append(
                self.filesystem_connection.close()
            )
        if self.http_client is not None:
            close_operations.append(self.http_client.aclose())
            self.http_client = None
        results = await asyncio.gather(
            *close_operations,
            return_exceptions=True,
        )
        errors = [
            result
            for result in results
            if isinstance(result, BaseException)
        ]
        if errors:
            raise BaseExceptionGroup(
                "One or more MCP connections failed to close",
                errors,
            )


def create_app(
    settings: APISettings | None = None,
    runtime_factory=AgentRuntime,
) -> FastAPI:
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_settings = settings or APISettings.from_environment()
        app.state.settings = resolved_settings
        runtime = runtime_factory(resolved_settings)
        app.state.runtime = runtime
        try:
            await runtime.start()
            yield
        finally:
            await runtime.close()

    app = FastAPI(
        title="Enterprise Read-Only Developer Assistant",
        version="3.0.0",
        lifespan=lifespan,
    )

    async def require_api_token(
        request: Request,
        credentials: HTTPAuthorizationCredentials
        | None = Depends(security),
    ) -> None:
        if (
            credentials is None
            or credentials.scheme.lower() != "bearer"
            or not hmac.compare_digest(
                credentials.credentials.encode("utf-8"),
                request.app.state.settings.api_token.encode("utf-8"),
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="A valid bearer token is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/health/live")
    async def liveness():
        return {"status": "alive"}

    @app.get("/health/ready")
    async def readiness(request: Request):
        runtime = getattr(request.app.state, "runtime", None)
        if runtime is None or not runtime.is_ready():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Required services are not ready.",
            )
        return {"status": "ready"}

    @app.post(
        "/api/v1/agent",
        response_model=AgentResponse,
    )
    async def run_agent(
        body: AgentRequest,
        request: Request,
        _authenticated: None = Depends(require_api_token),
    ):
        runtime: AgentRuntime = request.app.state.runtime
        request_id = str(uuid.uuid4())
        thread_id = body.thread_id or uuid.uuid4()
        config = {"configurable": {"thread_id": str(thread_id)}}
        try:
            agent = runtime.create_agent()
            async with runtime.agent_semaphore:
                result = await asyncio.wait_for(
                    agent.ainvoke(
                        {
                            "messages": [
                                {
                                    "role": "user",
                                    "content": body.message,
                                }
                            ]
                        },
                        config=config,
                        version="v2",
                    ),
                    timeout=runtime.settings.agent_timeout_seconds,
                )
        except TimeoutError as exc:
            logger.warning(
                "Agent request timed out",
                extra={
                    "event": "agent_timeout",
                    "request_id": request_id,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=(
                    f"Agent request timed out. "
                    f"Request ID: {request_id}"
                ),
            ) from exc
        except Exception as exc:
            logger.error(
                "Agent request failed",
                extra={
                    "event": "agent_failure",
                    "request_id": request_id,
                    "error_type": type(exc).__name__,
                    "error_detail": str(exc)[:500],
                },
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"Agent request failed. "
                    f"Request ID: {request_id}"
                ),
            ) from exc

        try:
            response_text = format_agent_response(result)
        except RuntimeError as exc:
            logger.error(
                "Agent returned no final response",
                extra={
                    "event": "agent_invalid_result",
                    "request_id": request_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Agent returned no final response. "
                    f"Request ID: {request_id}"
                ),
            ) from exc
        return AgentResponse(
            request_id=request_id,
            thread_id=thread_id,
            response=response_text,
        )

    return app


app = create_app()
