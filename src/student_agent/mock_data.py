"""Mock data generator for Day09 L3B Multi-Agent system.

Provides realistic mock inputs, specialist claims, and golden / invalid outputs
to facilitate isolated development and testing for Hai (Conflict Resolver & Verifier)
prior to live LLM / MCP Gateway integration.
"""

from __future__ import annotations

import copy
from typing import Any

from . import OUTPUT_SCHEMA_VERSION

MOCK_CASE_INPUT: dict[str, Any] = {
    "case_id": "L3B_CASE_001",
    "opened_at": "2018-01-01T09:00:00-03:00",
    "customer_request": {
        "language": "vi",
        "message": (
            "Điều tra đa nguồn: resolve đúng order, kiểm tra customer history, "
            "shipment, payment và policy trước khi kết luận."
        ),
        "claimed_order_id": "af0bbb47f125381ce9f3597dc70ef07b",
        "claims": [
            {
                "claim_id": "claim-001-a",
                "topic": "late_delivery_logistics",
            },
            {
                "claim_id": "claim-001-b",
                "topic": "requested_full_refund",
            },
        ],
    },
    "policy_version": "EC_POLICY_V2",
    "candidate_order_ids": [
        "af0bbb47f125381ce9f3597dc70ef07b",
        "candidate-001-alt",
    ],
    "investigation_scope": {
        "include_customer_history": True,
        "include_product_context": True,
        "require_independent_verification": True,
    },
    "customer_unique_id_hint": "customer-597dc70ef07b",
}

MOCK_GOLDEN_OUTPUT: dict[str, Any] = {
    "schema_version": OUTPUT_SCHEMA_VERSION,
    "case_id": "L3B_CASE_001",
    "assessment": {
        "primary_issue": "late_delivery_logistics",
        "secondary_issues": ["refund_pending"],
        "case_status": "action_required",
        "confidence": 0.92,
    },
    "affected_entities": {
        "order_ids": ["af0bbb47f125381ce9f3597dc70ef07b"],
        "item_ids": ["item_sku_xyz_001"],
        "seller_ids": ["seller_id_brazil_001"],
        "payment_references": ["pay_ref_9876543210abcdef01"],
        "shipment_ids": ["ship_597dc70ef07b_01"],
    },
    "claim_assessments": [
        {
            "claim_id": "claim-001-a",
            "verdict": "supported",
            "confidence": 0.95,
            "evidence_refs": ["ev_shipment_tracking_log_1234567890abcdef"],
        },
        {
            "claim_id": "claim-001-b",
            "verdict": "partially_supported",
            "confidence": 0.90,
            "evidence_refs": ["ev_payment_gateway_record_1234567890abcdef"],
        },
    ],
    "entity_resolution": {
        "status": "resolved",
        "resolved_order_ids": ["af0bbb47f125381ce9f3597dc70ef07b"],
        "rejected_candidates": ["candidate-001-alt"],
        "confidence": 0.95,
    },
    "customer_context": {
        "customer_unique_id": "customer-597dc70ef07b",
        "related_order_ids": ["af0bbb47f125381ce9f3597dc70ef07b"],
    },
    "shipment_analysis": {
        "verdict": "logistics_delay",
        "late_seller_ids": [],
        "timeline_complete": True,
    },
    "payment_analysis": {
        "verdict": "reconciled",
        "captured_total_brl": 150.50,
        "refunded_total_brl": 0.0,
        "refundable_total_brl": 150.50,
    },
    "root_cause_analysis": {
        "ranked_causes": [
            {
                "cause_code": "CARRIER_TRANSIT_DELAY",
                "rank": 1,
            }
        ],
        "responsible_parties": [
            {
                "party_type": "logistics_provider",
                "party_id": "carrier_correios_br",
            }
        ],
    },
    "evidence_refs": [
        "ev_shipment_tracking_log_1234567890abcdef",
        "ev_payment_gateway_record_1234567890abcdef",
        "ev_order_items_lookup_1234567890abcdef",
    ],
    "data_conflicts": [
        {
            "field": "refund_amount",
            "sources": ["customer_claimed_refund", "payment_gateway_record"],
            "selected_source": "payment_gateway_record",
            "resolution_code": "precedence_payment_gateway_over_customer",
        }
    ],
    "financial_resolution": {
        "currency": "BRL",
        "recommended_refund_brl": 150.50,
        "refund_lines": [
            {
                "reason_code": "LOGISTICS_DELAY_FULL_REFUND",
                "amount_brl": 150.50,
                "entity_id": "af0bbb47f125381ce9f3597dc70ef07b",
            }
        ],
    },
    "resolution_actions": [
        "issue_refund_customer",
        "notify_carrier_service_breach",
    ],
}


def get_mock_invalid_case(invariant_id: int) -> dict[str, Any]:
    """Generate an output dictionary intentionally violating a specific invariant (1-10)."""
    case = copy.deepcopy(MOCK_GOLDEN_OUTPUT)

    if invariant_id == 1:
        # Schema violation: missing required field 'financial_resolution'
        del case["financial_resolution"]
    elif invariant_id == 2:
        # Entity scope violation: order_ids has an unknown, un-resolved order ID
        case["affected_entities"]["order_ids"] = ["foreign_order_outside_scope_999"]
    elif invariant_id == 3:
        # Rejected candidates violation: omitted rejected candidate from list
        case["entity_resolution"]["rejected_candidates"] = []
    elif invariant_id == 4:
        # Evidence ownership / regex violation: invalid format (missing ev_ prefix / too short)
        case["evidence_refs"] = ["bad_evidence_ref_format_123"]
    elif invariant_id == 5:
        # Claim linkage violation: claim has empty evidence_refs
        case["claim_assessments"][0]["evidence_refs"] = []
    elif invariant_id == 6:
        # Timeline violation: delivered before purchased
        case["shipment_analysis"]["order_purchase_timestamp"] = "2024-05-10T10:00:00Z"
        case["shipment_analysis"]["order_delivered_customer_date"] = "2024-05-01T10:00:00Z"
    elif invariant_id == 7:
        # Payment totals violation: recommended refund exceeds captured total
        case["financial_resolution"]["recommended_refund_brl"] = 9999.0
        case["financial_resolution"]["refund_lines"][0]["amount_brl"] = 9999.0
    elif invariant_id == 8:
        # Source precedence violation: customer statement erroneously wins over tracking log
        case["data_conflicts"] = [
            {
                "field": "delivery_status",
                "sources": ["shipment_tracking_log", "customer_statement"],
                "selected_source": "customer_statement",
                "resolution_code": "override_tracking",
            }
        ]
    elif invariant_id == 9:
        # Responsibility & Action consistency violation: duplicate actions
        case["resolution_actions"] = ["issue_refund_customer", "issue_refund_customer"]
    elif invariant_id == 10:
        # Confidence bounds violation: unresolved conflict exists but confidence reported as 0.95
        case["data_conflicts"] = [
            {
                "field": "disputed_payment",
                "sources": ["source_a", "source_b"],
                "selected_source": None,
                "resolution_code": "unresolved_conflict",
            }
        ]
        case["assessment"]["confidence"] = 0.95

    return case
