# AI GitHub Developer Assistant

An AI-powered GitHub developer assistant built with the **Model Context Protocol (MCP)**, **LangChain**, and **LangGraph**. The project includes an authenticated GitHub MCP server, LangChain agents that consume MCP tools, and an example that connects to multiple MCP servers.

## Features

### GitHub integration

The GitHub service layer communicates with the GitHub REST API and supports:

- Searching repositories.
- Retrieving pull request details.
- Creating issues.
- Reading repository README files.

The MCP server exposes the GitHub operations as tools, and also provides an assistant-guidelines resource, a repository README resource template, and a pull-request review prompt. Tool responses use typed Pydantic output models.

### Authentication and permissions

The GitHub MCP server uses Streamable HTTP at `http://127.0.0.1:8000/mcp`. Its example token verifier recognizes two development tokens:

- `dev-read-token`: `github:read`
- `dev-write-token`: `github:read` and `github:write`

The MCP access token authorizes calls to the MCP server. Separately, `GITHUB_TOKEN` authorizes the server's requests to the GitHub API. For issue creation, both the MCP token and GitHub token must permit writes to the target repository.

These static tokens and the example issuer configuration are for local learning only. Replace them with a proper identity provider and secure token storage before deploying the server.

### Human confirmation for issue creation

The LangChain agent uses human-in-the-loop middleware to pause before invoking `create_issue`, so a person can approve, edit, or reject the proposed tool call. The MCP server also uses form elicitation to ask for confirmation before it sends the write request to GitHub. These are separate confirmation steps at the agent and server boundaries.

### Multiple MCP servers with LangChain

[`multi_server_agent.py`](./multi_server_agent.py) demonstrates dynamic tool discovery from both:

- The GitHub MCP server over Streamable HTTP.
- The MCP filesystem server over STDIO, launched with `npx`.

The script converts each discovered MCP tool's JSON Schema input into a Pydantic model and wraps it as a LangChain `StructuredTool`. This supports the common primitive, array, object, required, optional, default, and description fields used by these tools. JSON Schema is extensive; this example is not intended as a complete implementation of every JSON Schema keyword.

The example asks the agent to list the files in:

`D:\Dev\learn-agentic-ai\ai-github-assistant`

The filesystem server is configured with `D:\Dev\learn-agentic-ai` as its allowed root.

## Architecture

The GitHub MCP server and the filesystem MCP server use different transports:

```text
LangChain agent
  ├── LangChain adapters ── MCP client ── Streamable HTTP ── GitHub MCP server ── GitHub REST API
  └── LangChain adapters ── MCP client ── STDIO ──────────── Filesystem MCP server
```

The issue-creation flow has both agent-level approval and server-side elicitation before the GitHub API call.

## Project structure

```text
ai-github-assistant/
├── agent.py
├── multi_server_agent.py
├── server.py
├── test_resources.py
├── .env
├── requirements.txt
└── services/
    └── github_service.py
```

## Prerequisites

- Python 3.10 or newer.
- Node.js and `npx` for the MCP filesystem server.
- A GitHub personal access token with access to the repositories you use. Issue creation requires write access to issues in the target repository.
- An OpenAI API key for the LangChain agents.

## Setup

### 1. Create and activate a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Install Python dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file in the project root:

```env
GITHUB_TOKEN=your_github_token
OPENAI_API_KEY=your_openai_api_key
MCP_ACCESS_TOKEN=dev-read-token
```

Use `MCP_ACCESS_TOKEN=dev-write-token` when testing issue creation. The server currently recognizes only its two configured development token strings. Do not commit `.env` or print token values in logs.

## Run

### Start the GitHub MCP server

In one terminal, activate the environment and run:

```powershell
python .\server.py
```

The server listens at `http://127.0.0.1:8000/mcp`.

### Run the GitHub agent

In another terminal:

```powershell
python .\agent.py
```

This agent connects to the already-running GitHub MCP server over Streamable HTTP, discovers its tools, and runs its repository-search and issue-creation examples. Creating an issue requires the `dev-write-token`, a GitHub token with repository write access, and confirmation at both approval prompts.

### Run the multi-server example

With the GitHub MCP server running and Node.js/`npx` available:

```powershell
python .\multi_server_agent.py
```

The script connects to the GitHub server and launches the filesystem MCP server as an STDIO subprocess. A dedicated worker task owns each MCP transport context from connect through reconnect and shutdown; tool calls are queued through that owner so AnyIO cancellation scopes are entered and exited in the same task. The script discovers tools from each server, adapts their input schemas, and prints the agent's response.

#### MCP error handling

The multi-server example gives each MCP request a 45-second timeout. If a transport failure or timeout makes a session unusable, its connection manager closes the old session and attempts to establish a new one. Only tools explicitly annotated as read-only are replayed after reconnection, and the reconnect/replay is attempted once. Other retry-safe request failures use bounded retries with exponential backoff.

An MCP tool result marked as an error is different from a transport failure: its returned error text is surfaced immediately and is not retried. Tools without an explicit read-only annotation—including write operations—are never replayed after a timeout or connection loss, because the server may have completed the action before the response was lost. The connection is re-established for later work, and the error says the write outcome may be unknown. If reconnection itself fails, the error identifies the server and recommends checking that it is running.

On the GitHub MCP server, GitHub API calls have a 20-second overall deadline. Read-only API calls retry transient connection/timeouts and HTTP 5xx failures up to two times. Issue creation is never retried automatically: if its request times out or loses its connection, the issue may already have been created, so check GitHub before trying again. Other GitHub HTTP errors include the returned status and message in the MCP tool error.

### Test MCP resources and prompts

With the GitHub MCP server running:

```powershell
python .\test_resources.py
```

## Development notes

- `services/github_service.py` contains GitHub REST API operations; `server.py` exposes them through MCP.
- `agent.py` demonstrates a GitHub-only LangChain agent and MCP elicitation.
- `multi_server_agent.py` demonstrates dynamic tool integration across Streamable HTTP and STDIO MCP servers.
- The agent examples use in-memory state where configured; this is suitable for local learning, not durable production workflows.
- The filesystem server is limited to its configured root, but filesystem write-capable tools are still available to the agent. Review the request and tool actions carefully.

## Security

- Keep `.env` out of version control.
- Never hard-code real API credentials or expose tokens in logs.
- Use least-privilege GitHub token permissions.
- Review issue details before approving write operations.
- Enforce authorization at the MCP server/service boundary; agent middleware is not a substitute for server-side checks.
- Replace the example static token verifier before exposing the server beyond a trusted local environment.
