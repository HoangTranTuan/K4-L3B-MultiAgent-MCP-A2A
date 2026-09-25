"""Local and Remote LLM client supporting sub-10B models.

Optimized for:
- Ollama endpoint (http://localhost:11434/v1) with models such as qwen2.5:7b, qwen2.5:3b
- OpenRouter endpoint with qwen/qwen3.5-9b, temperature=0.0
- Deterministic parsing and fallback mechanisms to ensure high calibration and zero crashes.
"""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI


class LLMClient:
    """Async client for interacting with Local LLM (Ollama) or OpenAI-compatible servers."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        model: str = "qwen2.5:7b",
        api_key: str = "ollama",
        temperature: float = 0.0,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = timeout
        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
        )

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        fallback_default: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate structured JSON response using the configured model.

        Falls back to fallback_default if LLM is unreachable or returns invalid JSON.
        """
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )
            raw_text = response.choices[0].message.content or "{}"
            # Extract JSON block if wrapped in markdown
            cleaned = raw_text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned.removeprefix("```json").removesuffix("```").strip()
            elif cleaned.startswith("```"):
                cleaned = cleaned.removeprefix("```").removesuffix("```").strip()
            return json.loads(cleaned)
        except Exception:
            # Fall back safely if LLM is offline or model still downloading
            return fallback_default or {}
