"""
Integration test for automatic GitHub MCP reconnection.

Run from the project root:
    python .\tests\test_reconnection.py

Requirements:
- Stop any manually running github_mcp_server.py server first (port 8000 must be free).
- MCP_ACCESS_TOKEN must be present in the project's .env file.
- This test starts and stops its own github_mcp_server.py subprocess.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import httpx2
from dotenv import load_dotenv

# Make the source package importable when this file is run directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
os.chdir(PROJECT_ROOT)

load_dotenv(PROJECT_ROOT / ".env")

from assistant.mcp import mcp_client_runtime as app  # noqa: E402


def build_required_arguments(tool) -> dict:
    """Build simple values for the required fields in an MCP tool schema."""
    schema = tool.input_schema or {}
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    values = {
        "string": "LangGraph Python",
        "integer": 5,
        "number": 1.0,
        "boolean": False,
        "array": [],
        "object": {},
    }

    arguments = {}
    for field_name in required:
        field_schema = properties.get(field_name, {})
        field_type = field_schema.get("type", "string")
        arguments[field_name] = values.get(field_type, "LangGraph Python")

    # Give search-like fields a useful search phrase even if optional.
    for field_name in properties:
        if field_name not in arguments and (
            "query" in field_name.lower()
            or "search" in field_name.lower()
        ):
            arguments[field_name] = "LangGraph Python"

    return arguments


async def start_server() -> asyncio.subprocess.Process:
    server_env = os.environ.copy()
    source_path = str(PROJECT_ROOT / "src")
    server_env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (source_path, server_env.get("PYTHONPATH")))
    )
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "assistant.github_mcp_server",
        cwd=str(PROJECT_ROOT),
        env=server_env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )


async def stop_server(process) -> None:
    if process is None or process.returncode is not None:
        return

    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=8)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def wait_for_initial_connection(manager, attempts: int = 20) -> None:
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            await manager.connect()
            return
        except Exception as exc:
            last_error = exc
            print(
                f"Waiting for GitHub MCP server "
                f"({attempt}/{attempts}): {exc}"
            )
            await asyncio.sleep(1)

    raise RuntimeError(
        f"GitHub MCP server did not become ready: {last_error}"
    ) from last_error


async def main():
    if not os.getenv("MCP_ACCESS_TOKEN"):
        raise RuntimeError(
            "MCP_ACCESS_TOKEN is missing. Check the project's .env file."
        )

    # Widen the retry window for this integration test so the test can
    # restart the server while the client is retrying.
    app.MCP_RECONNECT_MAX_ATTEMPTS = 5
    app.MCP_RECONNECT_BASE_DELAY_SECONDS = 1

    server_process = None
    restart_task = None
    manager = None

    async with httpx2.AsyncClient(
        headers={
            "Authorization": f"Bearer {os.environ['MCP_ACCESS_TOKEN']}"
        },
        timeout=app.MCP_REQUEST_TIMEOUT_SECONDS,
    ) as http_client:

        manager = app.MCPConnectionManager(
            "GitHub",
            lambda: app.Client(
                app.streamable_http_client(
                    url=app.GITHUB_MCP_URL,
                    http_client=http_client,
                )
            ),
        )

        try:
            print("[1/5] Starting GitHub MCP server...")
            server_process = await start_server()

            print("[2/5] Waiting for the initial MCP connection...")
            await wait_for_initial_connection(manager)

            search_tool = next(
                (tool for tool in manager.tools if tool.name == "search_repo"),
                None,
            )
            if search_tool is None:
                raise RuntimeError(
                    "The GitHub MCP server did not expose search_repo."
                )

            arguments = build_required_arguments(search_tool)
            print(f"Using search_repo arguments: {arguments}")

            print("[3/5] Stopping GitHub MCP server to simulate a failure...")
            await stop_server(server_process)
            server_process = None

            async def restart_after_delay():
                nonlocal server_process
                # Let the first connection attempt fail. The manager's
                # backoff gives the test time to bring the server back.
                await asyncio.sleep(2)
                print("[TEST] Restarting GitHub MCP server...")
                server_process = await start_server()

            restart_task = asyncio.create_task(restart_after_delay())

            print("[4/5] Calling search_repo; automatic recovery should occur...")
            result = await asyncio.wait_for(
                manager.call_tool(
                    "search_repo",
                    arguments,
                    retry_safe=True,
                ),
                timeout=90,
            )

            if result.is_error:
                raise RuntimeError(
                    "search_repo returned an MCP tool error: "
                    + app._error_text(result)
                )

            if restart_task is not None:
                await restart_task

            print("[5/5] PASS: the safe tool call succeeded after reconnection.")
            print("Automatic reconnection was exercised successfully.")

        finally:
            if restart_task is not None and not restart_task.done():
                restart_task.cancel()
                try:
                    await restart_task
                except asyncio.CancelledError:
                    pass

            if manager is not None:
                try:
                    await manager.close()
                except Exception as exc:
                    print(f"Warning while closing MCP client: {exc}")

            await stop_server(server_process)


if __name__ == "__main__":
    asyncio.run(main())
