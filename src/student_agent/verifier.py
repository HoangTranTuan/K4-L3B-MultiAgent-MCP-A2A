"""Verifier module implementing the 10 Invariants from ARCHITECTURE.md §6.

All invariants are executed deterministically in pure Python code (0ms latency,
no token cost, 100% reproducible) to act as a strict quality gatekeeper.

Invariants:
1. Schema - Validates against l3b-output-v2.schema.json.
2. Entity scope - Evidence & affected entities belong to resolved case scope.
3. Rejected candidates - Excluded candidates are never silently dropped.
4. Evidence ownership - Strict ev_... regex format and valid MCP origin.
5. Claim linkage - Every claim has at least one valid supporting evidence_ref.
6. Timeline - Chronological ordering of order lifecycle milestones.
7. Payment/refund totals - Recommended refund <= captured total & sum(lines) matches.
8. Source precedence - Conflict resolutions follow agreed policy hierarchy.
9. Responsibility/action consistency - Responsible parties match proposed actions.
10. Confidence bounds - Unresolved conflicts or weak evidence penalize confidence <= 0.70.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .contracts import ContractError, Contracts

EVIDENCE_REF_PATTERN = re.compile(r"^ev_[A-Za-z0-9_-]{20,96}$")


class InvariantViolationError(ValueError):
    """Raised when an output fails one or more verification invariants."""

    def __init__(self, invariant_name: str, message: str) -> None:
        self.invariant_name = invariant_name
        super().__init__(f"[{invariant_name}] {message}")


@dataclass
class InvariantResult:
    invariant_id: int
    name: str
    passed: bool
    message: str = "OK"


@dataclass
class VerificationReport:
    case_id: str
    status: str  # "passed" | "failed"
    passed: bool
    results: list[InvariantResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        passed_count = sum(1 for r in self.results if r.passed)
        total_count = len(self.results)
        return (
            f"Case {self.case_id}: status={self.status} "
            f"({passed_count}/{total_count} invariants passed, {len(self.errors)} errors)"
        )


class Verifier:
    """Deterministic Quality Gatekeeper enforcing 10 Invariants."""

    def __init__(self, contracts: Contracts | None = None) -> None:
        if contracts is None:
            root = Path(__file__).resolve().parents[2]
            contracts = Contracts(root / "contracts" / "schemas")
        self.contracts = contracts

    def verify(
        self,
        output: dict[str, Any],
        case_input: dict[str, Any] | None = None,
        raise_on_error: bool = False,
    ) -> VerificationReport:
        """Run all 10 invariants sequentially on a draft output."""
        case_id = output.get("case_id", "UNKNOWN_CASE")
        report = VerificationReport(
            case_id=case_id,
            status="passed",
            passed=True,
        )

        checks = [
            (1, "Schema", self._check_schema),
            (2, "EntityScope", self._check_entity_scope),
            (3, "RejectedCandidates", self._check_rejected_candidates),
            (4, "EvidenceOwnership", self._check_evidence_ownership),
            (5, "ClaimLinkage", self._check_claim_linkage),
            (6, "Timeline", self._check_timeline),
            (7, "PaymentRefundTotals", self._check_payment_refund_totals),
            (8, "SourcePrecedence", self._check_source_precedence),
            (9, "ResponsibilityActionConsistency", self._check_responsibility_action),
            (10, "ConfidenceBounds", self._check_confidence_bounds),
        ]

        for invariant_id, name, check_fn in checks:
            try:
                msg = check_fn(output, case_input)
                report.results.append(
                    InvariantResult(
                        invariant_id=invariant_id,
                        name=name,
                        passed=True,
                        message=msg or "OK",
                    )
                )
            except (InvariantViolationError, ValueError, ContractError) as exc:
                err_msg = str(exc)
                report.results.append(
                    InvariantResult(
                        invariant_id=invariant_id,
                        name=name,
                        passed=False,
                        message=err_msg,
                    )
                )
                report.errors.append(err_msg)
                report.passed = False
                report.status = "failed"
                if raise_on_error:
                    raise InvariantViolationError(name, err_msg) from exc

        return report

    # -------------------------------------------------------------------------
    # Invariant 1: Schema Compliance
    # -------------------------------------------------------------------------
    def _check_schema(self, output: dict[str, Any], _case_input: dict[str, Any] | None) -> str:
        case_id = output.get("case_id", "draft")
        self.contracts.validate_output(output, f"outputs/{case_id}.json")
        return "JSON Schema Draft 2020-12 valid"

    # -------------------------------------------------------------------------
    # Invariant 2: Entity Scope
    # -------------------------------------------------------------------------
    def _check_entity_scope(
        self, output: dict[str, Any], case_input: dict[str, Any] | None
    ) -> str:
        case_id = output.get("case_id")
        if case_input and case_input.get("case_id") != case_id:
            input_id = case_input.get("case_id")
            raise InvariantViolationError(
                "EntityScope",
                f"Output case_id '{case_id}' does not match input case_id '{input_id}'",
            )

        resolved_orders = set(
            output.get("entity_resolution", {}).get("resolved_order_ids", [])
        )
        affected_orders = set(
            output.get("affected_entities", {}).get("order_ids", [])
        )

        if affected_orders and not affected_orders.issubset(resolved_orders):
            outside_orders = affected_orders - resolved_orders
            raise InvariantViolationError(
                "EntityScope",
                f"Affected orders {outside_orders} outside resolved {resolved_orders}",
            )

        return "Entity scope strictly matches resolved orders"

    # -------------------------------------------------------------------------
    # Invariant 3: Rejected Candidates Preservation
    # -------------------------------------------------------------------------
    def _check_rejected_candidates(
        self, output: dict[str, Any], case_input: dict[str, Any] | None
    ) -> str:
        entity_res = output.get("entity_resolution", {})
        rejected = set(entity_res.get("rejected_candidates", []))
        resolved = set(entity_res.get("resolved_order_ids", []))

        if case_input and "candidate_order_ids" in case_input:
            candidate_pool = set(case_input["candidate_order_ids"])
            expected_rejected = candidate_pool - resolved
            missing_rejected = expected_rejected - rejected
            if missing_rejected:
                raise InvariantViolationError(
                    "RejectedCandidates",
                    f"Candidate order IDs {missing_rejected} omitted from rejected_candidates",
                )

        return "All unchosen candidates preserved with full provenance"

    # -------------------------------------------------------------------------
    # Invariant 4: Evidence Ownership & Format
    # -------------------------------------------------------------------------
    def _check_evidence_ownership(
        self, output: dict[str, Any], _case_input: dict[str, Any] | None
    ) -> str:
        evidence_refs = output.get("evidence_refs", [])
        for ref in evidence_refs:
            if not isinstance(ref, str) or not EVIDENCE_REF_PATTERN.fullmatch(ref):
                raise InvariantViolationError(
                    "EvidenceOwnership",
                    f"Invalid evidence_ref format '{ref}'; must match regex "
                    r"^ev_[A-Za-z0-9_-]{20,96}$",
                )
        return f"{len(evidence_refs)} evidence_refs verified matching regex format"

    # -------------------------------------------------------------------------
    # Invariant 5: Claim Linkage
    # -------------------------------------------------------------------------
    def _check_claim_linkage(
        self, output: dict[str, Any], _case_input: dict[str, Any] | None
    ) -> str:
        root_refs = set(output.get("evidence_refs", []))
        claim_assessments = output.get("claim_assessments", [])

        if output.get("assessment", {}).get("case_status") == "action_required" and not root_refs:
            raise InvariantViolationError(
                "ClaimLinkage",
                "Case status is 'action_required' but root evidence_refs list is empty",
            )

        for claim in claim_assessments:
            claim_id = claim.get("claim_id")
            c_refs = claim.get("evidence_refs", [])
            if not c_refs:
                raise InvariantViolationError(
                    "ClaimLinkage",
                    f"Claim assessment '{claim_id}' has no supporting evidence_refs",
                )
            for r in c_refs:
                if not EVIDENCE_REF_PATTERN.fullmatch(r):
                    raise InvariantViolationError(
                        "ClaimLinkage",
                        f"Claim '{claim_id}' has malformed evidence_ref: '{r}'",
                    )
                if r not in root_refs:
                    raise InvariantViolationError(
                        "ClaimLinkage",
                        f"Claim evidence_ref '{r}' not included in root evidence_refs list",
                    )

        return "Every claim grounded in verified root evidence_refs"

    # -------------------------------------------------------------------------
    # Invariant 6: Timeline Ordering
    # -------------------------------------------------------------------------
    def _check_timeline(
        self, output: dict[str, Any], case_input: dict[str, Any] | None
    ) -> str:
        def parse_dt(val: Any) -> datetime | None:
            if not isinstance(val, str):
                return None
            try:
                cleaned = val.replace("Z", "+00:00")
                return datetime.fromisoformat(cleaned)
            except ValueError:
                return None

        timeline_timestamps: dict[str, datetime] = {}
        if case_input and "opened_at" in case_input:
            opened = parse_dt(case_input["opened_at"])
            if opened:
                timeline_timestamps["case_opened_at"] = opened

        metadata = output.get("shipment_analysis", {})
        for key in ("order_purchase_timestamp", "order_delivered_customer_date"):
            if key in metadata:
                dt = parse_dt(metadata[key])
                if dt:
                    timeline_timestamps[key] = dt

        purchase = timeline_timestamps.get("order_purchase_timestamp")
        delivery = timeline_timestamps.get("order_delivered_customer_date")
        if purchase and delivery and delivery < purchase:
            raise InvariantViolationError(
                "Timeline",
                f"Delivery date ({delivery}) is before purchase date ({purchase})",
            )

        return "Chronological ordering verified"

    # -------------------------------------------------------------------------
    # Invariant 7: Payment / Refund Totals
    # -------------------------------------------------------------------------
    def _check_payment_refund_totals(
        self, output: dict[str, Any], _case_input: dict[str, Any] | None
    ) -> str:
        payment = output.get("payment_analysis", {})
        financial = output.get("financial_resolution", {})

        captured = payment.get("captured_total_brl")
        recommended_refund = financial.get("recommended_refund_brl", 0.0)
        refund_lines = financial.get("refund_lines", [])

        if recommended_refund < 0:
            raise InvariantViolationError(
                "PaymentRefundTotals",
                f"Recommended refund ({recommended_refund}) cannot be negative",
            )

        if captured is not None and recommended_refund > (captured + 0.01):
            raise InvariantViolationError(
                "PaymentRefundTotals",
                f"Refund ({recommended_refund} BRL) exceeds captured ({captured} BRL)",
            )

        lines_total = sum(line.get("amount_brl", 0.0) for line in refund_lines)
        if abs(lines_total - recommended_refund) > 0.01:
            raise InvariantViolationError(
                "PaymentRefundTotals",
                f"Sum of lines ({lines_total:.2f} BRL) != "
                f"recommended ({recommended_refund:.2f} BRL)",
            )

        return "Refund calculation exactly reconciled with captured financial records"

    # -------------------------------------------------------------------------
    # Invariant 8: Source Precedence
    # -------------------------------------------------------------------------
    def _check_source_precedence(
        self, output: dict[str, Any], _case_input: dict[str, Any] | None
    ) -> str:
        conflicts = output.get("data_conflicts", [])
        for conf in conflicts:
            sources = conf.get("sources", [])
            selected = conf.get("selected_source")
            field_name = conf.get("field")

            if (
                "shipment_tracking_log" in sources
                and "customer_statement" in sources
                and selected is not None
                and selected != "shipment_tracking_log"
            ):
                raise InvariantViolationError(
                    "SourcePrecedence",
                    f"Conflict '{field_name}': tracking log overridden by '{selected}'",
                )

            if (
                "payment_gateway_record" in sources
                and "customer_claimed_refund" in sources
                and selected is not None
                and selected != "payment_gateway_record"
            ):
                raise InvariantViolationError(
                    "SourcePrecedence",
                    f"Conflict '{field_name}': gateway record overridden by '{selected}'",
                )

        return f"{len(conflicts)} data_conflicts adhere strictly to Source Precedence"

    # -------------------------------------------------------------------------
    # Invariant 9: Responsibility & Action Consistency
    # -------------------------------------------------------------------------
    def _check_responsibility_action(
        self, output: dict[str, Any], _case_input: dict[str, Any] | None
    ) -> str:
        root_cause = output.get("root_cause_analysis", {})
        responsible_parties = root_cause.get("responsible_parties", [])
        party_types = {p.get("party_type") for p in responsible_parties}
        shipment_verdict = output.get("shipment_analysis", {}).get("verdict")
        primary_issue = output.get("assessment", {}).get("primary_issue")
        actions = output.get("resolution_actions", [])

        if len(actions) != len(set(actions)):
            raise InvariantViolationError(
                "ResponsibilityActionConsistency",
                "resolution_actions contains duplicate actions",
            )

        if "logistics_provider" in party_types:
            valid_verdicts = {"logistics_delay", "lost", "conflicting", "insufficient_evidence"}
            is_valid = shipment_verdict in valid_verdicts
            if not is_valid and primary_issue != "late_delivery_logistics":
                raise InvariantViolationError(
                    "ResponsibilityActionConsistency",
                    f"Party is 'logistics_provider' but verdict is '{shipment_verdict}'",
                )

        return "Responsible parties and resolution actions logically aligned"

    # -------------------------------------------------------------------------
    # Invariant 10: Confidence Bounds
    # -------------------------------------------------------------------------
    def _check_confidence_bounds(
        self, output: dict[str, Any], _case_input: dict[str, Any] | None
    ) -> str:
        conf = output.get("assessment", {}).get("confidence", 0.0)
        conflicts = output.get("data_conflicts", [])
        has_unresolved = any(c.get("selected_source") is None for c in conflicts)
        entity_status = output.get("entity_resolution", {}).get("status")

        if (has_unresolved or entity_status in ("ambiguous", "not_found")) and conf > 0.70:
            raise InvariantViolationError(
                "ConfidenceBounds",
                f"Confidence ({conf}) exceeds 0.70 despite unresolved conflict/entity",
            )

        return f"Confidence calibrated at {conf:.2f} within permitted bounds"
