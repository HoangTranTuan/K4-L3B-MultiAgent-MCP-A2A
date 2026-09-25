from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .cases import ResolvedEntity, create_mock_resolved_entity
from .llm import LLMClient
from .mcp_gateway import EvidenceGateway, filter_tools_for_actor
from .trace import TraceWriter

logger = logging.getLogger("student_agent.coordinator")


def _make_mock_evidence_ref(domain: str, case_id: str, suffix: str = "01") -> str:
    """Generate a schema-compliant mock evidence reference matching ^ev_[A-Za-z0-9_-]{20,96}$."""
    clean_domain = domain.replace("-", "_")
    clean_case = case_id.replace("-", "_")
    body = f"{clean_domain}_{clean_case}_{suffix}".ljust(24, "0")[:40]
    return f"ev_{body}"


@dataclass
class A2AMessage:
    """Standard Agent-to-Agent (A2A) protocol envelope.

    Correlated across agents by case_id.
    """

    case_id: str
    from_actor: str
    to: str
    type: str  # "task_assigned" | "handoff" | "result" | "query"
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "from": self.from_actor,
            "to": self.to,
            "type": self.type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> A2AMessage:
        sender = data.get("from") or data.get("from_actor", "unknown")
        return cls(
            case_id=data["case_id"],
            from_actor=sender,
            to=data["to"],
            type=data["type"],
            payload=data.get("payload", {}),
            timestamp=data.get(
                "timestamp", datetime.now(UTC).isoformat().replace("+00:00", "Z")
            ),
        )


class SpecialistAgent:
    """Base class for all domain specialists in the L3B A2A architecture."""

    name: str = "specialist"
    domain: str = "general"
    model: str = "qwen/qwen3.5-9b"
    temperature: float = 0.0
    reasoning_effort: str = "low"

    def __init__(
        self,
        name: str | None = None,
        domain: str | None = None,
        model: str = "qwen/qwen3.5-9b",
        temperature: float = 0.0,
        reasoning_effort: str = "low",
        llm_client: LLMClient | None = None,
    ) -> None:
        if name:
            self.name = name
        if domain:
            self.domain = domain
        self.model = model
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.llm_client = llm_client or LLMClient(
            model=self.model,
            temperature=self.temperature,
        )

    async def handle_message(
        self,
        message: A2AMessage,
        gateway: EvidenceGateway | None = None,
        trace: TraceWriter | None = None,
    ) -> A2AMessage:
        """Process incoming A2A message and return result message."""
        if gateway is not None and self.llm_client.is_configured:
            payload = await self.process_llm_task(message, gateway, trace=trace)
        else:
            payload = await self.process_mock_task(message)
        return A2AMessage(
            case_id=message.case_id,
            from_actor=self.name,
            to=message.from_actor,
            type="result",
            payload=payload,
        )

    async def process_llm_task(
        self,
        message: A2AMessage,
        gateway: EvidenceGateway,
        trace: TraceWriter | None = None,
    ) -> dict[str, Any]:
        """Real LLM Native Tool Calling execution using Qwen 3.5-9B and MCP Gateway."""
        case_id = message.case_id

        # 1. Discover tools and filter for this specialist's domain (Least Privilege)
        all_tools = await gateway.list_tool_definitions()
        allowed_tools = filter_tools_for_actor(all_tools, self.name)

        system_prompt = (
            f"You are {self.name}, a domain specialist in '{self.domain}' for Day09 e-commerce investigation.\n"
            f"Ground all findings strictly on tool results. Call domain tools as needed to verify facts.\n"
            f"Never invent or alter evidence_ref values. Return a JSON object with your findings and claims."
        )
        user_prompt = (
            f"Case ID: {case_id}\n"
            f"Context: {json.dumps(message.payload, ensure_ascii=False)}"
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        collected_evidence_refs: list[str] = []
        max_tool_rounds = 3
        current_tool_round = 0

        while current_tool_round < max_tool_rounds:
            current_tool_round += 1
            response_msg = await self.llm_client.chat_completion(
                messages=messages,
                tools=allowed_tools if allowed_tools else None,
                reasoning_effort=self.reasoning_effort,
            )
            messages.append(response_msg)

            tool_calls = response_msg.get("tool_calls")
            if not tool_calls:
                break

            for tool_call in tool_calls:
                func = tool_call.get("function", {})
                func_name = func.get("name", "")
                raw_args = func.get("arguments", "{}")
                try:
                    tool_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except Exception:
                    tool_args = {}

                # Enforce least privilege by passing caller_actor
                mcp_res = await gateway.call(
                    func_name,
                    case_id=case_id,
                    caller_actor=self.name,
                    **tool_args,
                )
                ev_ref = mcp_res.get("evidence_ref")
                if ev_ref:
                    collected_evidence_refs.append(ev_ref)
                    if trace is not None:
                        trace.emit(
                            case_id=case_id,
                            event_type="tool_result_consumed",
                            actor=self.name,
                            tool_name=func_name,
                            evidence_refs=[ev_ref],
                        )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "name": func_name,
                        "content": json.dumps(mcp_res.get("data", mcp_res), ensure_ascii=False),
                    }
                )

        final_content = messages[-1].get("content") or ""
        try:
            claim_data = json.loads(final_content) if isinstance(final_content, str) else {}
        except Exception:
            claim_data = {"summary": str(final_content)}

        claim_data["domain"] = self.domain
        claim_data["model"] = self.model
        claim_data["evidence_refs"] = collected_evidence_refs
        return claim_data

    async def process_mock_task(self, message: A2AMessage) -> dict[str, Any]:
        """Mock processing logic for Phase 1. Overridden by domain specialists."""
        return {
            "status": "completed",
            "domain": self.domain,
            "model": self.model,
            "temperature": self.temperature,
            "evidence_refs": [_make_mock_evidence_ref(self.domain, message.case_id)],
        }


class OrderSpecialist(SpecialistAgent):
    """Specialist responsible for Order and Product investigation."""

    def __init__(
        self,
        model: str = "qwen/qwen3.5-9b",
        temperature: float = 0.0,
        llm_client: LLMClient | None = None,
    ) -> None:
        super().__init__(
            name="order_specialist",
            domain="order",
            model=model,
            temperature=temperature,
            reasoning_effort="low",
            llm_client=llm_client,
        )

    async def process_mock_task(self, message: A2AMessage) -> dict[str, Any]:
        case_id = message.case_id
        entity_data = message.payload.get("entity", {})
        order_ids = entity_data.get("resolved_order_ids") or [f"order_{case_id[:8]}_01"]
        item_ids = entity_data.get("item_ids") or [f"item_{order_ids[0]}_01"]
        seller_ids = entity_data.get("seller_ids") or ["seller_mock_001"]
        ev_ref = _make_mock_evidence_ref("order", case_id, "order_item_01")

        return {
            "domain": self.domain,
            "model": self.model,
            "temperature": self.temperature,
            "order_ids": order_ids,
            "item_ids": item_ids,
            "seller_ids": seller_ids,
            "claim_assessments": [
                {
                    "claim_id": f"claim_order_{case_id[:8]}",
                    "verdict": "supported",
                    "confidence": 0.95,
                    "evidence_refs": [ev_ref],
                }
            ],
            "evidence_refs": [ev_ref],
            "order_status": "delivered",
            "order_total_brl": 150.0,
        }


class ShipmentSpecialist(SpecialistAgent):
    """Specialist responsible for Shipment and Delivery timeline investigation."""

    def __init__(
        self,
        model: str = "qwen/qwen3.5-9b",
        temperature: float = 0.0,
        llm_client: LLMClient | None = None,
    ) -> None:
        super().__init__(
            name="shipment_specialist",
            domain="shipment",
            model=model,
            temperature=temperature,
            reasoning_effort="low",
            llm_client=llm_client,
        )

    async def process_mock_task(self, message: A2AMessage) -> dict[str, Any]:
        case_id = message.case_id
        entity_data = message.payload.get("entity", {})
        shipment_ids = entity_data.get("shipment_ids") or [f"ship_{case_id[:8]}_01"]
        ev_ref = _make_mock_evidence_ref("shipment", case_id, "tracking_01")

        complaint = str(message.payload.get("complaint_type", "")).lower()
        if "seller" in complaint:
            verdict = "seller_delay"
            late_sellers = entity_data.get("seller_ids") or ["seller_mock_001"]
        elif "logistics" in complaint or "late" in complaint:
            verdict = "logistics_delay"
            late_sellers = []
        else:
            verdict = "on_time"
            late_sellers = []

        return {
            "domain": self.domain,
            "model": self.model,
            "temperature": self.temperature,
            "shipment_ids": shipment_ids,
            "verdict": verdict,
            "late_seller_ids": late_sellers,
            "timeline_complete": True,
            "evidence_refs": [ev_ref],
        }


class PaymentSpecialist(SpecialistAgent):
    """Specialist responsible for Payment transactions and Refund status."""

    def __init__(
        self,
        model: str = "qwen/qwen3.5-9b",
        temperature: float = 0.0,
        llm_client: LLMClient | None = None,
    ) -> None:
        super().__init__(
            name="payment_specialist",
            domain="payment",
            model=model,
            temperature=temperature,
            reasoning_effort="low",
            llm_client=llm_client,
        )

    async def process_mock_task(self, message: A2AMessage) -> dict[str, Any]:
        case_id = message.case_id
        entity_data = message.payload.get("entity", {})
        ev_ref = _make_mock_evidence_ref("payment", case_id, "gateway_tx_01")

        order_ids = entity_data.get("resolved_order_ids") or [f"order_{case_id[:8]}_01"]
        payment_refs = entity_data.get("payment_references") or [f"pay_{case_id[:8]}_01"]

        return {
            "domain": self.domain,
            "model": self.model,
            "temperature": self.temperature,
            "payment_references": payment_refs,
            "verdict": "refund_pending",
            "captured_total_brl": 150.0,
            "refunded_total_brl": 0.0,
            "refundable_total_brl": 150.0,
            "financial_resolution": {
                "currency": "BRL",
                "recommended_refund_brl": 150.0,
                "refund_lines": [
                    {
                        "reason_code": "late_delivery_compensation",
                        "amount_brl": 150.0,
                        "entity_id": order_ids[0],
                    }
                ],
            },
            "evidence_refs": [ev_ref],
        }


class PolicySpecialist(SpecialistAgent):
    """Specialist responsible for E-commerce Platform Policy consultation."""

    def __init__(
        self,
        model: str = "qwen/qwen3.5-9b",
        temperature: float = 0.0,
        llm_client: LLMClient | None = None,
    ) -> None:
        super().__init__(
            name="policy_specialist",
            domain="policy",
            model=model,
            temperature=temperature,
            reasoning_effort="low",
            llm_client=llm_client,
        )

    async def process_mock_task(self, message: A2AMessage) -> dict[str, Any]:
        case_id = message.case_id
        ev_ref = _make_mock_evidence_ref("policy", case_id, "clause_sla_01")

        return {
            "domain": self.domain,
            "model": self.model,
            "temperature": self.temperature,
            "applicable_policy": "POLICY_REFUND_GUARANTEE_V2",
            "max_delivery_days": 10,
            "refund_allowed": True,
            "policy_verdict": "eligible_for_compensation",
            "evidence_refs": [ev_ref],
        }


class Coordinator:
    """Coordinator Agent: Routes mock entity to specialists and coordinates A2A messages.

    Protects downstream systems using asyncio.Semaphore(5) and prevents infinite loops
    via a bounded round budget.
    """

    def __init__(
        self,
        specialists: list[SpecialistAgent] | None = None,
        max_rounds: int = 2,
        concurrency_limit: int = 5,
    ) -> None:
        # Rate-limiting semaphore to prevent burst API calls
        self.semaphore = asyncio.Semaphore(concurrency_limit)
        # Maximum round budget to prevent infinite A2A loops
        self.max_rounds = max_rounds

        self.specialists: dict[str, SpecialistAgent] = {}
        default_specialists: list[SpecialistAgent] = specialists or [
            OrderSpecialist(),
            ShipmentSpecialist(),
            PaymentSpecialist(),
            PolicySpecialist(),
        ]
        for spec in default_specialists:
            self.specialists[spec.name] = spec

    def route_specialists(
        self, entity: ResolvedEntity | dict[str, Any]
    ) -> list[SpecialistAgent]:
        """Determine domain specialists to mobilize based on case complaint characteristics."""
        if isinstance(entity, ResolvedEntity):
            complaint_type = (entity.complaint_type or "").lower()
            description = (entity.description or "").lower()
        else:
            complaint_type = str(entity.get("complaint_type", "")).lower()
            description = str(entity.get("description", "")).lower()

        combined_text = f"{complaint_type} {description}"
        targeted: list[SpecialistAgent] = []

        if any(kw in combined_text for kw in ["order", "item", "product", "cancel", "seller"]):
            if "order_specialist" in self.specialists:
                targeted.append(self.specialists["order_specialist"])

        if any(kw in combined_text for kw in ["ship", "delivery", "late", "logistics", "delay"]):
            if "shipment_specialist" in self.specialists:
                targeted.append(self.specialists["shipment_specialist"])

        if any(kw in combined_text for kw in ["pay", "refund", "charge", "amount", "money", "brl"]):
            if "payment_specialist" in self.specialists:
                targeted.append(self.specialists["payment_specialist"])

        if any(kw in combined_text for kw in ["policy", "rule", "terms", "condition", "guarantee"]):
            if "policy_specialist" in self.specialists:
                targeted.append(self.specialists["policy_specialist"])

        # Fallback to all domain specialists if ambiguous or broad case
        if not targeted:
            targeted = list(self.specialists.values())

        return targeted

    async def dispatch_task(
        self,
        specialist: SpecialistAgent,
        message: A2AMessage,
        gateway: EvidenceGateway | None = None,
        trace: TraceWriter | None = None,
    ) -> A2AMessage:
        """Dispatch a single A2A task to a specialist under the concurrency semaphore."""
        case_id = message.case_id
        round_num = message.payload.get("round", 1)

        # 1. Log task_assigned explicitly to logger and stdout
        logger.info(
            "[task_assigned] Coordinator assigned case '%s' to '%s' (domain: %s, round: %d)",
            case_id,
            specialist.name,
            specialist.domain,
            round_num,
        )
        print(
            f"[task_assigned] Coordinator -> {specialist.name}: assigned case {case_id} "
            f"(domain: {specialist.domain}, round: {round_num})"
        )

        # 2. Emit observable trace event if trace writer is provided
        if trace is not None:
            trace.emit(
                case_id=case_id,
                event_type="task_assigned",
                actor="coordinator",
                target=specialist.name,
                attributes={
                    "domain": specialist.domain,
                    "model": specialist.model,
                    "round": round_num,
                },
            )

        # 3. Throttle execution using asyncio.Semaphore(5)
        async with self.semaphore:
            result_msg = await specialist.handle_message(
                message, gateway=gateway, trace=trace
            )

        return result_msg

    async def coordinate(
        self,
        entity: ResolvedEntity | dict[str, Any],
        gateway: EvidenceGateway | None = None,
        trace: TraceWriter | None = None,
    ) -> dict[str, A2AMessage]:
        """Execute the multi-agent coordination loop with round-budget loop prevention."""
        case_id = (
            entity.case_id
            if isinstance(entity, ResolvedEntity)
            else str(entity.get("case_id", "UNKNOWN"))
        )
        entity_dict = entity.to_dict() if isinstance(entity, ResolvedEntity) else dict(entity)

        all_results: dict[str, A2AMessage] = {}
        target_specialists = self.route_specialists(entity)

        current_round = 0
        while current_round < self.max_rounds:
            current_round += 1

            tasks: list[asyncio.Task[A2AMessage]] = []
            for spec in target_specialists:
                msg = A2AMessage(
                    case_id=case_id,
                    from_actor="coordinator",
                    to=spec.name,
                    type="task_assigned",
                    payload={
                        "round": current_round,
                        "entity": entity_dict,
                        "complaint_type": entity_dict.get("complaint_type"),
                        "description": entity_dict.get("description"),
                    },
                )
                tasks.append(
                    asyncio.create_task(
                        self.dispatch_task(spec, msg, gateway=gateway, trace=trace)
                    )
                )

            # Concurrent execution within this round
            round_results = await asyncio.gather(*tasks)
            for res in round_results:
                all_results[res.from_actor] = res

            # Stop once all routed specialists have returned findings
            if all(spec.name in all_results for spec in target_specialists):
                break

        return all_results


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Execute the L3B coordinator and specialist-agent workflow.

    In Phase 1, uses mock data and the A2A coordinator to orchestrate specialists.
    """
    case_id = case.get("case_id", "CASE_000")

    # Wrap incoming case into mock ResolvedEntity
    entity = ResolvedEntity(
        case_id=case_id,
        customer_unique_id=case.get("customer_hint", {}).get(
            "customer_unique_id", f"cust_{case_id[:8]}"
        ),
        resolved_order_ids=[f"order_{case_id[:8]}_01"],
        item_ids=[f"item_{case_id[:8]}_01"],
        seller_ids=["seller_mock_001"],
        shipment_ids=[f"ship_{case_id[:8]}_01"],
        payment_references=[f"pay_{case_id[:8]}_01"],
        status="resolved",
        confidence=0.95,
        rejected_candidates=["order_rejected_999"],
        complaint_type=case.get("complaint_type", "late_delivery"),
        description=case.get("description", "Mock case description"),
    )

    coordinator = Coordinator(concurrency_limit=5, max_rounds=2)
    specialist_results = await coordinator.coordinate(entity, gateway=gateway, trace=trace)

    # Synthesize claims from specialist results
    order_res = specialist_results.get("order_specialist")
    shipment_res = specialist_results.get("shipment_specialist")
    payment_res = specialist_results.get("payment_specialist")

    order_payload = order_res.payload if order_res else {}
    shipment_payload = shipment_res.payload if shipment_res else {}
    payment_payload = payment_res.payload if payment_res else {}

    all_evidence: list[str] = []
    for res in specialist_results.values():
        all_evidence.extend(res.payload.get("evidence_refs", []))
    if not all_evidence:
        all_evidence = [_make_mock_evidence_ref("coordinator", case_id, "default")]

    verdict_shipment = shipment_payload.get("verdict", "logistics_delay")
    verdict_payment = payment_payload.get("verdict", "refund_pending")

    primary_issue = (
        "late_delivery_logistics"
        if verdict_shipment == "logistics_delay"
        else "late_delivery_seller"
        if verdict_shipment == "seller_delay"
        else "canceled_order_paid"
    )

    output: dict[str, Any] = {
        "schema_version": "day09-l3b-output-v2",
        "case_id": case_id,
        "assessment": {
            "primary_issue": primary_issue,
            "secondary_issues": ["shipping_delay"],
            "case_status": "action_required",
            "confidence": 0.95,
        },
        "affected_entities": {
            "order_ids": entity.resolved_order_ids,
            "item_ids": entity.item_ids,
            "seller_ids": entity.seller_ids,
            "payment_references": entity.payment_references,
            "shipment_ids": entity.shipment_ids,
        },
        "claim_assessments": order_payload.get(
            "claim_assessments",
            [
                {
                    "claim_id": f"claim_{case_id[:8]}",
                    "verdict": "supported",
                    "confidence": 0.95,
                    "evidence_refs": all_evidence[:1],
                }
            ],
        ),
        "entity_resolution": {
            "status": "resolved",
            "resolved_order_ids": entity.resolved_order_ids,
            "rejected_candidates": entity.rejected_candidates,
            "confidence": entity.confidence,
        },
        "customer_context": {
            "customer_unique_id": entity.customer_unique_id,
            "related_order_ids": entity.resolved_order_ids,
        },
        "shipment_analysis": {
            "verdict": verdict_shipment,
            "late_seller_ids": shipment_payload.get("late_seller_ids", []),
            "timeline_complete": shipment_payload.get("timeline_complete", True),
        },
        "payment_analysis": {
            "verdict": verdict_payment,
            "captured_total_brl": payment_payload.get("captured_total_brl", 150.0),
            "refunded_total_brl": payment_payload.get("refunded_total_brl", 0.0),
            "refundable_total_brl": payment_payload.get("refundable_total_brl", 150.0),
        },
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": "LOGISTICS_TRANSIT_DELAY", "rank": 1}],
            "responsible_parties": [
                {"party_type": "logistics_provider", "party_id": "carrier_mock_01"}
            ],
        },
        "evidence_refs": all_evidence[:30],
        "data_conflicts": [],
        "financial_resolution": payment_payload.get(
            "financial_resolution",
            {
                "currency": "BRL",
                "recommended_refund_brl": 150.0,
                "refund_lines": [
                    {
                        "reason_code": "late_delivery_refund",
                        "amount_brl": 150.0,
                        "entity_id": entity.resolved_order_ids[0]
                        if entity.resolved_order_ids
                        else None,
                    }
                ],
            },
        ),
        "resolution_actions": [
            "issue_customer_refund",
            "notify_carrier_delay",
        ],
    }
    return output


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    async def _demo() -> None:
        print("=== Running Coordinator & Specialists Mock Demo ===")
        mock_entity = create_mock_resolved_entity(
            case_id="L3B_CASE_DEMO_001",
            complaint_type="late_delivery_logistics",
        )
        coord = Coordinator(concurrency_limit=5, max_rounds=2)
        print(f"Routing specialists for case: {mock_entity.case_id}...")
        results = await coord.coordinate(mock_entity)
        print(f"\nCompleted! Received responses from {len(results)} specialists:")
        for name, res in results.items():
            model = res.payload.get("model")
            ev_refs = res.payload.get("evidence_refs")
            print(f"  - {name} (model={model}): evidence={ev_refs}")
        print("=== Demo Complete ===")

    asyncio.run(_demo())
