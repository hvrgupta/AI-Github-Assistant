# AI GitHub Developer Assistant

The Enterprise Developer Assistant combines read-only GitHub repository
information with centrally managed internal engineering documentation.
Developers can ask about implementation details, architecture, API guidelines,
runbooks, and coding standards without the assistant modifying GitHub or
filesystem resources.

An AI-powered GitHub developer assistant built with the **Model Context Protocol (MCP)**, **LangChain**, and **LangGraph**. The project includes an authenticated GitHub MCP server, LangChain agents that consume MCP tools, and an example that connects to multiple MCP servers.

## Features

### GitHub integration

The GitHub service layer communicates with the GitHub REST API and supports:

- Searching repositories.
- Retrieving pull request details.
- Retrieving repository metadata.
- Retrieving repository file contents.
- Listing commits.
- Listing issues.
- Reading repository README files.

The MCP server exposes the GitHub operations as tools, and also provides an assistant-guidelines resource, a repository README resource template, and a pull-request review prompt. Tool responses use typed Pydantic output models.

### Authentication and permissions

The GitHub MCP server uses Streamable HTTP at `http://127.0.0.1:8000/mcp`. Its MCP bearer token is configured through `MCP_ACCESS_TOKEN`; `MCP_ACCESS_SCOPES` is restricted to `github:read`. The separate `GITHUB_TOKEN` authorizes read-only server requests to the GitHub API.

The FastAPI application uses a separate `AGENT_API_TOKEN`. Do not reuse the MCP token or GitHub token for this public API credential. The example issuer URL is local-development configuration, not an identity provider.

`GITHUB_TOKEN` is a prototype centrally managed shared credential. It is not
passed to the LLM or API responses. It is only used by the backend GitHub REST
service. Use a fine-grained GitHub token with read-only access to repository
contents and metadata, pull requests, commits, and issues. For private
repositories, grant read access only to the required repositories and do not
grant write or administration permissions. Classic PATs need only the
read-level repository scopes available for private repository access; never
grant workflow, issue-write, or repository-administration scopes.

### Read-only enforcement

The GitHub MCP server exposes only read-only repository operations. The FastAPI agent does not use human-in-the-loop middleware or approval endpoints. GitHub tools and filesystem tools are filtered by explicit application allowlists, MCP read-only annotations, and filesystem-root validation.

### Multiple MCP servers with LangChain

[`src/assistant/mcp/mcp_client_runtime.py`](./src/assistant/mcp/mcp_client_runtime.py) demonstrates dynamic tool discovery from both:

- The GitHub MCP server over Streamable HTTP.
- The MCP filesystem server over STDIO, launched with `npx`.

The script converts each discovered MCP tool's JSON Schema input into a Pydantic model and wraps it as a LangChain `StructuredTool`. This supports the common primitive, array, object, required, optional, default, and description fields used by these tools. JSON Schema is extensive; this example is not intended as a complete implementation of every JSON Schema keyword.

The filesystem server is restricted to `COMPANY_KNOWLEDGE_ROOT`. Locally this
defaults to `company-knowledge/` in the project. It runs in read-only mode and
does not expose arbitrary host filesystem locations.

## Architecture

The GitHub MCP server and the filesystem MCP server use different transports:

```text
LangChain agent
  ├── LangChain adapters ── MCP client ── Streamable HTTP ── GitHub MCP server ── GitHub REST API
  └── LangChain adapters ── MCP client ── STDIO ──────────── Filesystem MCP server
```

The assistant combines read-only GitHub and filesystem tools within LangGraph without performing mutations.

## Company knowledge directory

The local prototype knowledge base is under [`company-knowledge/`](./company-knowledge):

```text
company-knowledge/
├── architecture/
├── api-documentation/
├── runbooks/
├── coding-standards/
└── README.md
```

Add reviewed Markdown documentation to these directories. Do not place
credentials or secrets in the knowledge directory. The Filesystem MCP server
uses `COMPANY_KNOWLEDGE_ROOT` as its sole allowed root and starts with
read-only mode enabled.

## Project structure

```text
ai-github-assistant/
├── src/
│   └── assistant/
│       ├── github_agent_demo.py
│       ├── api.py
│       ├── github_mcp_server.py
│       ├── agent/
│       │   ├── __init__.py
│       │   ├── graph.py
│       │   ├── prompts.py
│       │   └── response.py
│       ├── mcp/
│       │   ├── __init__.py
│       │   ├── connection_manager.py
│       │   └── mcp_client_runtime.py
│       ├── models/
│       │   ├── __init__.py
│       │   └── requests.py
│       └── services/
│           ├── __init__.py
│           ├── filesystem.py
│           └── github_service.py
├── tests/
│   ├── test_api.py
│   ├── test_github.py
│   ├── test_multi_server_errors.py
│   ├── test_reconnection.py
│   ├── test_resources.py
│   └── test_server_errors.py
├── deploy/
│   └── docker/
│       ├── Dockerfile.api
│       ├── Dockerfile.caddy
│       ├── Dockerfile.mcp
│       ├── compose.yaml
│       ├── compose.local.yaml
│       ├── Caddyfile
│       └── Caddyfile.local
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── services/
│   │   ├── styles/
│   │   ├── types/
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── index.html
│   ├── package.json
│   ├── package-lock.json
│   ├── tsconfig.json
│   ├── tsconfig.app.json
│   ├── tsconfig.node.json
│   └── vite.config.ts
├── .env.example
├── .dockerignore
├── README.md
├── requirements.txt
├── requirements-mcp.txt
├── company-knowledge/
│   ├── architecture/
│   ├── api-documentation/
│   ├── runbooks/
│   ├── coding-standards/
│   └── README.md
└── reports/
```

## Prerequisites

- Python 3.11 or newer.
- Node.js and `npx` for the MCP filesystem server.
- A GitHub personal access token with read access to the repositories you use.
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

Make the `src` package importable in the active terminal:

```powershell
$env:PYTHONPATH = "$PWD\src"
```

```bash
export PYTHONPATH="$PWD/src"
```

### 2. Install Python dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` in the project root, then fill in the credentials:

```env
GITHUB_TOKEN=
OPENAI_API_KEY=
AGENT_API_TOKEN=
MCP_ACCESS_TOKEN=
MCP_ACCESS_SCOPES=github:read
COMPANY_KNOWLEDGE_ROOT=./company-knowledge
COMPANY_KNOWLEDGE_HOST_PATH=../../company-knowledge
CADDY_BASIC_AUTH_USERNAME=
CADDY_BASIC_AUTH_HASH=
```

Generate independent random values for `AGENT_API_TOKEN` and `MCP_ACCESS_TOKEN`, for example with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Keep the API token at least 32 characters long. Use `MCP_ACCESS_SCOPES=github:read` only. Never commit `.env` or print token values in logs.

| Variable | Purpose | Default |
|---|---|---|
| `GITHUB_TOKEN` | GitHub REST API credential | Required |
| `OPENAI_API_KEY` | LangChain model credential | Required |
| `AGENT_API_TOKEN` | Bearer token for the FastAPI endpoints | Required; at least 32 characters |
| `MCP_ACCESS_TOKEN` | Bearer token between the API and GitHub MCP server | Required |
| `MCP_ACCESS_SCOPES` | MCP scopes granted to that token | `github:read` |
| `GITHUB_MCP_URL` | API's GitHub MCP endpoint | `http://127.0.0.1:8000/mcp` |
| `MCP_SERVER_HOST`, `MCP_SERVER_PORT` | GitHub MCP bind address and port | `127.0.0.1`, `8000` |
| `MCP_RESOURCE_URL`, `MCP_AUTH_ISSUER_URL` | MCP authorization metadata | Local resource URL and `https://auth.example.com` |
| `FILESYSTEM_ROOT`, `PROJECT_DIRECTORY` | Filesystem MCP allowed root and project path | Project parent and project directory |
| `COMPANY_KNOWLEDGE_ROOT` | Filesystem MCP's in-process allowed root | Project `company-knowledge/` directory |
| `COMPANY_KNOWLEDGE_HOST_PATH` | Host or Linux bind path mounted at `/data/company-knowledge` | `../../company-knowledge` for Compose |
| `FILESYSTEM_SERVER_EXECUTABLE` | Optional preinstalled filesystem MCP binary; otherwise the local API uses `npx` | `npx` |
| `CADDY_DOMAIN` | Production HTTPS hostname for Caddy | `localhost` (local only) |
| `CADDY_BASIC_AUTH_USERNAME` | Shared browser and API Basic Auth username | Required |
| `CADDY_BASIC_AUTH_HASH` | Caddy-compatible BCrypt password hash | Required |
| `OPENAI_MODEL` | LangChain model identifier | `openai:gpt-4.1-mini` |
| `AGENT_TIMEOUT_SECONDS` | Maximum time for an agent request | `180` |
| `MAX_CONCURRENT_AGENT_RUNS` | Maximum in-flight agent requests | `8` |
| `MCP_STARTUP_MAX_ATTEMPTS`, `MCP_STARTUP_RETRY_DELAY_SECONDS` | API startup retries for MCP connections | `5`, `1` |
| `MCP_REQUEST_TIMEOUT_SECONDS`, `MCP_MAX_ATTEMPTS`, `MCP_RETRY_BASE_DELAY_SECONDS` | MCP request deadline and safe retry settings | `45`, `3`, `0.5` |
| `MCP_RECONNECT_MAX_ATTEMPTS`, `MCP_RECONNECT_BASE_DELAY_SECONDS` | MCP connection recovery settings | `3`, `1` |

## Run

### Start the GitHub MCP server

In one terminal, activate the environment and run:

```powershell
python -m assistant.github_mcp_server
```

The server listens at `http://127.0.0.1:8000/mcp`.

### Run the FastAPI agent API

Start the GitHub MCP server first. From another terminal, launch the API:

```powershell
python -m uvicorn assistant.api:app --host 127.0.0.1 --port 8080 --workers 1
```

The API connects to both the GitHub MCP server and the filesystem MCP server. The latter is started as an `npx` STDIO subprocess and is restricted to `COMPANY_KNOWLEDGE_ROOT` (the project `company-knowledge/` directory by default). It runs in read-only mode. Readiness remains unavailable until both MCP servers connect and their tools are discovered.

- `GET /health/live` reports that the API process is running.
- `GET /health/ready` returns `200` only when both MCP connections are ready.
- The `/api/v1/agent` request accepts a required `message` and optional UUID `thread_id`.
- The `/api/v1/agent` response includes `request_id` and `thread_id`.
- Supply the returned `thread_id` in follow-up requests to restore conversation state.
- `POST /api/v1/agent` accepts `{"message":"..."}` and requires `Authorization: Bearer <AGENT_API_TOKEN>`.
- The API exposes no approval or resume endpoints.
- Read-only requests execute immediately.

Example first request:

```json
{
  "message": "Find the LangGraph repository and summarize its purpose."
}
```

The response includes both identifiers:

```json
{
  "request_id": "request-uuid",
  "thread_id": "conversation-uuid",
  "status": "completed",
  "response": "..."
}
```

Example follow-up request:

```json
{
  "message": "Explain its architecture.",
  "thread_id": "conversation-uuid"
}
```

Conversation checkpoints use process-local `InMemorySaver` state and are lost
when the API restarts. The shared API bearer token does not provide individual
user-level conversation ownership.

Troubleshooting:

- `401` from the agent endpoint: send the `AGENT_API_TOKEN` as a Bearer token.
- `503` from `/health/ready`: start the GitHub MCP server and verify Node.js/`npx` can launch the filesystem MCP server.
- `502` from an agent request: use the returned request ID to correlate the structured API log; inspect the MCP server and GitHub service logs without printing credentials.
- `504` from an agent request: the operation timed out; verify MCP and upstream availability and submit a new read-only request if needed.

### Run the GitHub agent

In another terminal:

```powershell
python -m assistant.github_agent_demo --message "Find the top 5 Python LangGraph repositories"
```

This agent connects to the already-running GitHub MCP server over Streamable HTTP, discovers approved read-only tools, and runs repository search and pull-request examples.

### Run the multi-server example

With the GitHub MCP server running and Node.js/`npx` available:

```powershell
python -m assistant.mcp.mcp_client_runtime --message "Find relevant GitHub repositories and compare them with company knowledge"
```

The script connects to the GitHub server and launches the filesystem MCP server as an STDIO subprocess. A dedicated worker task owns each MCP transport context from connect through reconnect and shutdown; tool calls are queued through that owner so AnyIO cancellation scopes are entered and exited in the same task. The script discovers tools from each server, adapts their input schemas, and prints the agent's response.

#### MCP error handling

The multi-server example gives each MCP request a 45-second timeout. If a transport failure or timeout makes a session unusable, its connection manager closes the old session and attempts to establish a new one. Only tools explicitly annotated as read-only are replayed after reconnection, and the reconnect/replay is attempted once. Other retry-safe request failures use bounded retries with exponential backoff.

An MCP tool result marked as an error is different from a transport failure: its returned error text is surfaced immediately and is not retried. Because this system is read-only, approved tools may be safely replayed after a transport reconnect. Unknown, unannotated, and mutation-capable tools are rejected and are not replayed. If reconnection fails, the error identifies the server and recommends checking that it is running.

If a remote server becomes unreachable while its HTTP transport is shutting down, expected transport exceptions from the transport's background task are reported as shutdown warnings rather than replacing the workflow result. Unexpected cleanup errors remain visible, and both MCP connections are closed even if one close operation fails.

On the GitHub MCP server, GitHub API calls have a 20-second overall deadline. Read-only API calls retry transient connection/timeouts and HTTP 5xx failures up to two times. Other GitHub HTTP errors include the returned status and message in the MCP tool error.

### Test MCP resources and prompts

With the GitHub MCP server running:

```powershell
python .\tests\test_resources.py
```

Run the unit tests from the project root (with `PYTHONPATH` set as above):

```powershell
python -m unittest discover -s tests
```

### Run the React frontend locally

The frontend is in `frontend/`. For UI-only development:

```powershell
cd frontend
npm install
npm run dev
```

The Vite development server listens on `127.0.0.1:5173`. Full API
communication is tested through Caddy, because Caddy owns the shared Basic
Auth credentials and injects `AGENT_API_TOKEN` upstream. The browser never
receives or stores `AGENT_API_TOKEN`.

## Example developer questions

- "Explain how the authentication service works using repository code and architecture documentation."
- "Find the deployment instructions for the payment repository."
- "Identify coding standards relevant to a Java Spring Boot service."
- "Compare the implementation in `src/orders/` against our internal API guidelines."
- "Summarize recent commits in the developer portal repository."

The assistant should distinguish retrieved GitHub facts, internal document
facts, and its explanation. It should cite repository paths and URLs plus
paths below `company-knowledge/`, and explicitly state missing documentation.

## Docker Compose deployment

The Compose stack contains three services:

- `api`: FastAPI/LangChain agent, configured with exactly one Uvicorn worker and no host-published port.
- `github-mcp`: internal Streamable HTTP server with no host-published port.
- `caddy`: the only service that publishes host ports and the HTTPS reverse proxy.

The API and Caddy share the private `proxy` bridge network. The API and GitHub MCP server communicate over the `internal` `mcp-internal` network. The GitHub MCP server has a separate outbound-only bridge for GitHub REST API requests; the API uses its proxy network for OpenAI requests. No MCP endpoint is published to the host. The filesystem MCP server remains an STDIO child of the API container and is restricted to `/data/company-knowledge`.

Compose mounts `COMPANY_KNOWLEDGE_HOST_PATH` read-only at
`/data/company-knowledge`. On Linux deployment, set this variable to an
absolute host path such as `/srv/company-knowledge`. Local Compose defaults to
the repository's `company-knowledge/` directory.

The API image installs the filesystem MCP executable during its build using Node.js 24 and pins `@modelcontextprotocol/server-filesystem` to 2.4.0, so API startup does not need to download the package. The Python MCP SDK remains at 2.3.0 and the API continues to use `httpx2`.

### Configure secrets and domain

Copy `.env.example` to `.env`. Fill in `GITHUB_TOKEN`, `OPENAI_API_KEY`, `AGENT_API_TOKEN`, and `MCP_ACCESS_TOKEN` with separate values. Compose passes each secret only to the service that needs it; secrets are not build arguments and `.dockerignore` excludes `.env` files from the build context. Protect the deployment `.env` file with restrictive host permissions.

Set `MCP_ACCESS_SCOPES=github:read` only. Set `CADDY_DOMAIN` to a public DNS name whose A/AAAA record points to the host for production HTTPS. `localhost` is only a local fallback; Caddy can obtain a publicly trusted certificate only when the domain and public network prerequisites are correct.

Set `CADDY_BASIC_AUTH_USERNAME` and `CADDY_BASIC_AUTH_HASH`. Generate a Caddy
compatible BCrypt hash without putting the plaintext password in repository
files:

```powershell
docker run --rm caddy:2.10.2-alpine caddy hash-password --plaintext 'replace-with-a-strong-password'
```

Copy only the generated hash into `CADDY_BASIC_AUTH_HASH`. Caddy protects both
`/` and `/api/*` before forwarding a request. For API requests, Caddy replaces
the browser-facing Basic Authentication with:

```text
Authorization: Bearer <AGENT_API_TOKEN>
```

`AGENT_API_TOKEN` is not copied into the React image or frontend JavaScript.

Docker Compose `.env` interpolation treats `$` as a variable prefix. Store a
generated BCrypt hash with doubled dollar signs, for example:

```env
CADDY_BASIC_AUTH_HASH=$$2a$$14$$example-generated-hash
```

Compose converts `$$` back to `$` before passing the hash to Caddy.

### Local Docker Compose

With Docker Engine and Docker Compose v2.24.4 or newer installed, use the HTTP-only local Caddy override:

```powershell
docker compose --env-file .env -f deploy/docker/compose.yaml -f deploy/docker/compose.local.yaml config --quiet
docker compose --env-file .env -f deploy/docker/compose.yaml -f deploy/docker/compose.local.yaml build
docker compose --env-file .env -f deploy/docker/compose.yaml -f deploy/docker/compose.local.yaml up -d
docker compose --env-file .env -f deploy/docker/compose.yaml -f deploy/docker/compose.local.yaml ps
curl.exe http://localhost:8088/health/ready
docker compose --env-file .env -f deploy/docker/compose.yaml -f deploy/docker/compose.local.yaml logs -f api github-mcp caddy
docker compose --env-file .env -f deploy/docker/compose.yaml -f deploy/docker/compose.local.yaml down
```

### Production Caddy / EC2 prerequisites

For production, use `deploy/docker/compose.yaml` without the local override and replace `CADDY_DOMAIN` with the public DNS name. Caddy automatically provisions and renews HTTPS certificates. Before starting on an EC2 host, provision nothing through this project; independently prepare a supported Linux host with Docker Engine and the Compose plugin, point DNS at the host, allow inbound TCP 80/443 (and optionally UDP 443 for HTTP/3), and ensure sufficient disk space for the named volumes. Do not publish API or MCP ports in a firewall or Compose file.

```powershell
docker compose --env-file .env -f deploy/docker/compose.yaml config --quiet
docker compose --env-file .env -f deploy/docker/compose.yaml build
docker compose --env-file .env -f deploy/docker/compose.yaml up -d
docker compose --env-file .env -f deploy/docker/compose.yaml ps
docker compose --env-file .env -f deploy/docker/compose.yaml logs -f api github-mcp caddy
docker compose --env-file .env -f deploy/docker/compose.yaml down
```

`assistant-reports` retains filesystem reports across container replacement. `caddy-data` and `caddy-config` retain certificate and Caddy state. `docker compose down` keeps these volumes; `docker compose down -v` deletes them.

### Startup and health lifecycle

Compose starts `api` only after `github-mcp` passes its TCP health check, then starts Caddy only after the API readiness check succeeds. The API lifespan also starts and discovers the filesystem STDIO server before reporting ready. `/health/live` checks the API process; `/health/ready` checks both MCP client sessions. Compose restarts services after failures.

Run exactly one API instance with one Uvicorn worker. This remains the deployment default. Each agent request uses an isolated LangGraph thread and in-memory checkpointer.

Troubleshoot with `docker compose -f deploy/docker/compose.yaml ps` and `docker compose -f deploy/docker/compose.yaml logs api github-mcp caddy`. An unhealthy API usually means the GitHub MCP server or its credentials are unavailable, or filesystem MCP startup failed. An unhealthy Caddy container usually indicates an invalid Caddyfile or a proxy startup problem. For public HTTPS, verify DNS and inbound 80/443 reachability.

## Development notes

- `src/assistant/services/github_service.py` contains GitHub REST API operations; `src/assistant/github_mcp_server.py` exposes them through MCP.
- `src/assistant/agent/` contains prompts, LangGraph agent construction, and response formatting.
- `src/assistant/mcp/connection_manager.py` exposes the worker-owned MCP connection lifecycle.
- `src/assistant/models/requests.py` contains API settings and request/response models.
- `src/assistant/services/filesystem.py` contains filesystem allowlists and path validation.
- `src/assistant/github_agent_demo.py` demonstrates a strictly read-only GitHub LangChain agent with `--message`.
- `src/assistant/api.py` exposes an authenticated FastAPI endpoint with dynamic read-only MCP tool discovery.
- `src/assistant/mcp/mcp_client_runtime.py` demonstrates dynamic tool integration across Streamable HTTP and STDIO MCP servers.
- The agent examples use isolated in-memory state where configured.
- The filesystem server runs in read-only mode and is limited to its configured root.

## Security

- Keep `.env` out of version control.
- Never hard-code real API credentials or expose tokens in logs.
- Use least-privilege GitHub token permissions.
- Enforce authorization at the MCP server/service boundary; agent middleware is not a substitute for server-side checks.
- Keep the API and MCP bearer tokens separate, random, and outside version control.
- The MCP server grants only `github:read`.
- Unknown MCP tools and unannotated tools are rejected by the application.
- Filesystem paths outside `FILESYSTEM_ROOT` are rejected before an MCP call.
- Filesystem mutation tools are withheld by the official server and excluded by the application allowlist.
- Docker configuration uses separate API and MCP images, a persistent report volume, and Caddy-only host port publishing.
