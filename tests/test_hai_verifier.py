"""Unit tests for Hai's deliverables: Conflict Resolver, Verifier (10 Invariants), and Builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from student_agent.builder import build_l3b_output, save_case_output
from student_agent.conflict_resolver import ConflictResolver
from student_agent.contracts import Contracts
from student_agent.mock_data import (
    MOCK_CASE_INPUT,
    MOCK_GOLDEN_OUTPUT,
    get_mock_invalid_case,
)
from student_agent.verifier import InvariantViolationError, Verifier


@pytest.fixture
def contracts() -> Contracts:
    root = Path(__file__).resolve().parents[1]
    return Contracts(root / "contracts" / "schemas")


@pytest.fixture
def verifier(contracts: Contracts) -> Verifier:
    return Verifier(contracts)


@pytest.fixture
def resolver() -> ConflictResolver:
    return ConflictResolver()


# -----------------------------------------------------------------------------
# 1. Tests for Conflict Resolver (Source Precedence & Unresolved Conflicts)
# -----------------------------------------------------------------------------
def test_conflict_resolver_shipment_source_precedence(resolver: ConflictResolver) -> None:
    """Tracking log must win over customer statement."""
    source_values = {
        "customer_statement": "delayed",
        "shipment_tracking_log": "on_time",
    }
    source_refs = {
        "customer_statement": "ev_customer_complaint_ticket_1234567890",
        "shipment_tracking_log": "ev_shipment_tracking_log_1234567890abcdef",
    }
    val, record = resolver.resolve_field_conflict(
        field_name="delivery_status",
        source_values=source_values,
        source_evidence_refs=source_refs,
        domain="shipment",
    )
    assert val == "on_time"
    assert record is not None
    assert record.selected_source == "shipment_tracking_log"
    assert "precedence_shipment_tracking_log" in record.resolution_code


def test_conflict_resolver_payment_source_precedence(resolver: ConflictResolver) -> None:
    """Payment gateway must win over customer refund claim."""
    source_values = {
        "customer_refund_claim": 200.0,
        "payment_gateway_record": 150.50,
    }
    val, record = resolver.resolve_field_conflict(
        field_name="refund_amount",
        source_values=source_values,
        domain="payment",
    )
    assert val == 150.50
    assert record is not None
    assert record.selected_source == "payment_gateway_record"


def test_conflict_resolver_unresolved_tie(resolver: ConflictResolver) -> None:
    """Unknown or tie sources result in unresolved conflict with selected_source=None."""
    source_values = {
        "witness_a": "blue",
        "witness_b": "red",
    }
    val, record = resolver.resolve_field_conflict(
        field_name="color",
        source_values=source_values,
        domain="unknown",
    )
    assert val is None
    assert record is not None
    assert record.selected_source is None
    assert record.resolution_code == "unresolved_precedence_tie"


# -----------------------------------------------------------------------------
# 2. Tests for Verifier (10 Invariants)
# -----------------------------------------------------------------------------
def test_verifier_golden_case_passes_all_10_invariants(verifier: Verifier) -> None:
    """The golden output passes all 10 invariants cleanly."""
    report = verifier.verify(MOCK_GOLDEN_OUTPUT, case_input=MOCK_CASE_INPUT)
    assert report.passed is True
    assert report.status == "passed"
    assert len(report.errors) == 0
    assert all(r.passed for r in report.results)
    assert len(report.results) == 10


@pytest.mark.parametrize("invariant_id", list(range(1, 11)))
def test_verifier_catches_each_invariant_violation(
    verifier: Verifier, invariant_id: int
) -> None:
    """For each invariant (1 through 10), verify that deliberate violation is caught."""
    bad_case = get_mock_invalid_case(invariant_id)
    report = verifier.verify(bad_case, case_input=MOCK_CASE_INPUT, raise_on_error=False)

    assert report.passed is False
    assert report.status == "failed"
    assert len(report.errors) >= 1

    # Check that the specific invariant failed
    failed_invariant = next(r for r in report.results if r.invariant_id == invariant_id)
    assert failed_invariant.passed is False


def test_verifier_raise_on_error(verifier: Verifier) -> None:
    """Verifier immediately raises InvariantViolationError when raise_on_error=True."""
    bad_case = get_mock_invalid_case(invariant_id=7)  # Payment total mismatch
    with pytest.raises(InvariantViolationError, match="PaymentRefundTotals"):
        verifier.verify(bad_case, case_input=MOCK_CASE_INPUT, raise_on_error=True)


# -----------------------------------------------------------------------------
# 3. Tests for Output Builder & Atomic Saving
# -----------------------------------------------------------------------------
def test_builder_constructs_valid_schema(verifier: Verifier) -> None:
    """Builder constructs output that passes schema and verifier."""
    output = build_l3b_output(
        case_id="L3B_CASE_001",
        assessment=MOCK_GOLDEN_OUTPUT["assessment"],
        affected_entities=MOCK_GOLDEN_OUTPUT["affected_entities"],
        entity_resolution=MOCK_GOLDEN_OUTPUT["entity_resolution"],
        customer_context=MOCK_GOLDEN_OUTPUT["customer_context"],
        shipment_analysis=MOCK_GOLDEN_OUTPUT["shipment_analysis"],
        payment_analysis=MOCK_GOLDEN_OUTPUT["payment_analysis"],
        root_cause_analysis=MOCK_GOLDEN_OUTPUT["root_cause_analysis"],
        financial_resolution=MOCK_GOLDEN_OUTPUT["financial_resolution"],
        resolution_actions=MOCK_GOLDEN_OUTPUT["resolution_actions"],
        evidence_refs=MOCK_GOLDEN_OUTPUT["evidence_refs"],
        data_conflicts=MOCK_GOLDEN_OUTPUT["data_conflicts"],
        claim_assessments=MOCK_GOLDEN_OUTPUT["claim_assessments"],
    )
    report = verifier.verify(output, case_input=MOCK_CASE_INPUT)
    assert report.passed is True


def test_save_case_output_atomic(verifier: Verifier, tmp_path: Path) -> None:
    """Output is saved atomically to disk and verified."""
    target_path, report = save_case_output(
        MOCK_GOLDEN_OUTPUT,
        output_dir=tmp_path / "outputs",
        verifier=verifier,
        case_input=MOCK_CASE_INPUT,
    )
    assert target_path.exists()
    assert target_path.name == "L3B_CASE_001.json"
    assert report is not None
    assert report.passed is True
