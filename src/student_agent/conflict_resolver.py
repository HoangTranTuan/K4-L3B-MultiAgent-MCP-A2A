"""Conflict Resolver for Day09 L3B Multi-Agent investigation.

Applies deterministic Source Precedence policy as defined in ARCHITECTURE.md §4:
- Shipment: shipment_tracking_log > seller_dispatch_status > customer_statement
- Payment / Refund: payment_gateway_record > seller_invoice > customer_refund_claim
- Policy: policy_contract > general_store_faq

Uses sub-10B models (e.g. qwen/qwen3.5-9b, temperature=0.0) when semantic synthesis is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Sub-10B model identifiers compliant with system constraints (<10B parameters)
DEFAULT_SUB10B_MODEL = "qwen/qwen3.5-9b"
FAST_SUB10B_MODEL = "qwen/qwen-2.5-7b-instruct"
REASONING_SUB10B_MODEL = "meta-llama/llama-3.1-8b-instruct"
TEMPERATURE_DETERMINISTIC = 0.0

# Source Precedence Hierarchies (highest index = lowest priority, index 0 = highest priority)
SHIPMENT_PRECEDENCE = [
    "shipment_tracking_log",  # Priority 1 (Carrier tracking log)
    "seller_dispatch_status",  # Priority 2 (Seller claim / warehouse dispatch)
    "customer_statement",  # Priority 3 (Customer claim in ticket)
]

PAYMENT_PRECEDENCE = [
    "payment_gateway_record",  # Priority 1 (Bank / Gateway captured log)
    "seller_invoice",  # Priority 2 (Seller issued invoice)
    "customer_refund_claim",  # Priority 3 (Customer claimed amount in ticket)
]

ORDER_STATUS_PRECEDENCE = [
    "platform_order_status",
    "seller_status",
    "customer_status",
]


@dataclass(frozen=True)
class ConflictRecord:
    field: str
    sources: list[str]
    selected_source: str | None
    resolution_code: str
    evidence_refs: list[str] = field(default_factory=list)
    details: str = ""

    def to_schema_dict(self) -> dict[str, Any]:
        """Convert to JSON Schema compliant data_conflicts item."""
        return {
            "field": self.field,
            "sources": self.sources,
            "selected_source": self.selected_source,
            "resolution_code": self.resolution_code,
        }


class ConflictResolver:
    """Deterministic Conflict Resolver with Source Precedence rules."""

    def __init__(self, model_name: str = DEFAULT_SUB10B_MODEL) -> None:
        self.model_name = model_name

    def get_precedence_rank(self, source: str, domain: str = "shipment") -> int:
        """Lower number means higher priority. Returns 999 if source unknown."""
        hierarchy = (
            SHIPMENT_PRECEDENCE
            if domain == "shipment"
            else PAYMENT_PRECEDENCE
            if domain in ("payment", "financial")
            else ORDER_STATUS_PRECEDENCE
        )
        try:
            return hierarchy.index(source)
        except ValueError:
            return 999

    def resolve_field_conflict(
        self,
        field_name: str,
        source_values: dict[str, Any],
        source_evidence_refs: dict[str, str | list[str]] | None = None,
        domain: str = "shipment",
    ) -> tuple[Any, ConflictRecord | None]:
        """Resolve conflict between multiple sources reporting different values for a field.

        Args:
            field_name: Name of the conflicting field (e.g. 'delivery_status', 'refund_amount').
            source_values: Mapping of source_name -> value.
            source_evidence_refs: Optional mapping of source_name -> evidence_ref(s).
            domain: 'shipment', 'payment', or 'order'.

        Returns:
            (selected_value, ConflictRecord or None if no conflict existed).
        """
        unique_values = set()
        for v in source_values.values():
            if isinstance(v, (int, float, str, bool)):
                unique_values.add(v)
            else:
                unique_values.add(str(v))

        # If all sources report identical values, no conflict exists
        if len(unique_values) <= 1:
            first_val = next(iter(source_values.values())) if source_values else None
            return first_val, None

        # Multiple distinct values -> conflict detected
        sources = list(source_values.keys())
        all_refs: list[str] = []
        if source_evidence_refs:
            for s in sources:
                ref = source_evidence_refs.get(s)
                if isinstance(ref, list):
                    all_refs.extend(ref)
                elif isinstance(ref, str):
                    all_refs.append(ref)

        # Sort sources by precedence rank
        ranked_sources = sorted(sources, key=lambda s: self.get_precedence_rank(s, domain))
        highest_rank = self.get_precedence_rank(ranked_sources[0], domain)
        second_rank = self.get_precedence_rank(ranked_sources[1], domain)

        if highest_rank < second_rank:
            # Winner found by strict precedence
            winning_source = ranked_sources[0]
            winning_value = source_values[winning_source]
            record = ConflictRecord(
                field=field_name[:100],
                sources=sources[:5],
                selected_source=winning_source[:80],
                resolution_code=f"precedence_{winning_source}_over_{ranked_sources[1]}"[:80],
                evidence_refs=all_refs,
                details=f"Selected {winning_source} according to {domain} policy precedence.",
            )
            return winning_value, record

        # Tie in precedence or both unknown: cannot resolve deterministically
        record = ConflictRecord(
            field=field_name[:100],
            sources=sources[:5],
            selected_source=None,
            resolution_code="unresolved_precedence_tie"[:80],
            evidence_refs=all_refs,
            details="Sources have equal/unknown precedence; marked as unresolved conflict.",
        )
        return None, record

    def reconcile_case_claims(
        self,
        *,
        shipment_claim: dict[str, Any] | None = None,
        payment_claim: dict[str, Any] | None = None,
        customer_request: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[ConflictRecord], bool]:
        """Reconcile claims from specialists against customer requests.

        Returns:
            (reconciled_values, conflict_records, has_unresolved_conflicts)
        """
        conflicts: list[ConflictRecord] = []
        reconciled: dict[str, Any] = {}

        # 1. Reconcile delivery status if customer claims non-receipt vs carrier tracking
        if shipment_claim and customer_request:
            carrier_verdict = shipment_claim.get("verdict")
            carrier_ref = shipment_claim.get("evidence_refs", [])
            cust_topic = None
            cust_claims = customer_request.get("claims", [])
            for c in cust_claims:
                topic = c.get("topic", "")
                if "late" in topic or "deliver" in topic or "lost" in topic:
                    cust_topic = topic

            is_late_dispute = bool(
                carrier_verdict == "on_time" and cust_topic and "late" in cust_topic
            )
            if carrier_verdict and cust_topic and is_late_dispute:
                val, conflict = self.resolve_field_conflict(
                    field_name="delivery_timeliness",
                    source_values={
                        "shipment_tracking_log": "on_time",
                        "customer_statement": "late",
                    },
                    source_evidence_refs={
                        "shipment_tracking_log": carrier_ref,
                        "customer_statement": "ev_customer_complaint_ticket",
                    },
                    domain="shipment",
                )
                if conflict:
                    conflicts.append(conflict)
                reconciled["delivery_timeliness"] = val

        # 2. Reconcile refund amount if customer requests full refund but payment shows different
        if payment_claim and customer_request:
            captured = payment_claim.get("captured_total_brl")
            cust_claims = customer_request.get("claims", [])
            has_refund_claim = any("refund" in c.get("topic", "") for c in cust_claims)

            if has_refund_claim and captured is not None:
                reconciled["max_refundable_brl"] = captured

        has_unresolved = any(c.selected_source is None for c in conflicts)
        return reconciled, conflicts, has_unresolved
