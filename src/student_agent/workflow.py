from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from .mcp_gateway import EvidenceGateway, resolve_entities
from .trace import TraceWriter

# ============================================================
# GENERIC HELPERS
# ============================================================


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)

    return {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, tuple):
        return list(value)

    return [value]


def _rows(value: Any) -> list[dict[str, Any]]:
    """
    Convert common MCP response data into list[dict].
    """

    if isinstance(value, list):
        return [
            dict(item)
            for item in value
            if isinstance(item, Mapping)
        ]

    if not isinstance(value, Mapping):
        return []

    data = dict(value)

    for key in (
        "rows",
        "items",
        "orders",
        "payments",
        "events",
        "shipments",
        "sellers",
        "products",
        "refunds",
        "history",
        "results",
    ):
        nested = data.get(key)

        if isinstance(nested, list):
            return [
                dict(item)
                for item in nested
                if isinstance(item, Mapping)
            ]

    return [data]


def _first_present(
    data: Mapping[str, Any],
    *keys: str,
) -> Any:
    for key in keys:
        value = data.get(key)

        if value is not None:
            return value

    return None


def _collect_values(
    value: Any,
    keys: tuple[str, ...],
) -> list[str]:
    found: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, item in node.items():
                if (
                    key in keys
                    and item is not None
                    and not isinstance(
                        item,
                        (dict, list, tuple),
                    )
                ):
                    text = str(item).strip()

                    if text:
                        found.append(text)

                visit(item)

        elif isinstance(node, Sequence) and not isinstance(
            node,
            (str, bytes, bytearray),
        ):
            for item in node:
                visit(item)

    visit(value)

    result: list[str] = []

    for item in found:
        if item not in result:
            result.append(item)

    return result


def _to_decimal(
    value: Any,
) -> Decimal | None:
    if value is None:
        return None

    try:
        return Decimal(
            str(value)
        )

    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return None


def _sum_numeric_fields(
    value: Any,
    keys: tuple[str, ...],
) -> Decimal | None:
    values: list[Decimal] = []

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, item in node.items():
                if key in keys:
                    number = _to_decimal(
                        item
                    )

                    if number is not None:
                        values.append(number)

                visit(item)

        elif isinstance(node, Sequence) and not isinstance(
            node,
            (str, bytes, bytearray),
        ):
            for item in node:
                visit(item)

    visit(value)

    if not values:
        return None

    return sum(
        values,
        Decimal("0"),
    )


def _money(
    value: Decimal | None,
) -> float | None:
    if value is None:
        return None

    return float(
        value.quantize(
            Decimal("0.01")
        )
    )


def _evidence_ref(
    evidence: Mapping[str, Any],
) -> str:
    return str(
        evidence["evidence_ref"]
    )


def _evidence_data(
    evidence: Mapping[str, Any],
) -> Any:
    return evidence.get("data")


# ============================================================
# TRACE + MCP EVIDENCE
# ============================================================


async def _consume_tool(
    *,
    gateway: EvidenceGateway,
    trace: TraceWriter,
    case_id: str,
    actor: str,
    tool_name: str,
    **arguments: str,
) -> dict[str, Any]:
    """
    Call MCP tool and emit tool_result_consumed only when
    the returned evidence is consumed by the specialist.
    """

    evidence = await gateway.call(
        tool_name,
        case_id=case_id,
        **arguments,
    )

    evidence_ref = _evidence_ref(
        evidence
    )

    trace.emit(
        case_id=case_id,
        event_type="tool_result_consumed",
        actor=actor,
        tool_name=tool_name,
        evidence_refs=[
            evidence_ref
        ],
    )

    return evidence


def _add_ref(
    refs: list[str],
    evidence: Mapping[str, Any],
) -> None:
    ref = _evidence_ref(
        evidence
    )

    if ref not in refs:
        refs.append(ref)


# ============================================================
# ENTITY RESOLVER
# ============================================================


async def _resolve_order(
    *,
    case: dict[str, Any],
    gateway: EvidenceGateway,
    trace: TraceWriter,
    evidence_refs: list[str],
) -> tuple[
    dict[str, Any],
    str | None,
    dict[str, Any] | None,
]:
    case_id = str(
        case["case_id"]
    )

    customer_request = _as_dict(
        case.get(
            "customer_request"
        )
    )

    claimed_order_id = (
        customer_request.get(
            "claimed_order_id"
        )
    )

    candidate_order_ids = [
        str(value)
        for value in _as_list(
            case.get(
                "candidate_order_ids"
            )
        )
        if value
    ]

    if (
        claimed_order_id
        and str(claimed_order_id)
        not in candidate_order_ids
    ):
        candidate_order_ids.insert(
            0,
            str(claimed_order_id),
        )

    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="entity-agent",
        attributes={
            "candidate_count":
                len(candidate_order_ids),
        },
    )

    candidate_records: list[
        dict[str, Any]
    ] = []

    evidence_by_order: dict[
        str,
        dict[str, Any],
    ] = {}

    for order_id in candidate_order_ids:
        try:
            evidence = await _consume_tool(
                gateway=gateway,
                trace=trace,
                case_id=case_id,
                actor="entity-agent",
                tool_name="get_order",
                order_id=order_id,
            )

        except (
            RuntimeError,
            ValueError,
        ):
            continue

        _add_ref(
            evidence_refs,
            evidence,
        )

        raw_data = _evidence_data(
            evidence
        )

        rows = _rows(
            raw_data
        )

        record = (
            rows[0]
            if rows
            else _as_dict(raw_data)
        )

        if not record:
            continue

        record = dict(
            record
        )

        record.setdefault(
            "order_id",
            order_id,
        )

        candidate_records.append(
            record
        )

        evidence_by_order[
            order_id
        ] = evidence

    expected: dict[str, Any] = {
        "order_id":
            str(claimed_order_id)
            if claimed_order_id
            else None,
    }

    customer_hint = case.get(
        "customer_unique_id_hint"
    )

    if customer_hint:
        expected[
            "customer_unique_id"
        ] = str(
            customer_hint
        )

    if not candidate_records:
        return (
            {
                "status":
                    "not_found",
                "resolved_order_ids":
                    [],
                "rejected_candidates":
                    candidate_order_ids,
                "confidence":
                    0.0,
            },
            None,
            None,
        )

    resolution = resolve_entities(
        candidate_records,
        expected,
        threshold=0.60,
        ambiguity_margin=0.10,
    )

    resolved_ids = resolution[
        "resolved_order_ids"
    ]

    resolved_order_id = (
        resolved_ids[0]
        if resolved_ids
        else None
    )

    resolved_evidence = (
        evidence_by_order.get(
            resolved_order_id
        )
        if resolved_order_id
        else None
    )

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="entity-agent",
        target="coordinator",
        decision_code=(
            "ENTITY_RESOLVED"
            if resolved_order_id
            else "ENTITY_UNRESOLVED"
        ),
        attributes={
            "confidence":
                float(
                    resolution[
                        "confidence"
                    ]
                ),
        },
    )

    return (
        resolution,
        resolved_order_id,
        resolved_evidence,
    )


# ============================================================
# CUSTOMER SPECIALIST
# ============================================================


async def _customer_specialist(
    *,
    case: dict[str, Any],
    gateway: EvidenceGateway,
    trace: TraceWriter,
    evidence_refs: list[str],
) -> dict[str, Any]:
    case_id = str(
        case["case_id"]
    )

    customer_id = case.get(
        "customer_unique_id_hint"
    )

    if not customer_id:
        return {
            "customer_unique_id":
                None,
            "related_order_ids":
                [],
            "evidence":
                None,
        }

    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="customer-specialist",
    )

    try:
        evidence = await _consume_tool(
            gateway=gateway,
            trace=trace,
            case_id=case_id,
            actor="customer-specialist",
            tool_name="get_customer_history",
            customer_unique_id=str(
                customer_id
            ),
        )

    except (
        RuntimeError,
        ValueError,
    ):
        return {
            "customer_unique_id":
                str(customer_id),
            "related_order_ids":
                [],
            "evidence":
                None,
        }

    _add_ref(
        evidence_refs,
        evidence,
    )

    related_order_ids = (
        _collect_values(
            _evidence_data(
                evidence
            ),
            (
                "order_id",
                "related_order_id",
            ),
        )
    )

    return {
        "customer_unique_id":
            str(customer_id),
        "related_order_ids":
            related_order_ids[:20],
        "evidence":
            evidence,
    }


# ============================================================
# ORDER / PRODUCT SPECIALIST
# ============================================================


async def _order_product_specialist(
    *,
    case_id: str,
    order_id: str,
    gateway: EvidenceGateway,
    trace: TraceWriter,
    evidence_refs: list[str],
) -> dict[str, Any]:
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="order-product-specialist",
    )

    result: dict[str, Any] = {
        "items":
            None,
        "products":
            None,
        "sellers":
            None,
    }

    tools = (
        (
            "get_order_items",
            "items",
        ),
        (
            "get_product_context",
            "products",
        ),
        (
            "get_sellers",
            "sellers",
        ),
    )

    for tool_name, key in tools:
        try:
            evidence = await _consume_tool(
                gateway=gateway,
                trace=trace,
                case_id=case_id,
                actor=(
                    "order-product-specialist"
                ),
                tool_name=tool_name,
                order_id=order_id,
            )

        except (
            RuntimeError,
            ValueError,
        ):
            continue

        _add_ref(
            evidence_refs,
            evidence,
        )

        result[key] = evidence

    return result


# ============================================================
# SHIPMENT SPECIALIST
# ============================================================


def _infer_shipment_verdict(
    data: Any,
) -> tuple[
    str,
    list[str],
    bool,
]:
    text = str(
        data
    ).casefold()

    seller_ids = (
        _collect_values(
            data,
            (
                "seller_id",
                "late_seller_id",
            ),
        )
    )

    if (
        "seller_delay" in text
        or "seller late" in text
        or "seller_late" in text
        or "handoff delay" in text
    ):
        return (
            "seller_delay",
            seller_ids[:20],
            True,
        )

    if (
        "lost" in text
        or "missing shipment" in text
    ):
        return (
            "lost",
            [],
            True,
        )

    if (
        "returned" in text
        or "return_to_sender" in text
    ):
        return (
            "returned",
            [],
            True,
        )

    if (
        "logistics_delay" in text
        or "carrier delay" in text
        or "delivery delay" in text
        or '"late": true' in text
        or "'late': true" in text
    ):
        return (
            "logistics_delay",
            [],
            True,
        )

    if (
        "on_time" in text
        or "delivered_on_time"
        in text
        or '"late": false'
        in text
        or "'late': false"
        in text
    ):
        return (
            "on_time",
            [],
            True,
        )

    if (
        "conflict" in text
        or "inconsistent" in text
    ):
        return (
            "conflicting",
            [],
            False,
        )

    timeline_values = (
        _collect_values(
            data,
            (
                "order_delivered_customer_date",
                "order_estimated_delivery_date",
                "delivered_at",
                "estimated_delivery_at",
                "shipped_at",
                "handoff_at",
            ),
        )
    )

    timeline_complete = (
        len(
            timeline_values
        )
        >= 2
    )

    return (
        "insufficient_evidence",
        [],
        timeline_complete,
    )


async def _shipment_specialist(
    *,
    case_id: str,
    order_id: str,
    gateway: EvidenceGateway,
    trace: TraceWriter,
    evidence_refs: list[str],
) -> dict[str, Any]:
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="shipment-specialist",
    )

    try:
        evidence = await _consume_tool(
            gateway=gateway,
            trace=trace,
            case_id=case_id,
            actor="shipment-specialist",
            tool_name="get_shipment_summary",
            order_id=order_id,
        )

    except (
        RuntimeError,
        ValueError,
    ):
        return {
            "verdict":
                "insufficient_evidence",
            "late_seller_ids":
                [],
            "timeline_complete":
                False,
            "evidence":
                None,
        }

    _add_ref(
        evidence_refs,
        evidence,
    )

    (
        verdict,
        late_sellers,
        complete,
    ) = _infer_shipment_verdict(
        _evidence_data(
            evidence
        )
    )

    return {
        "verdict":
            verdict,
        "late_seller_ids":
            late_sellers,
        "timeline_complete":
            complete,
        "evidence":
            evidence,
    }


# ============================================================
# PAYMENT / REFUND SPECIALIST
# ============================================================


def _infer_payment_analysis(
    payments: Any,
    payment_timeline: Any,
    refund_timeline: Any,
) -> dict[str, Any]:
    captured = (
        _sum_numeric_fields(
            payments,
            (
                "payment_value",
                "captured_total_brl",
                "captured_amount",
            ),
        )
    )

    refunded = (
        _sum_numeric_fields(
            refund_timeline,
            (
                "refunded_total_brl",
                "refund_amount",
            ),
        )
    )

    direct_captured = None
    direct_refunded = None

    if isinstance(
        payments,
        Mapping,
    ):
        direct_captured = (
            _to_decimal(
                _first_present(
                    payments,
                    "captured_total_brl",
                    "total_paid_brl",
                    "payment_total_brl",
                )
            )
        )

    if isinstance(
        refund_timeline,
        Mapping,
    ):
        direct_refunded = (
            _to_decimal(
                _first_present(
                    refund_timeline,
                    "refunded_total_brl",
                    "total_refunded_brl",
                )
            )
        )

    if direct_captured is not None:
        captured = direct_captured

    if direct_refunded is not None:
        refunded = direct_refunded

    refunded = (
        refunded
        if refunded is not None
        else Decimal("0")
    )

    refundable = None

    if captured is not None:
        refundable = max(
            Decimal("0"),
            captured - refunded,
        )

    text = (
        f"{payments} "
        f"{payment_timeline} "
        f"{refund_timeline}"
    ).casefold()

    if (
        "refund_failed" in text
        or "refund failed" in text
    ):
        verdict = "refund_failed"

    elif (
        "refund_pending" in text
        or "refund pending" in text
        or "pending_refund" in text
    ):
        verdict = "refund_pending"

    elif (
        refunded > 0
        and refundable == 0
    ):
        verdict = "refunded"

    elif (
        "duplicate" in text
        and "payment" in text
    ):
        verdict = "duplicate_capture"

    elif (
        "mismatch" in text
        or "capture_mismatch" in text
    ):
        verdict = "capture_mismatch"

    elif captured is not None:
        verdict = "reconciled"

    else:
        verdict = (
            "insufficient_evidence"
        )

    return {
        "verdict":
            verdict,
        "captured_total_brl":
            _money(captured),
        "refunded_total_brl":
            _money(refunded),
        "refundable_total_brl":
            _money(refundable),
    }


async def _payment_refund_specialist(
    *,
    case_id: str,
    order_id: str,
    gateway: EvidenceGateway,
    trace: TraceWriter,
    evidence_refs: list[str],
) -> dict[str, Any]:
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="payment-refund-specialist",
    )

    evidences: dict[
        str,
        dict[str, Any] | None,
    ] = {
        "payments":
            None,
        "payment_timeline":
            None,
        "refund_timeline":
            None,
    }

    tools = (
        (
            "get_order_payments",
            "payments",
        ),
        (
            "get_payment_timeline",
            "payment_timeline",
        ),
        (
            "get_refund_timeline",
            "refund_timeline",
        ),
    )

    for tool_name, key in tools:
        try:
            evidence = await _consume_tool(
                gateway=gateway,
                trace=trace,
                case_id=case_id,
                actor=(
                    "payment-refund-specialist"
                ),
                tool_name=tool_name,
                order_id=order_id,
            )

        except (
            RuntimeError,
            ValueError,
        ):
            continue

        _add_ref(
            evidence_refs,
            evidence,
        )

        evidences[key] = evidence

    payments_data = (
        _evidence_data(
            evidences["payments"]
        )
        if evidences["payments"]
        else None
    )

    payment_timeline_data = (
        _evidence_data(
            evidences[
                "payment_timeline"
            ]
        )
        if evidences[
            "payment_timeline"
        ]
        else None
    )

    refund_timeline_data = (
        _evidence_data(
            evidences[
                "refund_timeline"
            ]
        )
        if evidences[
            "refund_timeline"
        ]
        else None
    )

    analysis = (
        _infer_payment_analysis(
            payments_data,
            payment_timeline_data,
            refund_timeline_data,
        )
    )

    analysis[
        "evidences"
    ] = evidences

    return analysis


# ============================================================
# POLICY SPECIALIST
# ============================================================


async def _policy_specialist(
    *,
    case: dict[str, Any],
    gateway: EvidenceGateway,
    trace: TraceWriter,
    evidence_refs: list[str],
) -> dict[str, Any] | None:
    case_id = str(
        case["case_id"]
    )

    policy_version = case.get(
        "policy_version"
    )

    if not policy_version:
        return None

    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="policy-specialist",
    )

    try:
        evidence = await _consume_tool(
            gateway=gateway,
            trace=trace,
            case_id=case_id,
            actor="policy-specialist",
            tool_name="get_policy",
            policy_version=str(
                policy_version
            ),
        )

    except (
        RuntimeError,
        ValueError,
    ):
        return None

    _add_ref(
        evidence_refs,
        evidence,
    )

    return evidence


# ============================================================
# OUTPUT HELPERS
# ============================================================


def _build_entities(
    *,
    order_id: str | None,
    order_product: dict[str, Any],
    payment: dict[str, Any],
    shipment: dict[str, Any],
) -> dict[str, list[str]]:
    combined = {
        "order_product":
            order_product,
        "payment":
            payment,
        "shipment":
            shipment,
    }

    item_ids = _collect_values(
        combined,
        (
            "order_item_id",
            "item_id",
            "product_id",
        ),
    )

    seller_ids = _collect_values(
        combined,
        (
            "seller_id",
        ),
    )

    payment_refs = _collect_values(
        combined,
        (
            "payment_reference",
            "payment_id",
            "transaction_id",
            "charge_id",
        ),
    )

    shipment_ids = _collect_values(
        combined,
        (
            "shipment_id",
            "tracking_id",
            "tracking_code",
            "delivery_id",
        ),
    )

    return {
        "order_ids":
            [order_id]
            if order_id
            else [],
        "item_ids":
            item_ids[:20],
        "seller_ids":
            seller_ids[:20],
        "payment_references":
            payment_refs[:20],
        "shipment_ids":
            shipment_ids[:20],
    }


def _claim_topics(
    case: Mapping[str, Any],
) -> list[
    tuple[str, str]
]:
    request = _as_dict(
        case.get(
            "customer_request"
        )
    )

    result: list[
        tuple[str, str]
    ] = []

    for claim in _as_list(
        request.get(
            "claims"
        )
    ):
        if not isinstance(
            claim,
            Mapping,
        ):
            continue

        claim_id = claim.get(
            "claim_id"
        )

        topic = claim.get(
            "topic"
        )

        if claim_id and topic:
            result.append(
                (
                    str(claim_id),
                    str(topic),
                )
            )

    return result


def _derive_primary_issue(
    *,
    claim_topics: list[
        tuple[str, str]
    ],
    shipment_verdict: str,
    payment_verdict: str,
) -> str:
    topics = {
        topic
        for _, topic
        in claim_topics
    }

    if payment_verdict == (
        "duplicate_capture"
    ):
        return "duplicate_charge"

    if payment_verdict == (
        "refund_failed"
    ):
        return "refund_failed"

    if payment_verdict == (
        "refund_pending"
    ):
        return "refund_pending"

    if shipment_verdict == (
        "seller_delay"
    ):
        return "late_delivery_seller"

    if shipment_verdict == (
        "logistics_delay"
    ):
        return (
            "late_delivery_logistics"
        )

    if (
        "late_delivery_logistics"
        in topics
    ):
        if shipment_verdict == (
            "on_time"
        ):
            return "unsupported_claim"

        return "late_delivery_logistics"

    if (
        "requested_full_refund"
        in topics
        and payment_verdict
        in {
            "refund_pending",
            "refund_failed",
        }
    ):
        return payment_verdict

    if shipment_verdict in {
        "insufficient_evidence",
        "conflicting",
    }:
        return (
            "insufficient_evidence"
        )

    return "unsupported_claim"


def _claim_assessments(
    *,
    claims: list[
        tuple[str, str]
    ],
    primary_issue: str,
    shipment_verdict: str,
    payment_verdict: str,
    evidence_refs: list[str],
) -> list[dict[str, Any]]:
    result: list[
        dict[str, Any]
    ] = []

    for claim_id, topic in claims:
        verdict = (
            "insufficient_evidence"
        )

        confidence = 0.45

        if topic == (
            "late_delivery_logistics"
        ):
            if shipment_verdict == (
                "logistics_delay"
            ):
                verdict = "supported"
                confidence = 0.90

            elif shipment_verdict in {
                "seller_delay",
                "on_time",
            }:
                verdict = "unsupported"
                confidence = 0.85

        elif topic == (
            "requested_full_refund"
        ):
            if payment_verdict in {
                "refund_pending",
                "refund_failed",
            }:
                verdict = (
                    "partially_supported"
                )
                confidence = 0.75

            elif primary_issue in {
                "late_delivery_logistics",
                "late_delivery_seller",
            }:
                verdict = (
                    "partially_supported"
                )
                confidence = 0.65

        result.append(
            {
                "claim_id":
                    claim_id,
                "verdict":
                    verdict,
                "confidence":
                    confidence,
                "evidence_refs":
                    evidence_refs[:30],
            }
        )

    return result[:5]


def _root_cause(
    *,
    primary_issue: str,
    shipment: dict[str, Any],
) -> dict[str, Any]:
    cause_map = {
        "late_delivery_logistics":
            "LOGISTICS_DELAY",
        "late_delivery_seller":
            "SELLER_HANDOFF_DELAY",
        "duplicate_charge":
            "DUPLICATE_PAYMENT_CAPTURE",
        "refund_pending":
            "REFUND_PENDING",
        "refund_failed":
            "REFUND_FAILED",
        "payment_mismatch":
            "PAYMENT_MISMATCH",
        "unsupported_claim":
            "CLAIM_NOT_SUPPORTED",
        "insufficient_evidence":
            "INSUFFICIENT_EVIDENCE",
    }

    cause_code = cause_map.get(
        primary_issue,
        "UNRESOLVED_CASE",
    )

    responsible_parties: list[
        dict[str, Any]
    ]

    if primary_issue == (
        "late_delivery_seller"
    ):
        seller_ids = shipment.get(
            "late_seller_ids",
            [],
        )

        if seller_ids:
            responsible_parties = [
                {
                    "party_type":
                        "seller",
                    "party_id":
                        seller_id,
                }
                for seller_id
                in seller_ids[:5]
            ]

        else:
            responsible_parties = [
                {
                    "party_type":
                        "seller",
                    "party_id":
                        None,
                }
            ]

    elif primary_issue == (
        "late_delivery_logistics"
    ):
        responsible_parties = [
            {
                "party_type":
                    "logistics_provider",
                "party_id":
                    None,
            }
        ]

    elif primary_issue in {
        "duplicate_charge",
        "refund_pending",
        "refund_failed",
        "payment_mismatch",
    }:
        responsible_parties = [
            {
                "party_type":
                    "payment_provider",
                "party_id":
                    None,
            }
        ]

    else:
        responsible_parties = [
            {
                "party_type":
                    "unknown",
                "party_id":
                    None,
            }
        ]

    return {
        "ranked_causes": [
            {
                "cause_code":
                    cause_code,
                "rank":
                    1,
            }
        ],
        "responsible_parties":
            responsible_parties,
    }


def _financial_resolution(
    *,
    order_id: str | None,
    primary_issue: str,
    payment: dict[str, Any],
    policy_data: Any,
) -> dict[str, Any]:
    refundable = payment.get(
        "refundable_total_brl"
    )

    recommended = 0.0

    refund_lines: list[
        dict[str, Any]
    ] = []

    if (
        refundable is not None
        and primary_issue in {
            "duplicate_charge",
            "refund_pending",
            "refund_failed",
        }
    ):
        recommended = float(
            refundable
        )

    elif (
        refundable is not None
        and primary_issue in {
            "late_delivery_logistics",
            "late_delivery_seller",
        }
    ):
        policy_text = str(
            policy_data
        ).casefold()

        if (
            "full refund"
            in policy_text
            or "full_refund"
            in policy_text
            or "refund eligible"
            in policy_text
        ):
            recommended = float(
                refundable
            )

    if recommended > 0:
        refund_lines.append(
            {
                "reason_code":
                    primary_issue.upper(),
                "amount_brl":
                    recommended,
                "entity_id":
                    order_id,
            }
        )

    return {
        "currency":
            "BRL",
        "recommended_refund_brl":
            recommended,
        "refund_lines":
            refund_lines,
    }


def _resolution_actions(
    *,
    primary_issue: str,
    financial: dict[str, Any],
) -> list[str]:
    actions: list[str] = []

    if (
        financial[
            "recommended_refund_brl"
        ]
        > 0
    ):
        actions.append(
            "issue_recommended_refund"
        )

    if primary_issue == (
        "late_delivery_logistics"
    ):
        actions.append(
            "review_logistics_delay"
        )

    elif primary_issue == (
        "late_delivery_seller"
    ):
        actions.append(
            "review_seller_handoff_delay"
        )

    elif primary_issue == (
        "refund_pending"
    ):
        actions.append(
            "follow_up_pending_refund"
        )

    elif primary_issue == (
        "refund_failed"
    ):
        actions.append(
            "retry_or_escalate_refund"
        )

    elif primary_issue == (
        "duplicate_charge"
    ):
        actions.append(
            "reconcile_duplicate_charge"
        )

    elif primary_issue == (
        "insufficient_evidence"
    ):
        actions.append(
            "request_additional_investigation"
        )

    if not actions:
        actions.append(
            "close_without_financial_action"
        )

    return list(
        dict.fromkeys(
            actions
        )
    )[:8]


# ============================================================
# MAIN MULTI-AGENT WORKFLOW
# ============================================================


async def solve_case(
    case: dict[str, Any],
    gateway: EvidenceGateway,
    trace: TraceWriter,
) -> dict[str, Any]:
    case_id = str(
        case["case_id"]
    )

    evidence_refs: list[str] = []

    # ========================================================
    # 1. ENTITY RESOLUTION
    # ========================================================

    (
        entity_resolution,
        order_id,
        _,
    ) = await _resolve_order(
        case=case,
        gateway=gateway,
        trace=trace,
        evidence_refs=evidence_refs,
    )

    claims = _claim_topics(
        case
    )

    # ========================================================
    # ENTITY NOT FOUND
    # ========================================================

    if not order_id:
        customer_hint = case.get(
            "customer_unique_id_hint"
        )

        output = {
            "schema_version":
                "day09-l3b-output-v2",

            "case_id":
                case_id,

            "assessment": {
                "primary_issue":
                    "insufficient_evidence",
                "secondary_issues":
                    [],
                "case_status":
                    "needs_investigation",
                "confidence":
                    0.25,
            },

            "affected_entities": {
                "order_ids":
                    [],
                "item_ids":
                    [],
                "seller_ids":
                    [],
                "payment_references":
                    [],
                "shipment_ids":
                    [],
            },

            "claim_assessments": [
                {
                    "claim_id":
                        claim_id,
                    "verdict":
                        "insufficient_evidence",
                    "confidence":
                        0.25,
                    "evidence_refs":
                        evidence_refs[:30],
                }
                for claim_id, _
                in claims[:5]
            ],

            "entity_resolution":
                entity_resolution,

            "customer_context": {
                "customer_unique_id":
                    str(customer_hint)
                    if customer_hint
                    else None,
                "related_order_ids":
                    [],
            },

            "shipment_analysis": {
                "verdict":
                    "insufficient_evidence",
                "late_seller_ids":
                    [],
                "timeline_complete":
                    False,
            },

            "payment_analysis": {
                "verdict":
                    "insufficient_evidence",
                "captured_total_brl":
                    None,
                "refunded_total_brl":
                    None,
                "refundable_total_brl":
                    None,
            },

            "root_cause_analysis": {
                "ranked_causes": [
                    {
                        "cause_code":
                            "INSUFFICIENT_EVIDENCE",
                        "rank":
                            1,
                    }
                ],
                "responsible_parties": [
                    {
                        "party_type":
                            "unknown",
                        "party_id":
                            None,
                    }
                ],
            },

            "evidence_refs":
                evidence_refs[:30],

            "data_conflicts":
                [],

            "financial_resolution": {
                "currency":
                    "BRL",
                "recommended_refund_brl":
                    0.0,
                "refund_lines":
                    [],
            },

            "resolution_actions": [
                "request_additional_investigation"
            ],
        }

        trace.emit(
            case_id=case_id,
            event_type="verification_completed",
            actor="verifier",
            decision_code="ENTITY_UNRESOLVED",
        )

        return output

    # ========================================================
    # 2. HANDOFF TO SPECIALISTS
    # ========================================================

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="coordinator",
        target="specialists",
        decision_code="START_INVESTIGATION",
    )

    scope = _as_dict(
        case.get(
            "investigation_scope"
        )
    )

    # ========================================================
    # CUSTOMER SPECIALIST
    # ========================================================

    if scope.get(
        "include_customer_history",
        True,
    ):
        customer = (
            await _customer_specialist(
                case=case,
                gateway=gateway,
                trace=trace,
                evidence_refs=evidence_refs,
            )
        )

    else:
        customer = {
            "customer_unique_id":
                case.get(
                    "customer_unique_id_hint"
                ),
            "related_order_ids":
                [],
            "evidence":
                None,
        }

    # ========================================================
    # ORDER + PRODUCT SPECIALIST
    # ========================================================

    order_product = (
        await _order_product_specialist(
            case_id=case_id,
            order_id=order_id,
            gateway=gateway,
            trace=trace,
            evidence_refs=evidence_refs,
        )
    )

    # ========================================================
    # SHIPMENT SPECIALIST
    # ========================================================

    shipment = (
        await _shipment_specialist(
            case_id=case_id,
            order_id=order_id,
            gateway=gateway,
            trace=trace,
            evidence_refs=evidence_refs,
        )
    )

    # ========================================================
    # PAYMENT SPECIALIST
    # ========================================================

    payment = (
        await _payment_refund_specialist(
            case_id=case_id,
            order_id=order_id,
            gateway=gateway,
            trace=trace,
            evidence_refs=evidence_refs,
        )
    )

    # ========================================================
    # POLICY SPECIALIST
    # ========================================================

    policy = (
        await _policy_specialist(
            case=case,
            gateway=gateway,
            trace=trace,
            evidence_refs=evidence_refs,
        )
    )

    # ========================================================
    # 3. CONFLICT RESOLUTION
    # ========================================================

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="specialists",
        target="conflict-resolver",
        decision_code="EVIDENCE_COLLECTED",
    )

    data_conflicts: list[
        dict[str, Any]
    ] = []

    customer_request = _as_dict(
        case.get(
            "customer_request"
        )
    )

    claimed_order_id = (
        customer_request.get(
            "claimed_order_id"
        )
    )

    if (
        claimed_order_id
        and str(
            claimed_order_id
        )
        != order_id
    ):
        data_conflicts.append(
            {
                "field":
                    "order_id",

                "sources": [
                    "customer_request",
                    "mcp_get_order",
                ],

                "selected_source":
                    "mcp_get_order",

                "resolution_code":
                    "AUTHORITATIVE_ORDER_SELECTED",
            }
        )

    # ========================================================
    # 4. DETERMINE PRIMARY ISSUE
    # ========================================================

    primary_issue = (
        _derive_primary_issue(
            claim_topics=claims,
            shipment_verdict=(
                shipment[
                    "verdict"
                ]
            ),
            payment_verdict=(
                payment[
                    "verdict"
                ]
            ),
        )
    )

    if primary_issue == (
        "insufficient_evidence"
    ):
        case_status = (
            "needs_investigation"
        )

        confidence = 0.45

    elif primary_issue == (
        "unsupported_claim"
    ):
        case_status = "no_action"

        confidence = 0.75

    else:
        case_status = (
            "action_required"
        )

        confidence = 0.82

    secondary_issues: list[str] = []

    for _, topic in claims:
        if (
            topic != primary_issue
            and topic
            not in secondary_issues
        ):
            secondary_issues.append(
                topic
            )

    # ========================================================
    # 5. AFFECTED ENTITIES
    # ========================================================

    affected_entities = (
        _build_entities(
            order_id=order_id,
            order_product=order_product,
            payment=payment,
            shipment=shipment,
        )
    )

    # ========================================================
    # 6. FINANCIAL RESOLUTION
    # ========================================================

    policy_data = (
        _evidence_data(
            policy
        )
        if policy
        else None
    )

    financial = (
        _financial_resolution(
            order_id=order_id,
            primary_issue=primary_issue,
            payment=payment,
            policy_data=policy_data,
        )
    )

    # ========================================================
    # 7. BUILD FINAL OUTPUT
    # ========================================================

    output = {
        "schema_version":
            "day09-l3b-output-v2",

        "case_id":
            case_id,

        "assessment": {
            "primary_issue":
                primary_issue,

            "secondary_issues":
                secondary_issues[:10],

            "case_status":
                case_status,

            "confidence":
                confidence,
        },

        "affected_entities":
            affected_entities,

        "claim_assessments":
            _claim_assessments(
                claims=claims,
                primary_issue=primary_issue,
                shipment_verdict=(
                    shipment[
                        "verdict"
                    ]
                ),
                payment_verdict=(
                    payment[
                        "verdict"
                    ]
                ),
                evidence_refs=evidence_refs,
            ),

        "entity_resolution":
            entity_resolution,

        "customer_context": {
            "customer_unique_id":
                customer[
                    "customer_unique_id"
                ],

            "related_order_ids":
                customer[
                    "related_order_ids"
                ][:20],
        },

        "shipment_analysis": {
            "verdict":
                shipment[
                    "verdict"
                ],

            "late_seller_ids":
                shipment[
                    "late_seller_ids"
                ][:20],

            "timeline_complete":
                bool(
                    shipment[
                        "timeline_complete"
                    ]
                ),
        },

        "payment_analysis": {
            "verdict":
                payment[
                    "verdict"
                ],

            "captured_total_brl":
                payment[
                    "captured_total_brl"
                ],

            "refunded_total_brl":
                payment[
                    "refunded_total_brl"
                ],

            "refundable_total_brl":
                payment[
                    "refundable_total_brl"
                ],
        },

        "root_cause_analysis":
            _root_cause(
                primary_issue=primary_issue,
                shipment=shipment,
            ),

        "evidence_refs":
            evidence_refs[:30],

        "data_conflicts":
            data_conflicts[:5],

        "financial_resolution":
            financial,

        "resolution_actions":
            _resolution_actions(
                primary_issue=primary_issue,
                financial=financial,
            ),
    }

    # ========================================================
    # 8. VERIFIER
    # ========================================================

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="conflict-resolver",
        target="verifier",
        decision_code="READY_FOR_VERIFICATION",
    )

    if (
        output[
            "financial_resolution"
        ][
            "recommended_refund_brl"
        ]
        > 0
        and output[
            "assessment"
        ][
            "case_status"
        ]
        == "no_action"
    ):
        raise ValueError(
            "Financial action conflicts "
            "with case_status=no_action"
        )

    if not output[
        "evidence_refs"
    ]:
        raise ValueError(
            "No MCP evidence consumed "
            f"for {case_id}"
        )

    trace.emit(
        case_id=case_id,
        event_type="verification_completed",
        actor="verifier",
        decision_code="CASE_VERIFIED",
        attributes={
            "evidence_count":
                len(
                    evidence_refs
                ),
            "conflict_count":
                len(
                    data_conflicts
                ),
        },
    )

    return output