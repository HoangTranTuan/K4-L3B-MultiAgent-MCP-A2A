from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

TEAM_KEY_PATTERN = re.compile(r"^sk-team-[A-Za-z0-9_-]{16,128}$")


def _http_url(value: str, name: str) -> str:
    """Validate and normalize an absolute HTTP(S) URL."""
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{name} must be an absolute HTTP(S) URL")

    return normalized


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from the repository .env file/environment."""

    competition_api_url: str
    team_api_key: str
    mcp_endpoint: str
    root: Path
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen2.5:7b"
    llm_api_key: str = "ollama"

    @classmethod
    def load(cls, root: Path | None = None) -> Settings:
        resolved_root = (root or Path.cwd()).resolve()
        env_path = resolved_root / ".env"

        # Biến môi trường của CI/shell được ưu tiên hơn .env
        load_dotenv(env_path, override=False)

        raw_api_url = os.getenv("COMPETITION_API_URL", "")
        team_key = os.getenv(
            "COMPETITION_TEAM_API_KEY",
            "",
        ).strip()

        raw_mcp_endpoint = os.getenv("MCP_ENDPOINT", "")

        errors: list[str] = []

        try:
            api_url = _http_url(
                raw_api_url,
                "COMPETITION_API_URL",
            )
        except ValueError as exc:
            api_url = ""
            errors.append(str(exc))

        if not TEAM_KEY_PATTERN.fullmatch(team_key):
            errors.append(
                "COMPETITION_TEAM_API_KEY must use "
                "the sk-team-... format"
            )

        try:
            mcp_endpoint = _http_url(
                raw_mcp_endpoint,
                "MCP_ENDPOINT",
            )
        except ValueError as exc:
            mcp_endpoint = ""
            errors.append(str(exc))

        if errors:
            example_path = resolved_root / ".env.example"

            hint = (
                f" Copy {example_path} to "
                f"{env_path} and fill in your team values."
            )

            raise ValueError(
                "; ".join(errors) + hint
            )

        return cls(
            api_url,
            team_key,
            mcp_endpoint,
            resolved_root,
        )

    def safe_summary(self) -> dict[str, str]:
        """Return config values safe to print to logs."""

        return {
            "root": str(self.root),
            "competition_api_url": self.competition_api_url,
            "mcp_endpoint": self.mcp_endpoint,
            "team_api_key": "configured",
        }
