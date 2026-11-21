import math
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator

from assistant.mcp.mcp_client_runtime import COMPANY_KNOWLEDGE_ROOT


@dataclass(frozen=True)
class APISettings:
    api_token: str
    mcp_access_token: str
    openai_api_key: str
    github_mcp_url: str
    company_knowledge_root: str = str(COMPANY_KNOWLEDGE_ROOT)
    agent_timeout_seconds: float = 180
    max_concurrent_agent_runs: int = 8
    startup_max_attempts: int = 5
    startup_retry_delay_seconds: float = 1

    def __post_init__(self) -> None:
        if len(self.api_token) < 32:
            raise ValueError(
                "AGENT_API_TOKEN must contain at least 32 characters."
            )
        if not self.mcp_access_token:
            raise ValueError("MCP_ACCESS_TOKEN must not be empty.")
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY must not be empty.")
        if not Path(self.company_knowledge_root).is_absolute():
            raise ValueError(
                "COMPANY_KNOWLEDGE_ROOT must be an absolute path."
            )
        if (
            not math.isfinite(self.agent_timeout_seconds)
            or self.agent_timeout_seconds <= 0
        ):
            raise ValueError("AGENT_TIMEOUT_SECONDS must be positive.")
        if self.max_concurrent_agent_runs < 1:
            raise ValueError("MAX_CONCURRENT_AGENT_RUNS must be positive.")
        if self.startup_max_attempts < 1:
            raise ValueError("MCP_STARTUP_MAX_ATTEMPTS must be positive.")
        if (
            not math.isfinite(self.startup_retry_delay_seconds)
            or self.startup_retry_delay_seconds < 0
        ):
            raise ValueError(
                "MCP_STARTUP_RETRY_DELAY_SECONDS cannot be negative."
            )

    @classmethod
    def from_environment(cls) -> "APISettings":
        load_dotenv()
        required = {
            "AGENT_API_TOKEN": os.getenv("AGENT_API_TOKEN"),
            "MCP_ACCESS_TOKEN": os.getenv("MCP_ACCESS_TOKEN"),
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(
                "Missing required environment variables: "
                + ", ".join(missing)
            )
        company_knowledge_root = str(
            Path(
                os.getenv(
                    "COMPANY_KNOWLEDGE_ROOT",
                    str(COMPANY_KNOWLEDGE_ROOT),
                )
            ).resolve()
        )
        return cls(
            api_token=required["AGENT_API_TOKEN"],
            mcp_access_token=required["MCP_ACCESS_TOKEN"],
            openai_api_key=required["OPENAI_API_KEY"],
            github_mcp_url=os.getenv(
                "GITHUB_MCP_URL",
                "http://127.0.0.1:8000/mcp",
            ),
            company_knowledge_root=company_knowledge_root,
            agent_timeout_seconds=float(
                os.getenv("AGENT_TIMEOUT_SECONDS", "180")
            ),
            max_concurrent_agent_runs=int(
                os.getenv("MAX_CONCURRENT_AGENT_RUNS", "8")
            ),
            startup_max_attempts=int(
                os.getenv("MCP_STARTUP_MAX_ATTEMPTS", "5")
            ),
            startup_retry_delay_seconds=float(
                os.getenv("MCP_STARTUP_RETRY_DELAY_SECONDS", "1")
            ),
        )


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=10_000)
    thread_id: UUID | None = None

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class AgentResponse(BaseModel):
    request_id: str
    thread_id: UUID
    status: str = "completed"
    response: str
