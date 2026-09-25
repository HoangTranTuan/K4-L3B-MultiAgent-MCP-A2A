from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from dotenv import load_dotenv

# Ensure .env is loaded regardless of pytest cwd
root_dir = Path(__file__).resolve().parents[1]
load_dotenv(root_dir / ".env")

from student_agent.contracts import Contracts
from student_agent.llm import LLMClient
from student_agent.mcp_gateway import EvidenceGateway, filter_tools_for_actor
from student_agent.trace import TraceWriter
from student_agent.workflow import A2AMessage, OrderSpecialist, ShipmentSpecialist


def test_least_privilege_enforcement() -> None:
    """Coordinator, Verifier, and Conflict Resolver must be strictly forbidden from MCP evidence tools."""
    mock_session = MagicMock()
    mock_contracts = MagicMock()
    gateway = EvidenceGateway(mock_session, mock_contracts)

    forbidden_actors = ["coordinator", "verifier", "conflict_resolver", "Coordinator", "VERIFIER"]

    for actor in forbidden_actors:
        # 1. Filter tools must return empty list for forbidden actors
        dummy_tools = [
            {"type": "function", "function": {"name": "get_order_details", "description": "order"}},
            {"type": "function", "function": {"name": "track_shipment", "description": "shipment"}},
        ]
        assert filter_tools_for_actor(dummy_tools, actor) == []

        # 2. Direct call with forbidden actor must raise PermissionError
        with pytest.raises(PermissionError, match="forbidden from calling MCP evidence tool"):
            asyncio.run(
                gateway.call(
                    "get_order_details",
                    case_id="L3B_TEST_001",
                    caller_actor=actor,
                )
            )


def test_domain_filtering_for_specialists() -> None:
    """Specialists must only receive tool definitions corresponding to their domain."""
    sample_tools = [
        {"type": "function", "function": {"name": "get_customer_history", "description": "lookup customer history"}},
        {"type": "function", "function": {"name": "lookup_order_items", "description": "lookup order and items"}},
        {"type": "function", "function": {"name": "track_carrier_shipment", "description": "track carrier delivery shipment"}},
        {"type": "function", "function": {"name": "query_payment_status", "description": "check payment transactions"}},
        {"type": "function", "function": {"name": "get_refund_policy", "description": "get policy rules for return and refund"}},
    ]

    order_tools = filter_tools_for_actor(sample_tools, "order_specialist")
    order_tool_names = [t["function"]["name"] for t in order_tools]
    assert "lookup_order_items" in order_tool_names
    assert "track_carrier_shipment" not in order_tool_names

    shipment_tools = filter_tools_for_actor(sample_tools, "shipment_specialist")
    shipment_tool_names = [t["function"]["name"] for t in shipment_tools]
    assert "track_carrier_shipment" in shipment_tool_names
    assert "query_payment_status" not in shipment_tool_names

    payment_tools = filter_tools_for_actor(sample_tools, "payment_specialist")
    payment_tool_names = [t["function"]["name"] for t in payment_tools]
    assert "query_payment_status" in payment_tool_names
    assert "lookup_order_items" not in payment_tool_names

    policy_tools = filter_tools_for_actor(sample_tools, "policy_specialist")
    policy_tool_names = [t["function"]["name"] for t in policy_tools]
    assert "get_refund_policy" in policy_tool_names
    assert "track_carrier_shipment" not in policy_tool_names


def test_specialist_native_tool_calling_loop_mocked(tmp_path: Path) -> None:
    """Verify that Specialist executes native tool calling, extracts evidence_ref, and emits trace."""
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace_path = tmp_path / "traces" / "trace.jsonl"
    trace = TraceWriter(trace_path, contracts)

    # 1. Setup mock gateway
    mock_gateway = MagicMock()
    mock_gateway.list_tool_definitions = AsyncMock(
        return_value=[
            {
                "type": "function",
                "function": {
                    "name": "lookup_order",
                    "description": "Lookup order items",
                    "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}},
                },
            }
        ]
    )
    mock_gateway.call = AsyncMock(
        return_value={
            "schema_version": "day09-mcp-evidence-v1",
            "evidence_ref": "ev_order_test_mocked_0000000000001",
            "result_hash": f"sha256:{'a'*64}",
            "domain": "order",
            "data": {"order_id": "ORD_123", "status": "delivered"},
        }
    )

    # 2. Setup mock LLM responses simulating Qwen native tool calling:
    # Round 1: LLM outputs tool_calls
    # Round 2: LLM outputs final structured claim
    mock_llm = MagicMock()
    tool_call_response = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_qwen_01",
                "type": "function",
                "function": {"name": "lookup_order", "arguments": '{"order_id": "ORD_123"}'},
            }
        ],
    }
    final_claim_response = {
        "role": "assistant",
        "content": json.dumps({"verdict": "delivered", "order_found": True}),
    }
    mock_llm.chat_completion = AsyncMock(side_effect=[tool_call_response, final_claim_response])
    mock_llm.is_configured = True

    specialist = OrderSpecialist(llm_client=mock_llm)
    msg = A2AMessage(
        case_id="L3B_CASE_TOOL_001",
        from_actor="coordinator",
        to="order_specialist",
        type="task_assigned",
        payload={"complaint_type": "item_inquiry"},
    )

    res = asyncio.run(specialist.handle_message(msg, gateway=mock_gateway, trace=trace))

    # Assert gateway was called with least privilege actor identity
    mock_gateway.call.assert_awaited_once_with(
        "lookup_order",
        case_id="L3B_CASE_TOOL_001",
        caller_actor="order_specialist",
        order_id="ORD_123",
    )

    # Assert evidence_ref was collected
    assert res.payload["evidence_refs"] == ["ev_order_test_mocked_0000000000001"]

    # Assert tool_result_consumed trace event was emitted
    trace_lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(trace_lines) == 1
    event = json.loads(trace_lines[0])
    assert event["event_type"] == "tool_result_consumed"
    assert event["actor"] == "order_specialist"
    assert event["tool_name"] == "lookup_order"
    assert event["evidence_refs"] == ["ev_order_test_mocked_0000000000001"]


def test_live_qwen_tool_calling() -> None:
    """Optional live test: verifies real qwen/qwen3.5-9b native tool calling when API key is provided."""
    client = LLMClient(model="qwen/qwen3.5-9b", temperature=0.0)
    if not client.is_configured or not client.api_key.startswith("sk-"):
        pytest.skip("Valid OPENROUTER_API_KEY is required to run live test with qwen/qwen3.5-9b")

    test_tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup_tracking_status",
                "description": "Lookup carrier shipping status for an order",
                "parameters": {
                    "type": "object",
                    "properties": {"tracking_code": {"type": "string"}},
                    "required": ["tracking_code"],
                },
            },
        }
    ]

    messages = [
        {"role": "system", "content": "You are a shipment specialist. Use available tools to check tracking status."},
        {"role": "user", "content": "Please check tracking for package TRACK_9988."},
    ]

    response = asyncio.run(client.chat_completion(messages=messages, tools=test_tools))
    assert "tool_calls" in response
    tool_calls = response["tool_calls"]
    assert len(tool_calls) > 0
    assert tool_calls[0]["function"]["name"] == "lookup_tracking_status"
