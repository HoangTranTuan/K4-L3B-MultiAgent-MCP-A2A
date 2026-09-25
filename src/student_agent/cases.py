from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import VARIANT_ID

CASE_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,63}$")


@dataclass(frozen=True)
class CaseSet:
    version: str
    variant_id: str
    case_ids: tuple[str, ...]
    cases: dict[str, dict[str, Any]]


@dataclass
class ResolvedEntity:
    """Resolved entity representation passed from Entity Resolver to Coordinator."""

    case_id: str
    customer_unique_id: str | None = None
    resolved_order_ids: list[str] = field(default_factory=list)
    item_ids: list[str] = field(default_factory=list)
    seller_ids: list[str] = field(default_factory=list)
    shipment_ids: list[str] = field(default_factory=list)
    payment_references: list[str] = field(default_factory=list)
    status: str = "resolved"  # "resolved" | "ambiguous" | "not_found"
    confidence: float = 1.0
    rejected_candidates: list[str] = field(default_factory=list)
    complaint_type: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "customer_unique_id": self.customer_unique_id,
            "resolved_order_ids": list(self.resolved_order_ids),
            "item_ids": list(self.item_ids),
            "seller_ids": list(self.seller_ids),
            "shipment_ids": list(self.shipment_ids),
            "payment_references": list(self.payment_references),
            "status": self.status,
            "confidence": self.confidence,
            "rejected_candidates": list(self.rejected_candidates),
            "complaint_type": self.complaint_type,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResolvedEntity:
        return cls(
            case_id=data["case_id"],
            customer_unique_id=data.get("customer_unique_id"),
            resolved_order_ids=list(data.get("resolved_order_ids", [])),
            item_ids=list(data.get("item_ids", [])),
            seller_ids=list(data.get("seller_ids", [])),
            shipment_ids=list(data.get("shipment_ids", [])),
            payment_references=list(data.get("payment_references", [])),
            status=data.get("status", "resolved"),
            confidence=float(data.get("confidence", 1.0)),
            rejected_candidates=list(data.get("rejected_candidates", [])),
            complaint_type=data.get("complaint_type"),
            description=data.get("description"),
        )


def create_mock_resolved_entity(
    case_id: str = "CASE_MOCK_001",
    complaint_type: str = "late_delivery",
    customer_unique_id: str = "cust_mock_12345",
    order_id: str = "order_mock_98765",
    confidence: float = 0.95,
) -> ResolvedEntity:
    """Generate a mock ResolvedEntity for testing Coordinator and Specialists."""
    return ResolvedEntity(
        case_id=case_id,
        customer_unique_id=customer_unique_id,
        resolved_order_ids=[order_id],
        item_ids=[f"item_{order_id}_01"],
        seller_ids=["seller_mock_001"],
        shipment_ids=[f"ship_{order_id}_01"],
        payment_references=[f"pay_{order_id}_01"],
        status="resolved",
        confidence=confidence,
        rejected_candidates=["order_mock_99999"],
        complaint_type=complaint_type,
        description=f"Mock customer complaint regarding {complaint_type} for order {order_id}",
    )


def create_mock_case(
    case_id: str = "CASE_MOCK_001",
    complaint_type: str = "late_delivery",
    description: str = "Customer reports item arrived late with logistics delay",
) -> dict[str, Any]:
    """Generate a mock case dictionary matching the Day09 inputs structure."""
    return {
        "case_id": case_id,
        "complaint_type": complaint_type,
        "description": description,
        "customer_hint": {"name": "Test Customer", "email": "customer@example.com"},
        "date_hint": "2024-01-15",
    }


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: invalid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def load_case_set(root: Path, expected_count: int = 100) -> CaseSet:
    root = root.resolve()
    manifest = _object(root / "case-set.json")
    if set(manifest) != {"case_set_version", "variant_id", "case_ids"}:
        raise ValueError("case-set.json has unexpected or missing fields")
    if manifest["variant_id"] != VARIANT_ID:
        raise ValueError(f"expected variant {VARIANT_ID}, got {manifest['variant_id']!r}")
    raw_ids = manifest["case_ids"]
    if not isinstance(raw_ids, list) or not all(isinstance(item, str) for item in raw_ids):
        raise ValueError("case_ids must be an array of strings")
    if len(raw_ids) != expected_count or len(set(raw_ids)) != expected_count:
        raise ValueError(f"case-set must contain exactly {expected_count} unique case IDs")
    if any(not CASE_ID_PATTERN.fullmatch(case_id) for case_id in raw_ids):
        raise ValueError("case-set contains an invalid case ID")
    version = manifest["case_set_version"]
    if not isinstance(version, str) or not version:
        raise ValueError("case_set_version must be a non-empty string")

    input_root = root / "inputs"
    actual_files = {path.stem: path for path in input_root.glob("*.json") if path.is_file()}
    if set(actual_files) != set(raw_ids):
        missing = sorted(set(raw_ids) - set(actual_files))
        extra = sorted(set(actual_files) - set(raw_ids))
        raise ValueError(f"inputs do not match case-set; missing={missing}, extra={extra}")
    cases = {case_id: _object(actual_files[case_id]) for case_id in raw_ids}
    for case_id, case in cases.items():
        if case.get("case_id") != case_id:
            raise ValueError(f"inputs/{case_id}.json has a mismatched case_id")
    return CaseSet(version, VARIANT_ID, tuple(raw_ids), cases)
