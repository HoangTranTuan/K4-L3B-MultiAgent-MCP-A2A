from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .contracts import Contracts

# ============================================================
# ENTITY RESOLVER
# ============================================================


def _normalize(value: Any) -> str:
    """Chuẩn hóa giá trị trước khi so sánh."""
    if value is None:
        return ""

    if isinstance(value, bool):
        return "true" if value else "false"

    return str(value).strip().casefold()


def _value_similarity(
    expected: Any,
    actual: Any,
) -> float:
    """
    Tính độ giống nhau giữa expected và actual.

    Trả về:
    - 1.0: giống hoàn toàn
    - 0.5: một chuỗi chứa chuỗi còn lại
    - 0.0: không giống
    """

    if (
        expected is None
        or expected == ""
        or expected == []
    ):
        return 0.0

    if isinstance(
        expected,
        Sequence,
    ) and not isinstance(
        expected,
        (str, bytes, bytearray),
    ):
        expected_set = {
            _normalize(item)
            for item in expected
            if _normalize(item)
        }

        if isinstance(
            actual,
            Sequence,
        ) and not isinstance(
            actual,
            (str, bytes, bytearray),
        ):
            actual_set = {
                _normalize(item)
                for item in actual
                if _normalize(item)
            }
        else:
            actual_set = (
                {_normalize(actual)}
                if _normalize(actual)
                else set()
            )

        if not expected_set:
            return 0.0

        if not actual_set:
            return 0.0

        return len(
            expected_set & actual_set
        ) / len(
            expected_set | actual_set
        )

    left = _normalize(
        expected
    )

    right = _normalize(
        actual
    )

    if not left or not right:
        return 0.0

    if left == right:
        return 1.0

    if (
        left in right
        or right in left
    ):
        return 0.5

    return 0.0


def score_candidate(
    candidate: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> float:
    """
    Chấm confidence cho candidate.

    Các trường ID/reference được ưu tiên
    trọng số 2, field thường trọng số 1.
    """

    total_weight = 0.0
    weighted_score = 0.0

    for (
        field,
        expected_value,
    ) in expected.items():
        if (
            expected_value is None
            or expected_value == ""
            or expected_value == []
        ):
            continue

        field_lower = (
            field.casefold()
        )

        if (
            field_lower.endswith(
                "_id"
            )
            or field_lower.endswith(
                "_ids"
            )
            or "reference"
            in field_lower
        ):
            weight = 2.0
        else:
            weight = 1.0

        total_weight += weight

        similarity = (
            _value_similarity(
                expected_value,
                candidate.get(
                    field
                ),
            )
        )

        weighted_score += (
            weight * similarity
        )

    if total_weight == 0:
        return 0.0

    score = (
        weighted_score
        / total_weight
    )

    score = max(
        0.0,
        min(
            1.0,
            score,
        ),
    )

    return round(
        score,
        4,
    )


def _candidate_id(
    candidate: Mapping[str, Any],
) -> str:
    """
    Lấy ID đại diện cho candidate.
    """

    for key in (
        "order_id",
        "candidate_id",
        "id",
    ):
        value = candidate.get(
            key
        )

        if (
            value is not None
            and str(
                value
            ).strip()
        ):
            return str(
                value
            ).strip()

    return json.dumps(
        dict(
            candidate
        ),
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


def resolve_entities(
    candidates: Sequence[
        Mapping[str, Any]
    ],
    expected: Mapping[str, Any],
    *,
    threshold: float = 0.65,
    ambiguity_margin: float = 0.10,
) -> dict[str, Any]:
    """
    Entity Resolver.

    - Chấm điểm các candidate.
    - Chọn candidate tốt nhất.
    - Lưu rejected_candidates.
    """

    if not (
        0.0
        <= threshold
        <= 1.0
    ):
        raise ValueError(
            "threshold must be "
            "between 0 and 1"
        )

    if not (
        0.0
        <= ambiguity_margin
        <= 1.0
    ):
        raise ValueError(
            "ambiguity_margin must be "
            "between 0 and 1"
        )

    scored = [
        (
            _candidate_id(
                candidate
            ),
            score_candidate(
                candidate,
                expected,
            ),
        )
        for candidate
        in candidates
    ]

    scored.sort(
        key=lambda item: (
            -item[1],
            item[0],
        )
    )

    if not scored:
        return {
            "status":
                "not_found",
            "resolved_order_ids":
                [],
            "rejected_candidates":
                [],
            "confidence":
                0.0,
        }

    best_id, best_score = (
        scored[0]
    )

    second_score = (
        scored[1][1]
        if len(scored) > 1
        else 0.0
    )

    rejected = [
        candidate_id
        for candidate_id, _
        in scored[1:]
    ]

    if best_score < threshold:
        return {
            "status":
                "not_found",
            "resolved_order_ids":
                [],
            "rejected_candidates": [
                candidate_id
                for candidate_id, _
                in scored
            ],
            "confidence":
                best_score,
        }

    if (
        len(scored) > 1
        and (
            best_score
            - second_score
        )
        < ambiguity_margin
    ):
        return {
            "status":
                "ambiguous",
            "resolved_order_ids":
                [],
            "rejected_candidates": [
                candidate_id
                for candidate_id, _
                in scored
            ],
            "confidence":
                best_score,
        }

    return {
        "status":
            "resolved",
        "resolved_order_ids": [
            best_id
        ],
        "rejected_candidates":
            rejected,
        "confidence":
            best_score,
    }


# ============================================================
# MCP GATEWAY
# ============================================================


class EvidenceGateway:
    """
    MCP Gateway.

    Có:
    - cache theo:
      (case_id, tool_name, params)
    - retry timeout tối đa 2 lần
    """

    def __init__(
        self,
        session: ClientSession,
        contracts: Contracts,
        *,
        max_retries: int = 2,
    ) -> None:
        if max_retries < 0:
            raise ValueError(
                "max_retries must be >= 0"
            )

        self._session = session
        self._contracts = contracts
        self._max_retries = (
            max_retries
        )

        self._cache: dict[
            tuple[str, str, str],
            dict[str, Any],
        ] = {}

    async def list_tools(
        self,
    ) -> list[str]:
        response = (
            await self._session.list_tools()
        )

        return sorted(
            tool.name
            for tool
            in response.tools
        )

    @staticmethod
    def _cache_key(
        case_id: str,
        tool_name: str,
        params: Mapping[
            str,
            Any,
        ],
    ) -> tuple[str, str, str]:
        canonical_params = (
            json.dumps(
                params,
                sort_keys=True,
                ensure_ascii=False,
                separators=(
                    ",",
                    ":",
                ),
                default=str,
            )
        )

        return (
            case_id,
            tool_name,
            canonical_params,
        )

    async def call(
        self,
        tool_name: str,
        *,
        case_id: str,
        **arguments: str,
    ) -> dict[str, Any]:
        # ====================================================
        # CACHE
        # ====================================================

        cache_key = (
            self._cache_key(
                case_id,
                tool_name,
                arguments,
            )
        )

        cached = self._cache.get(
            cache_key
        )

        if cached is not None:
            return copy.deepcopy(
                cached
            )

        payload = {
            "case_id":
                case_id,
            **arguments,
        }

        # ====================================================
        # TIMEOUT TYPES
        # ====================================================

        timeout_exception = getattr(
            httpx2,
            "TimeoutException",
            TimeoutError,
        )

        timeout_errors = (
            TimeoutError,
            asyncio.TimeoutError,
            timeout_exception,
        )

        result = None

        # ====================================================
        # RETRY
        #
        # max_retries = 2:
        # lần đầu + tối đa 2 lần retry
        # ====================================================

        for attempt in range(
            self._max_retries + 1
        ):
            try:
                result = (
                    await self._session.call_tool(
                        tool_name,
                        arguments=payload,
                    )
                )

                break

            except timeout_errors:
                if (
                    attempt
                    >= self._max_retries
                ):
                    raise

                await asyncio.sleep(
                    0
                )

        if result is None:
            raise RuntimeError(
                f"MCP tool "
                f"{tool_name} "
                "returned no result"
            )

        # ====================================================
        # MCP ERROR
        #
        # MCP Python versions may expose:
        # - is_error
        # - isError
        #
        # Current environment uses is_error.
        # ====================================================

        is_error = getattr(
            result,
            "is_error",
            getattr(
                result,
                "isError",
                False,
            ),
        )

        if is_error:
            message = " ".join(
                block.text
                for block
                in result.content
                if getattr(
                    block,
                    "text",
                    None,
                )
            )

            raise RuntimeError(
                f"MCP tool "
                f"{tool_name} failed: "
                f"{message or 'unknown error'}"
            )

        # ====================================================
        # EXTRACT STRUCTURED RESULT
        #
        # Support both naming conventions:
        # - structuredContent
        # - structured_content
        # ====================================================

        evidence = getattr(
            result,
            "structured_content",
            None,
        )

        if evidence is None:
            evidence = getattr(
                result,
                "structuredContent",
                None,
            )

        # ====================================================
        # FALLBACK:
        # parse one textual JSON block
        # ====================================================

        if evidence is None:
            text_blocks = [
                block.text
                for block
                in result.content
                if getattr(
                    block,
                    "text",
                    None,
                )
            ]

            if len(
                text_blocks
            ) != 1:
                raise ValueError(
                    f"MCP tool "
                    f"{tool_name} "
                    "did not return one "
                    "evidence object"
                )

            try:
                evidence = (
                    json.loads(
                        text_blocks[0]
                    )
                )

            except (
                json.JSONDecodeError
            ) as exc:
                raise ValueError(
                    f"MCP tool "
                    f"{tool_name} "
                    "returned invalid "
                    "JSON evidence"
                ) from exc

        if not isinstance(
            evidence,
            Mapping,
        ):
            raise ValueError(
                f"MCP tool "
                f"{tool_name} "
                "returned evidence "
                "that is not an object"
            )

        evidence = dict(
            evidence
        )

        # ====================================================
        # CONTRACT VALIDATION
        # ====================================================

        self._contracts.validate_evidence(
            evidence,
            f"MCP tool {tool_name}",
        )

        # ====================================================
        # CACHE SUCCESSFUL RESULT ONLY
        # ====================================================

        self._cache[
            cache_key
        ] = copy.deepcopy(
            evidence
        )

        return copy.deepcopy(
            evidence
        )


# ============================================================
# MCP CONNECTION
# ============================================================


@asynccontextmanager
async def connect_gateway(
    endpoint: str,
    team_api_key: str,
    contracts: Contracts,
) -> AsyncIterator[
    EvidenceGateway
]:
    headers = {
        "Authorization":
            f"Bearer {team_api_key}"
    }

    timeout = httpx2.Timeout(
        300.0,
        connect=30.0,
        write=30.0,
        pool=30.0,
    )

    async with (
        httpx2.AsyncClient(
            headers=headers,
            timeout=timeout,
        ) as http_client,
        streamable_http_client(
            endpoint,
            http_client=http_client,
        ) as (
            read_stream,
            write_stream,
        ),
        ClientSession(
            read_stream,
            write_stream,
        ) as session,
    ):
        await session.initialize()

        yield EvidenceGateway(
            session,
            contracts,
            max_retries=2,
        )