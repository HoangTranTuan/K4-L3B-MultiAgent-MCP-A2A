from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Add src to pythonpath
root_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root_dir / "src"))

from dotenv import load_dotenv

load_dotenv(root_dir / ".env")

from student_agent.llm import LLMClient
from student_agent.mcp_gateway import filter_tools_for_actor


async def main() -> None:
    print("=" * 60)
    print("DAY09 L3B - TASK 2.1 VERIFICATION: QWEN 3.5-9B & TOOL CALLING")
    print("=" * 60)

    # 1. Least Privilege Check
    print("\n[1] Checking Least Privilege Enforcement...")
    dummy_tools = [
        {"type": "function", "function": {"name": "get_order_details", "description": "lookup order"}},
        {"type": "function", "function": {"name": "track_carrier_shipment", "description": "track shipment"}},
        {"type": "function", "function": {"name": "query_payment_status", "description": "check payment"}},
        {"type": "function", "function": {"name": "get_refund_policy", "description": "check policy"}},
    ]
    for actor in ["coordinator", "verifier", "conflict_resolver"]:
        allowed = filter_tools_for_actor(dummy_tools, actor)
        assert len(allowed) == 0, f"Actor {actor} should have 0 tools!"
        print(f"  ✓ Actor '{actor}': 0 tools allowed (Forbidden as required)")

    print("  ✓ Specialists tool filtering:")
    for spec in ["order_specialist", "shipment_specialist", "payment_specialist", "policy_specialist"]:
        allowed = filter_tools_for_actor(dummy_tools, spec)
        tool_names = [t["function"]["name"] for t in allowed]
        print(f"  ✓ '{spec}' -> tools: {tool_names}")

    # 2. LLM Configuration & Connection Check
    print("\n[2] Checking Qwen 3.5-9B Configuration...")
    client = LLMClient(model="qwen/qwen3.5-9b", temperature=0.0)
    print(f"  ✓ Model: {client.model}")
    print(f"  ✓ Temperature: {client.temperature}")
    print(f"  ✓ Base URL: {client.base_url}")
    print(f"  ✓ API Key configured: {'YES' if client.is_configured else 'NO (Set OPENROUTER_API_KEY in .env)'}")

    if not client.is_configured:
        print("\n[!] OPENROUTER_API_KEY is not set in .env.")
        print("    To run live tool calling against qwen/qwen3.5-9b, add to .env:")
        print("    OPENROUTER_API_KEY=sk-or-v1-...\n")
        print("=" * 60)
        print("✓ Task 2.1 Architecture and Least Privilege checks: PASSED")
        print("=" * 60)
        return

    # 3. Live Native Tool Calling with Qwen3.5-9B
    print("\n[3] Testing Native Tool Calling with qwen/qwen3.5-9b...")
    test_tools = [
        {
            "type": "function",
            "function": {
                "name": "track_carrier_shipment",
                "description": "Look up real-time carrier tracking status for a shipment",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tracking_number": {"type": "string", "description": "Shipment tracking number"}
                    },
                    "required": ["tracking_number"],
                },
            },
        }
    ]

    messages = [
        {
            "role": "system",
            "content": "You are a logistics specialist. Always use the track_carrier_shipment tool to check tracking.",
        },
        {
            "role": "user",
            "content": "Where is shipment SHIP_VN_882910 right now? Please track it.",
        },
    ]

    print("  -> Sending prompt to qwen/qwen3.5-9b with tool track_carrier_shipment...")
    try:
        response = await client.chat_completion(messages=messages, tools=test_tools)
        tool_calls = response.get("tool_calls", [])
        if tool_calls:
            print(f"  ✓ SUCCESS: Qwen3.5-9B generated native tool call:")
            for tc in tool_calls:
                func = tc.get("function", {})
                print(f"    - Function: {func.get('name')}")
                print(f"    - Arguments: {func.get('arguments')}")
        else:
            print(f"  [!] Model responded without tool_calls: {response.get('content')}")
    except Exception as e:
        print(f"  [x] Error calling OpenRouter: {e}")

    print("\n" + "=" * 60)
    print("Task 2.1 Live Verification Complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
