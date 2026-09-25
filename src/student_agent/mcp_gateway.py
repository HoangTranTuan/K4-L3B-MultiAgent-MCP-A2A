from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .contracts import Contracts


FORBIDDEN_EVIDENCE_ACTORS = {"coordinator", "verifier", "conflict_resolver"}


def filter_tools_for_actor(
    tools: list[dict[str, Any]], actor_name: str
) -> list[dict[str, Any]]:
    """Filter MCP tool definitions to enforce least privilege per actor."""
    actor = actor_name.lower()
    if any(forbidden in actor for forbidden in ["coordinator", "verifier", "conflict"]):
        # Coordinator, Verifier, and Conflict Resolver are forbidden from evidence tools
        return []

    domain_keywords: dict[str, list[str]] = {
        "order": ["order", "item", "product", "seller", "catalog"],
        "shipment": ["ship", "tracking", "logistics", "carrier", "delivery"],
        "payment": ["payment", "refund", "transaction", "charge", "invoice", "price"],
        "policy": ["policy", "terms", "rules", "sla", "warranty"],
        "entity": ["customer", "history", "search", "lookup"],
    }

    keywords: list[str] = []
    for domain_key, kws in domain_keywords.items():
        if domain_key in actor:
            keywords.extend(kws)
            break

    if not keywords:
        return tools

    filtered: list[dict[str, Any]] = []
    for tool_def in tools:
        name = tool_def.get("function", {}).get("name", "").lower()
        desc = tool_def.get("function", {}).get("description", "").lower()
        if any(kw in name or kw in desc for kw in keywords):
            filtered.append(tool_def)
    return filtered


class EvidenceGateway:
    def __init__(self, session: ClientSession, contracts: Contracts) -> None:
        self._session = session
        self._contracts = contracts
        # Cache key: (case_id, tool_name, tuple(sorted(arguments.items())))
        self._cache: dict[tuple[str, str, tuple[tuple[str, Any], ...]], dict[str, Any]] = {}

    def clear_case_cache(self, case_id: str | None = None) -> None:
        """Clear cache for a specific case or all cases when case closes."""
        if case_id is None:
            self._cache.clear()
        else:
            keys_to_remove = [k for k in self._cache if k[0] == case_id]
            for k in keys_to_remove:
                del self._cache[k]

    async def list_tools(self) -> list[str]:
        response = await self._session.list_tools()
        return sorted(tool.name for tool in response.tools)

    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        cache_key = (case_id, tool_name, tuple(sorted(arguments.items())))
        if cache_key in self._cache:
            return self._cache[cache_key]

        payload = {"case_id": case_id, **arguments}

        # Retry up to 2 times on transient failures
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                result = await self._session.call_tool(tool_name, arguments=payload)
                is_err = getattr(result, "is_error", getattr(result, "isError", False))
                if is_err:
                    message = " ".join(
                        block.text for block in result.content if getattr(block, "text", None)
                    )
                    raise RuntimeError(f"MCP tool {tool_name} failed: {message or 'unknown error'}")
                evidence = getattr(result, "structuredContent", None)
                if evidence is None:
                    evidence = getattr(result, "structured_content", None)
                if evidence is None:
                    text_blocks = [
                        block.text for block in result.content if getattr(block, "text", None)
                    ]
                    if len(text_blocks) != 1:
                        raise ValueError(f"MCP tool {tool_name} did not return one evidence object")
                    evidence = json.loads(text_blocks[0])
                self._contracts.validate_evidence(evidence, f"MCP tool {tool_name}")
                self._cache[cache_key] = evidence
                return evidence
            except (httpx2.TimeoutException, TimeoutError) as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    raise RuntimeError(
                        f"MCP tool {tool_name} timed out after 3 attempts"
                    ) from last_exc

        raise RuntimeError(f"MCP tool {tool_name} failed unexpectedly") from last_exc


@asynccontextmanager
async def connect_gateway(
    endpoint: str, team_api_key: str, contracts: Contracts
) -> AsyncIterator[EvidenceGateway]:
    headers = {"Authorization": f"Bearer {team_api_key}"}
    timeout = httpx2.Timeout(300.0, connect=30.0, write=30.0, pool=30.0)
    async with (
        httpx2.AsyncClient(headers=headers, timeout=timeout) as http_client,
        streamable_http_client(endpoint, http_client=http_client) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield EvidenceGateway(session, contracts)
