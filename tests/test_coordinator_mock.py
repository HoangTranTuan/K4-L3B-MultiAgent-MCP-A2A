from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from student_agent.cases import ResolvedEntity, create_mock_case, create_mock_resolved_entity
from student_agent.contracts import Contracts
from student_agent.trace import TraceWriter
from student_agent.workflow import (
    A2AMessage,
    Coordinator,
    OrderSpecialist,
    PaymentSpecialist,
    PolicySpecialist,
    ShipmentSpecialist,
    SpecialistAgent,
    solve_case,
)


def test_specialists_model_and_temperature() -> None:
    """Specialists must be configured with model qwen/qwen3.5-9b and temperature 0.0."""
    specialists = [
        OrderSpecialist(),
        ShipmentSpecialist(),
        PaymentSpecialist(),
        PolicySpecialist(),
    ]
    for spec in specialists:
        assert spec.model == "qwen/qwen3.5-9b", f"{spec.name} has incorrect model {spec.model}"
        assert spec.temperature == 0.0, f"{spec.name} has incorrect temperature {spec.temperature}"


def test_a2a_message_protocol() -> None:
    """A2A message envelope must contain case_id, from, to, type, payload."""
    payload = {"entity_id": "item_123", "status": "ok"}
    msg = A2AMessage(
        case_id="L3B_CASE_001",
        from_actor="coordinator",
        to="order_specialist",
        type="task_assigned",
        payload=payload,
    )
    msg_dict = msg.to_dict()

    assert msg_dict["case_id"] == "L3B_CASE_001"
    assert msg_dict["from"] == "coordinator"
    assert msg_dict["to"] == "order_specialist"
    assert msg_dict["type"] == "task_assigned"
    assert msg_dict["payload"] == payload

    reconstituted = A2AMessage.from_dict(msg_dict)
    assert reconstituted.case_id == msg.case_id
    assert reconstituted.from_actor == msg.from_actor
    assert reconstituted.to == msg.to
    assert reconstituted.type == msg.type
    assert reconstituted.payload == msg.payload


def test_coordinator_semaphore_initialization() -> None:
    """Coordinator must use asyncio.Semaphore(5) to throttle API requests."""
    coordinator = Coordinator(concurrency_limit=5)
    # Semaphore internal counter starts at concurrency_limit (5)
    assert coordinator.semaphore._value == 5


def test_coordinator_round_budget_prevents_loop() -> None:
    """Coordinator must terminate and respect max_rounds budget."""
    coordinator = Coordinator(max_rounds=2, concurrency_limit=5)
    entity = create_mock_resolved_entity(case_id="L3B_CASE_BUDGET_01")
    results = asyncio.run(coordinator.coordinate(entity))

    assert isinstance(results, dict)
    assert len(results) > 0
    # Specialists have responded and coordination terminated within budget
    for name, a2a_res in results.items():
        assert a2a_res.type == "result"
        assert a2a_res.from_actor == name
        assert a2a_res.case_id == "L3B_CASE_BUDGET_01"


def test_coordinator_logs_task_assigned(capsys: pytest.CaptureFixture[str]) -> None:
    """Coordinator execution must visibly print/log [task_assigned] for each routed specialist."""
    coordinator = Coordinator(concurrency_limit=5, max_rounds=2)
    entity = create_mock_resolved_entity(
        case_id="L3B_CASE_TEST_001",
        complaint_type="late_delivery",
    )
    asyncio.run(coordinator.coordinate(entity))

    captured = capsys.readouterr()
    assert "[task_assigned]" in captured.out
    assert "Coordinator ->" in captured.out
    assert "assigned case L3B_CASE_TEST_001" in captured.out


def test_coordinator_trace_emission(tmp_path: Path) -> None:
    """Coordinator must emit task_assigned trace event with schema compliance."""
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace_path = tmp_path / "traces" / "trace.jsonl"
    trace = TraceWriter(trace_path, contracts)

    coordinator = Coordinator(concurrency_limit=5, max_rounds=1)
    entity = create_mock_resolved_entity(case_id="L3B_CASE_TRACE_001")
    asyncio.run(coordinator.coordinate(entity, trace=trace))

    lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) > 0
    import json

    events = [json.loads(line) for line in lines]
    task_assigned_events = [e for e in events if e.get("event_type") == "task_assigned"]
    assert len(task_assigned_events) > 0
    for evt in task_assigned_events:
        assert evt["actor"] == "coordinator"
        assert evt["case_id"] == "L3B_CASE_TRACE_001"
        assert evt["target"] in coordinator.specialists
        assert "domain" in evt["attributes"]
        assert evt["attributes"]["model"] == "qwen/qwen3.5-9b"


def test_solve_case_schema_compliance(tmp_path: Path) -> None:
    """solve_case must produce an output valid under l3b-output-v2 schema."""
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace_path = tmp_path / "traces" / "trace.jsonl"
    trace = TraceWriter(trace_path, contracts)

    case = create_mock_case(case_id="L3B_CASE_SCHEMA_01")
    # Gateway can be None in Phase 1 mock
    output = asyncio.run(solve_case(case, gateway=None, trace=trace))  # type: ignore[arg-type]

    contracts.validate_output(output, "mock solve_case output")
    assert output["case_id"] == "L3B_CASE_SCHEMA_01"
    assert output["schema_version"] == "day09-l3b-output-v2"

