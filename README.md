# AI GitHub Developer Assistant

An AI-powered GitHub developer assistant built with the **Model Context Protocol (MCP)**, **LangChain**, and **LangGraph**. The project exposes GitHub operations through an MCP server and connects those tools to an LLM-powered agent.

> **Project status:** The current implementation covers GitHub MCP tools, an MCP client, LangChain integration, and human-in-the-loop approval for issue creation. MCP Resources and Prompts are planned next; they are not described as implemented here.

## Current features

### GitHub service layer

The GitHub service layer communicates with the GitHub REST API and currently supports:

- Searching GitHub repositories.
- Retrieving pull request details.
- Creating GitHub issues.

### MCP server

The MCP server exposes the GitHub operations as tools that MCP-compatible clients can discover and invoke.

### LangChain agent integration

The agent:

- Starts the MCP server as a subprocess using the STDIO transport.
- Connects to it through the MCP Python client.
- Discovers available MCP tools.
- Makes those tools available to a LangChain agent.
- Uses an adapter to bridge MCP tool calls to LangChain-compatible tools.

### Human-in-the-loop approval

Issue creation is treated as a side-effecting action. The LangChain agent uses `HumanInTheLoopMiddleware` to pause before executing `create_issue`, allowing a human to approve, edit, or reject the proposed action.

The current approval workflow uses an in-memory LangGraph checkpointer. Checkpoints are not durable across process restarts.

## Architecture

```text
User
  |
  v
LangChain Agent (LLM)
  |
  v
LangChain tool adapter
  |
  v
MCP Client
  |
  | STDIO
  v
MCP Server
  |
  v
GitHub Service
  |
  v
GitHub REST API
```

For issue creation, the human-in-the-loop middleware pauses the agent before the MCP tool is executed.

## Project structure

The core project is organized approximately as follows:

```text
ai-github-assistant/
├── agent.py
├── server.py
├── .env
├── .gitignore
├── requirements.txt
├── services/
│   ├── __init__.py
│   └── github_service.py
└── tests/
```

Your local repository may contain additional files as development progresses.

## Prerequisites

- Python 3.10 or newer.
- A GitHub account.
- A GitHub personal access token with access to the repository you want to use.
- An OpenAI API key for the LangChain agent.

## Setup

### 1. Clone the repository

```bash
git clone <your-repository-url>
cd ai-github-assistant
```

### 2. Create and activate a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root:

```env
GITHUB_TOKEN=your_github_token
OPENAI_API_KEY=your_openai_api_key
```

Do not commit `.env` or expose either secret in logs, screenshots, or source control.

For the GitHub token, grant access only to the repositories needed for testing. The current issue-creation workflow requires **Issues: Read and write** permission for the target repository. Repository search and pull request access may require additional read permissions depending on the operation and repository visibility.

## Run the project

### Start the MCP server with MCP Inspector

From the project root, with the virtual environment activated:

```bash
mcp dev server.py
```

Use the Inspector to discover and test the available GitHub tools.

### Run the LangChain agent

```bash
python agent.py
```

The agent starts the MCP server as a subprocess, discovers its tools, and makes them available to the LangChain agent. When the agent proposes creating an issue, the human-in-the-loop middleware pauses for a decision.

## Human-in-the-loop behavior

The current policy allows read-only tools to execute without approval and requires human review for issue creation:

- `search_repositories`: no approval required.
- `get_pull_request`: no approval required.
- `create_issue`: human approval required.

The human can approve, edit, or reject the proposed action. Always inspect the owner, repository, title, and body before approving a write operation.

**Security note:** Middleware approval protects calls made through this LangChain agent. It does not automatically protect the MCP server from other clients that can connect to it. Enforce authorization at the server/service boundary as well when hardening the project.

## Development notes

- The MCP server and GitHub service layer are separate so API logic is not coupled to the agent.
- The MCP client communicates with the local server over STDIO.
- The LangChain adapter converts discovered MCP tool schemas into LangChain-compatible tools.
- The current in-memory checkpointer is suitable for learning and local experiments, not durable production workflows.
- Dependency versions are intentionally not pinned in this initial requirements file. For reproducible deployments, add tested version pins or a lock file.

## Planned improvements

- MCP Resources and Resource Templates.
- MCP Prompts.
- Streamable HTTP transport and remote MCP connectivity.
- Stronger authentication and authorization boundaries.
- More robust validation, error handling, retries, and logging.
- Persistent checkpoint storage.
- Automated tests and deployment.

## Security

- Keep `.env` out of version control.
- Never hard-code API tokens.
- Use least-privilege GitHub token permissions.
- Review all proposed write actions before approval.
- Do not assume agent-level HITL is a substitute for server-side authorization.
