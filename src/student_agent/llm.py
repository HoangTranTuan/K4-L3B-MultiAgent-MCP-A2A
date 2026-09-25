from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx2

logger = logging.getLogger("student_agent.llm")

DEFAULT_MODEL = "qwen/qwen3.5-9b"
DEFAULT_OPENROUTER_URL = "https://openrouter.ai/api/v1"


class LLMClient:
    """Client for calling Qwen3.5-9B on OpenRouter with Native Tool Calling."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.0,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.api_key = (
            api_key
            or os.getenv("OPENROUTER_API_KEY", "")
            or os.getenv("LLM_API_KEY", "")
        ).strip()
        self.base_url = (
            base_url
            or os.getenv("OPENROUTER_BASE_URL", "")
            or DEFAULT_OPENROUTER_URL
        ).rstrip("/")
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        """Call OpenRouter Chat Completion API supporting native tool calling."""
        if not self.is_configured:
            raise RuntimeError(
                "OPENROUTER_API_KEY (or LLM_API_KEY) is not set in environment or .env."
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://day09.vinaction.local",
            "X-Title": "Day09 L3B Multi-Agent System",
        }

        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        if reasoning_effort:
            payload["extra_body"] = {"reasoning_effort": reasoning_effort}

        url = f"{self.base_url}/chat/completions"
        client_timeout = httpx2.Timeout(self.timeout, connect=15.0, write=15.0, pool=15.0)

        async with httpx2.AsyncClient(timeout=client_timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code != 200:
                raise RuntimeError(
                    f"OpenRouter API error {response.status_code}: {response.text}"
                )
            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                raise ValueError("OpenRouter API returned no choices in response")
            return choices[0]["message"]
