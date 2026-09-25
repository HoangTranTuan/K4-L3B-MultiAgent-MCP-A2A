from __future__ import annotations

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

    async def list_tools(self) -> list[str]:
        response = await self._session.list_tools()
        return sorted(tool.name for tool in response.tools)

    async def list_tool_definitions(self) -> list[dict[str, Any]]:
        """Return discovered MCP tools as OpenAI-compatible function definitions."""
        response = await self._session.list_tools()
        definitions: list[dict[str, Any]] = []
        for tool in response.tools:
            parameters = (
                tool.inputSchema
                if isinstance(tool.inputSchema, dict)
                else {"type": "object", "properties": {}}
            )
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": parameters,
                    },
                }
            )
        return definitions

    async def call(
        self,
        tool_name: str,
        *,
        case_id: str,
        caller_actor: str | None = None,
        **arguments: Any,
    ) -> dict[str, Any]:
        if caller_actor and caller_actor.lower() in FORBIDDEN_EVIDENCE_ACTORS:
            raise PermissionError(
                f"Actor '{caller_actor}' is forbidden from calling MCP evidence tool '{tool_name}' "
                f"under the Least Privilege principle (§2)."
            )
        payload = {"case_id": case_id, **arguments}
        result = await self._session.call_tool(tool_name, arguments=payload)
        if result.isError:
            message = " ".join(
                block.text for block in result.content if getattr(block, "text", None)
            )
            raise RuntimeError(f"MCP tool {tool_name} failed: {message or 'unknown error'}")
        evidence = getattr(result, "structuredContent", None)
        if evidence is None:
            evidence = getattr(result, "structured_content", None)
        if evidence is None:
            text_blocks = [block.text for block in result.content if getattr(block, "text", None)]
            if len(text_blocks) != 1:
                raise ValueError(f"MCP tool {tool_name} did not return one evidence object")
            evidence = json.loads(text_blocks[0])
        self._contracts.validate_evidence(evidence, f"MCP tool {tool_name}")
        return evidence


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
