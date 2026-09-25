"""Output Builder and Assembler for Day09 L3B Multi-Agent investigation.

Constructs valid, canonical L3B output dictionaries adhering to
contracts/schemas/l3b-output-v2.schema.json, integrates with the Verifier,
and manages atomic file generation in outputs/<case_id>.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import OUTPUT_SCHEMA_VERSION
from .verifier import VerificationReport, Verifier


def build_l3b_output(
    *,
    case_id: str,
    assessment: dict[str, Any],
    affected_entities: dict[str, Any],
    entity_resolution: dict[str, Any],
    customer_context: dict[str, Any],
    shipment_analysis: dict[str, Any],
    payment_analysis: dict[str, Any],
    root_cause_analysis: dict[str, Any],
    financial_resolution: dict[str, Any],
    resolution_actions: list[str],
    evidence_refs: list[str],
    data_conflicts: list[dict[str, Any]] | None = None,
    claim_assessments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble a canonical L3B case output dictionary."""
    # Deduplicate evidence_refs while preserving ordering
    seen_refs: set[str] = set()
    unique_refs: list[str] = []
    for r in evidence_refs:
        if r and r not in seen_refs:
            seen_refs.add(r)
            unique_refs.append(r)

    # Deduplicate actions while preserving ordering (max 8)
    seen_actions: set[str] = set()
    unique_actions: list[str] = []
    for a in resolution_actions:
        if a and a not in seen_actions:
            seen_actions.add(a)
            unique_actions.append(a[:80])
        if len(unique_actions) >= 8:
            break

    output: dict[str, Any] = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "case_id": case_id,
        "assessment": {
            "primary_issue": assessment["primary_issue"],
            "secondary_issues": list(dict.fromkeys(assessment.get("secondary_issues", [])))[:10],
            "case_status": assessment["case_status"],
            "confidence": float(assessment["confidence"]),
        },
        "affected_entities": {
            "order_ids": list(dict.fromkeys(affected_entities.get("order_ids", [])))[:20],
            "item_ids": list(dict.fromkeys(affected_entities.get("item_ids", [])))[:20],
            "seller_ids": list(dict.fromkeys(affected_entities.get("seller_ids", [])))[:20],
            "payment_references": list(
                dict.fromkeys(affected_entities.get("payment_references", []))
            )[:20],
            "shipment_ids": list(dict.fromkeys(affected_entities.get("shipment_ids", [])))[:20],
        },
        "entity_resolution": {
            "status": entity_resolution["status"],
            "resolved_order_ids": list(
                dict.fromkeys(entity_resolution.get("resolved_order_ids", []))
            )[:20],
            "rejected_candidates": list(
                dict.fromkeys(entity_resolution.get("rejected_candidates", []))
            )[:20],
            "confidence": float(entity_resolution.get("confidence", 1.0)),
        },
        "customer_context": {
            "customer_unique_id": customer_context.get("customer_unique_id"),
            "related_order_ids": list(
                dict.fromkeys(customer_context.get("related_order_ids", []))
            )[:20],
        },
        "shipment_analysis": {
            "verdict": shipment_analysis["verdict"],
            "late_seller_ids": list(
                dict.fromkeys(shipment_analysis.get("late_seller_ids", []))
            )[:20],
            "timeline_complete": bool(shipment_analysis.get("timeline_complete", True)),
        },
        "payment_analysis": {
            "verdict": payment_analysis["verdict"],
            "captured_total_brl": payment_analysis.get("captured_total_brl"),
            "refunded_total_brl": payment_analysis.get("refunded_total_brl"),
            "refundable_total_brl": payment_analysis.get("refundable_total_brl"),
        },
        "root_cause_analysis": {
            "ranked_causes": root_cause_analysis.get("ranked_causes", [])[:5],
            "responsible_parties": root_cause_analysis.get("responsible_parties", [])[:5],
        },
        "evidence_refs": unique_refs[:30],
        "data_conflicts": (data_conflicts or [])[:5],
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": float(
                financial_resolution.get("recommended_refund_brl", 0.0)
            ),
            "refund_lines": financial_resolution.get("refund_lines", [])[:10],
        },
        "resolution_actions": unique_actions,
    }

    if claim_assessments:
        output["claim_assessments"] = claim_assessments[:5]

    return output


def save_case_output(
    output: dict[str, Any],
    output_dir: Path,
    verifier: Verifier | None = None,
    case_input: dict[str, Any] | None = None,
) -> tuple[Path, VerificationReport | None]:
    """Verify (optional) and atomically write case output JSON."""
    report = None
    if verifier is not None:
        report = verifier.verify(output, case_input=case_input, raise_on_error=True)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    case_id = output["case_id"]
    target_path = output_dir / f"{case_id}.json"
    temp_path = target_path.with_suffix(".json.tmp")

    payload = json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    temp_path.write_text(payload, encoding="utf-8")
    temp_path.replace(target_path)
    return target_path, report
