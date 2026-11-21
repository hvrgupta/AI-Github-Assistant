import asyncio
import logging
import tempfile
import unittest
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from assistant.api import (
    JsonLogFormatter,
    FILESYSTEM_READ_ONLY_TOOLS,
    GITHUB_READ_ONLY_TOOLS,
    create_app,
    validate_filesystem_arguments,
)
from assistant.agent.prompts import SYSTEM_PROMPT
from assistant.agent.graph import create_read_only_agent
from assistant.models.requests import APISettings


SETTINGS = APISettings(
    api_token="a" * 40,
    mcp_access_token="test-mcp-token",
    openai_api_key="test-openai-key",
    github_mcp_url="http://127.0.0.1:8000/mcp",
)


def agent_result(response):
    return SimpleNamespace(
        value={"messages": [SimpleNamespace(type="ai", content=response)]},
        interrupts=(),
    )


class FakeAgent:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def ainvoke(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        return self.result


class ConversationAgent:
    def __init__(self):
        self.history: dict[str, list[str]] = {}
        self.configs: list[str] = []

    async def ainvoke(self, input_value, **kwargs):
        thread_id = kwargs["config"]["configurable"]["thread_id"]
        self.configs.append(thread_id)
        message = input_value["messages"][0]["content"]
        self.history.setdefault(thread_id, []).append(message)
        response = " | ".join(self.history[thread_id])
        return agent_result(response)


class TimeoutAgent:
    async def ainvoke(self, *_args, **_kwargs):
        await asyncio.sleep(0.1)
        return agent_result("Too late")


class FakeRuntime:
    def __init__(self, agents=(), ready=True, settings=None):
        self.settings = settings or SETTINGS
        self.agents = list(agents)
        self.agent_semaphore = asyncio.Semaphore(2)
        self.ready = ready
        self.checkpointer = InMemorySaver()

    async def start(self):
        pass

    async def close(self):
        pass

    def is_ready(self):
        return self.ready

    def create_agent(self):
        if not self.agents:
            raise RuntimeError("No fake agent configured")
        if len(self.agents) == 1:
            return self.agents[0]
        return self.agents.pop(0)


class ReadOnlyAPITests(unittest.TestCase):
    def create_client(self, agents=(), ready=True, settings=None):
        runtime = FakeRuntime(
            agents,
            ready,
            settings=settings,
        )
        app = create_app(
            settings=settings or SETTINGS,
            runtime_factory=lambda _settings: runtime,
        )
        client = TestClient(app)
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client

    def test_agent_factory_reuses_supplied_checkpointer(self):
        checkpointer = InMemorySaver()
        with patch(
            "assistant.agent.graph.create_agent"
        ) as create_agent:
            create_read_only_agent(
                [],
                checkpointer=checkpointer,
            )
        kwargs = create_agent.call_args.kwargs
        self.assertIs(kwargs["checkpointer"], checkpointer)

    def test_health_and_readiness(self):
        client = self.create_client()
        self.assertEqual(client.get("/health/live").status_code, 200)
        self.assertEqual(client.get("/health/ready").status_code, 200)

    def test_readiness_reports_unavailable(self):
        client = self.create_client(ready=False)
        self.assertEqual(client.get("/health/ready").status_code, 503)

    def test_bearer_authentication_is_required(self):
        client = self.create_client()
        response = client.post("/api/v1/agent", json={"message": "List docs"})
        self.assertEqual(response.status_code, 401)

    def test_extra_request_fields_are_rejected(self):
        client = self.create_client()
        response = client.post(
            "/api/v1/agent",
            headers={"Authorization": f"Bearer {SETTINGS.api_token}"},
            json={"message": "List docs", "unexpected": True},
        )
        self.assertEqual(response.status_code, 422)

    def test_completed_read_only_response(self):
        client = self.create_client([FakeAgent(agent_result("Retrieved PR details"))])
        response = client.post(
            "/api/v1/agent",
            headers={"Authorization": f"Bearer {SETTINGS.api_token}"},
            json={"message": "Get pull request 42"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(response.json()["response"], "Retrieved PR details")
        parsed_thread_id = uuid.UUID(response.json()["thread_id"])
        self.assertEqual(str(parsed_thread_id), response.json()["thread_id"])

    def test_first_request_generates_thread_id(self):
        client = self.create_client([
            FakeAgent(agent_result("Repository found"))
        ])
        response = client.post(
            "/api/v1/agent",
            headers={"Authorization": f"Bearer {SETTINGS.api_token}"},
            json={"message": "Find the LangGraph repository"},
        )
        self.assertEqual(response.status_code, 200)
        uuid.UUID(response.json()["thread_id"])

    def test_follow_up_reuses_supplied_thread_id(self):
        agent = ConversationAgent()
        client = self.create_client([agent])
        headers = {"Authorization": f"Bearer {SETTINGS.api_token}"}
        first = client.post(
            "/api/v1/agent",
            headers=headers,
            json={"message": "Find the LangGraph repository"},
        )
        thread_id = first.json()["thread_id"]
        follow_up = client.post(
            "/api/v1/agent",
            headers=headers,
            json={
                "message": "Explain its architecture",
                "thread_id": thread_id,
            },
        )
        self.assertEqual(follow_up.status_code, 200)
        self.assertEqual(follow_up.json()["thread_id"], thread_id)
        self.assertEqual(agent.configs, [thread_id, thread_id])
        self.assertIn(
            "Explain its architecture",
            follow_up.json()["response"],
        )

    def test_different_threads_are_isolated(self):
        agent = ConversationAgent()
        client = self.create_client([agent])
        headers = {"Authorization": f"Bearer {SETTINGS.api_token}"}
        first = client.post(
            "/api/v1/agent",
            headers=headers,
            json={"message": "LangGraph request"},
        )
        first_thread = first.json()["thread_id"]
        second = client.post(
            "/api/v1/agent",
            headers=headers,
            json={
                "message": "Authentication request",
                "thread_id": str(uuid.uuid4()),
            },
        )
        second_thread = second.json()["thread_id"]
        self.assertNotEqual(first_thread, second_thread)
        self.assertEqual(
            set(agent.history.keys()),
            {first_thread, second_thread},
        )
        self.assertNotIn(
            "LangGraph request",
            agent.history[second_thread],
        )

    def test_same_thread_shares_conversation_history(self):
        agent = ConversationAgent()
        client = self.create_client([agent])
        headers = {"Authorization": f"Bearer {SETTINGS.api_token}"}
        first = client.post(
            "/api/v1/agent",
            headers=headers,
            json={"message": "Repository purpose"},
        )
        thread_id = first.json()["thread_id"]
        second = client.post(
            "/api/v1/agent",
            headers=headers,
            json={"message": "Architecture", "thread_id": thread_id},
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            agent.history[thread_id],
            ["Repository purpose", "Architecture"],
        )
        self.assertIn(
            "Repository purpose",
            second.json()["response"],
        )

    def test_invalid_thread_id_is_rejected(self):
        client = self.create_client([FakeAgent(agent_result("unused"))])
        response = client.post(
            "/api/v1/agent",
            headers={"Authorization": f"Bearer {SETTINGS.api_token}"},
            json={"message": "Question", "thread_id": "not-a-uuid"},
        )
        self.assertEqual(response.status_code, 422)

    def test_empty_message_is_rejected(self):
        client = self.create_client([FakeAgent(agent_result("unused"))])
        response = client.post(
            "/api/v1/agent",
            headers={"Authorization": f"Bearer {SETTINGS.api_token}"},
            json={"message": "   ", "thread_id": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 422)

    def test_agent_timeout_preserves_thread_id(self):
        timeout_settings = replace(SETTINGS, agent_timeout_seconds=0.01)
        client = self.create_client(
            [TimeoutAgent()],
            settings=timeout_settings,
        )
        thread_id = str(uuid.uuid4())
        response = client.post(
            "/api/v1/agent",
            headers={
                "Authorization":
                f"Bearer {timeout_settings.api_token}"
            },
            json={"message": "Slow question", "thread_id": thread_id},
        )
        self.assertEqual(response.status_code, 504)
        self.assertIn("Request ID:", response.json()["detail"])

    def test_agent_error_does_not_expose_exception_text(self):
        client = self.create_client([FakeAgent(error=RuntimeError("secret-value"))])
        response = client.post(
            "/api/v1/agent",
            headers={"Authorization": f"Bearer {SETTINGS.api_token}"},
            json={"message": "Get pull request"},
        )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret-value", response.text)
        self.assertIn("Request ID:", response.json()["detail"])

    def test_github_allowlist_excludes_write_tools(self):
        self.assertIn("search_repo", GITHUB_READ_ONLY_TOOLS)
        self.assertIn("get_pull_request", GITHUB_READ_ONLY_TOOLS)
        self.assertIn("get_repository", GITHUB_READ_ONLY_TOOLS)
        self.assertIn("get_file_contents", GITHUB_READ_ONLY_TOOLS)
        self.assertIn("list_commits", GITHUB_READ_ONLY_TOOLS)
        self.assertIn("list_issues", GITHUB_READ_ONLY_TOOLS)
        self.assertNotIn("create_issue", GITHUB_READ_ONLY_TOOLS)
        self.assertNotIn("update_issue", GITHUB_READ_ONLY_TOOLS)

    def test_filesystem_allowlist_excludes_mutations(self):
        self.assertIn("read_text_file", FILESYSTEM_READ_ONLY_TOOLS)
        self.assertIn("list_directory", FILESYSTEM_READ_ONLY_TOOLS)
        self.assertIn("search_files", FILESYSTEM_READ_ONLY_TOOLS)
        for mutation in (
            "write_file",
            "append_file",
            "edit_file",
            "create_directory",
            "move_file",
            "copy_file",
            "delete_file",
        ):
            self.assertNotIn(mutation, FILESYSTEM_READ_ONLY_TOOLS)

    def test_unknown_tool_is_not_in_either_allowlist(self):
        self.assertNotIn("unknown_tool", GITHUB_READ_ONLY_TOOLS)
        self.assertNotIn("unknown_tool", FILESYSTEM_READ_ONLY_TOOLS)

    def test_filesystem_path_outside_root_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside the configured root"):
            validate_filesystem_arguments(
                "read_text_file",
                {"path": r"D:\Windows\System32\drivers\etc\hosts"},
            )

    def test_approval_endpoint_is_not_exposed(self):
        client = self.create_client()
        response = client.post(
            "/api/v1/agent/approvals/example",
            json={"decisions": ["approve"]},
        )
        self.assertEqual(response.status_code, 404)

    def test_company_knowledge_root_is_configurable(self):
        knowledge_root = tempfile.gettempdir() + "/company-knowledge"
        settings = APISettings(
            api_token="a" * 40,
            mcp_access_token="test-mcp-token",
            openai_api_key="test-openai-key",
            github_mcp_url="http://127.0.0.1:8000/mcp",
            company_knowledge_root=knowledge_root,
        )
        self.assertEqual(
            settings.company_knowledge_root,
            knowledge_root,
        )

    def test_cross_source_prompt_requests_citations_and_missing_status(self):
        prompt = SYSTEM_PROMPT.lower()
        self.assertIn("github", prompt)
        self.assertIn("filesystem", prompt)
        self.assertIn("cite", prompt)
        self.assertIn("if one source", prompt)

    def test_missing_documentation_response_is_explicit(self):
        client = self.create_client([
            FakeAgent(agent_result(
                "No matching internal document was retrieved."
            ))
        ])
        response = client.post(
            "/api/v1/agent",
            headers={"Authorization": f"Bearer {SETTINGS.api_token}"},
            json={"message": "Find the deployment runbook for unknown-service"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "No matching",
            response.json()["response"],
        )

    def test_cross_source_workflow_response_combines_sources(self):
        client = self.create_client([
            FakeAgent(agent_result(
                "GitHub repository hvrgupta/auth-service implements authentication "
                "(https://github.com/hvrgupta/auth-service). Internal guidance "
                "is available at company-knowledge/architecture/auth.md."
            ))
        ])
        response = client.post(
            "/api/v1/agent",
            headers={"Authorization": f"Bearer {SETTINGS.api_token}"},
            json={"message": "Compare authentication implementation to architecture docs"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("GitHub", response.json()["response"])
        self.assertIn("company-knowledge", response.json()["response"])

    def test_structured_log_formatter_does_not_include_secrets(self):
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="GITHUB_TOKEN=secret GITHUB_ACCESS_TOKEN=other",
            args=(),
            exc_info=None,
        )
        output = JsonLogFormatter().format(record)
        self.assertNotIn("secret", output)
        self.assertNotIn("GITHUB_TOKEN", output)


if __name__ == "__main__":
    unittest.main()
