from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import Contracts

# Chỉ cho phép 4 event Task 1.3
ALLOWED_EVENT_TYPES = frozenset(
    {
        "task_assigned",
        "handoff",
        "tool_result_consumed",
        "verification_completed",
    }
)


class TraceWriter:
    """
    Ghi trace dạng JSONL.

    Chỉ chấp nhận 4 loại event
    được quy định trong Task 1.3.
    """

    def __init__(
        self,
        path: Path,
        contracts: Contracts,
    ) -> None:

        self.path = path
        self.contracts = contracts

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    def emit(
        self,
        *,
        case_id: str,
        event_type: str,
        actor: str,
        target: str | None = None,
        decision_code: str | None = None,
        tool_name: str | None = None,
        evidence_refs: list[str]
        | None = None,
        attributes: dict[
            str,
            str
            | int
            | float
            | bool
            | None,
        ]
        | None = None,
    ) -> dict[str, Any]:

        # =========================
        # VALIDATE EVENT TYPE
        # =========================

        if (
            event_type
            not in ALLOWED_EVENT_TYPES
        ):
            allowed = ", ".join(
                sorted(
                    ALLOWED_EVENT_TYPES
                )
            )

            raise ValueError(
                "unsupported trace "
                f"event_type="
                f"{event_type!r}; "
                f"allowed: {allowed}"
            )

        # =========================
        # BUILD EVENT
        # =========================

        event: dict[
            str,
            Any,
        ] = {
            "schema_version":
                "day09-trace-event-v1",

            "event_id":
                "evt_"
                + secrets.token_urlsafe(
                    18
                ),

            "case_id":
                case_id,

            "event_type":
                event_type,

            "occurred_at":
                datetime.now(
                    UTC
                )
                .isoformat()
                .replace(
                    "+00:00",
                    "Z",
                ),

            "actor":
                actor,
        }

        optional = {
            "target":
                target,

            "decision_code":
                decision_code,

            "tool_name":
                tool_name,

            "evidence_refs":
                evidence_refs,

            "attributes":
                attributes,
        }

        # Chỉ đưa field có value vào JSON
        event.update(
            {
                key: value
                for key, value
                in optional.items()
                if value is not None
            }
        )

        # Validate theo schema của repo
        self.contracts.validate_trace(
            event,
            "trace event",
        )

        # =========================
        # WRITE JSONL
        # =========================

        with self.path.open(
            "a",
            encoding="utf-8",
        ) as handle:

            handle.write(
                json.dumps(
                    event,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )

        return event