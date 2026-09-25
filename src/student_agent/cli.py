from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path
from typing import Any

from .cases import load_case_set
from .config import Settings
from .contracts import Contracts
from .mcp_gateway import connect_gateway
from .submission import package_submission, validate_artifacts
from .trace import TraceWriter
from .workflow import solve_case

# ============================================================
# RANDOM SEED
# ============================================================

RANDOM_SEED = 42

random.seed(RANDOM_SEED)


# ============================================================
# PATH HELPERS
# ============================================================

def _root(value: str) -> Path:
    """
    Convert repository root argument to an absolute Path.
    """
    return Path(value).resolve()


def _resolve_path(
    root: Path,
    value: Path,
) -> Path:
    """
    Resolve a path.

    - Absolute path: use directly.
    - Relative path: resolve relative to repository root.
    """
    if value.is_absolute():
        return value.resolve()

    return (root / value).resolve()


# ============================================================
# TASK 1.3 - FAKE TRACE
# ============================================================

def _write_demo_trace(
    root: Path,
) -> Path:
    """
    Generate the fake trace required by Task 1.3.

    Only the four allowed trace events are emitted:
    - task_assigned
    - handoff
    - tool_result_consumed
    - verification_completed
    """

    contracts = Contracts(
        root / "contracts" / "schemas"
    )

    trace_path = (
        root
        / "traces"
        / "trace.jsonl"
    )

    trace_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Remove previous trace.
    trace_path.unlink(
        missing_ok=True
    )

    trace = TraceWriter(
        trace_path,
        contracts,
    )

    case_id = "CASE_DEMO_001"

    # --------------------------------------------------------
    # 1. task_assigned
    # --------------------------------------------------------

    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="entity_resolver",
        attributes={
            "seed": RANDOM_SEED,
        },
    )

    # --------------------------------------------------------
    # 2. handoff
    # --------------------------------------------------------

    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="entity_resolver",
        target="evidence_agent",
        decision_code="ENTITY_RESOLVED",
    )

    # --------------------------------------------------------
    # 3. tool_result_consumed
    # --------------------------------------------------------

    trace.emit(
        case_id=case_id,
        event_type="tool_result_consumed",
        actor="evidence_agent",
        tool_name="demo_lookup",
        attributes={
            "cached": False,
        },
    )

    # --------------------------------------------------------
    # 4. verification_completed
    # --------------------------------------------------------

    trace.emit(
        case_id=case_id,
        event_type="verification_completed",
        actor="verifier",
        decision_code="DEMO_OK",
    )

    return trace_path


# ============================================================
# TASK 2.3 - LOAD DATASET
# ============================================================

def _load_cases_from_directory(
    input_dir: Path,
) -> list[dict[str, Any]]:
    """
    Load every case matching:

        L3B_CASE_XXX.json

    from the supplied input directory.
    """

    if not input_dir.exists():
        raise ValueError(
            f"Input directory does not exist: {input_dir}"
        )

    if not input_dir.is_dir():
        raise ValueError(
            f"Input path is not a directory: {input_dir}"
        )

    case_files = sorted(
        input_dir.glob("L3B_CASE_*.json")
    )

    if not case_files:
        raise ValueError(
            "No L3B_CASE_*.json files found in "
            f"{input_dir}"
        )

    cases: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()

    for path in case_files:
        try:
            # utf-8-sig supports UTF-8 files both with and
            # without a BOM.
            case = json.loads(
                path.read_text(
                    encoding="utf-8-sig"
                )
            )

        except UnicodeDecodeError as exc:
            raise ValueError(
                f"Invalid UTF-8 file: {path}"
            ) from exc

        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON file: {path}"
            ) from exc

        if not isinstance(
            case,
            dict,
        ):
            raise ValueError(
                f"{path.name} must contain a JSON object"
            )

        expected_case_id = path.stem

        actual_case_id = case.get(
            "case_id"
        )

        if actual_case_id != expected_case_id:
            raise ValueError(
                f"{path.name}: case_id mismatch. "
                f"Expected {expected_case_id!r}, "
                f"got {actual_case_id!r}"
            )

        actual_case_id = str(
            actual_case_id
        )

        if actual_case_id in seen_case_ids:
            raise ValueError(
                f"Duplicate case_id: {actual_case_id}"
            )

        seen_case_ids.add(
            actual_case_id
        )

        cases.append(case)

    return cases


def _load_external_case_set(
    input_dir: Path,
) -> dict[str, Any]:
    """
    Load case-set.json located next to the external inputs/
    directory.

    Expected dataset layout:

        D:\\l3b-inputs-v1\\
        ├── case-set.json
        └── inputs\\
            ├── L3B_CASE_001.json
            └── ...
    """

    case_set_path = (
        input_dir.parent
        / "case-set.json"
    )

    if not case_set_path.exists():
        raise ValueError(
            "case-set.json was not found next to "
            f"the input directory: {case_set_path}"
        )

    try:
        payload = json.loads(
            case_set_path.read_text(
                encoding="utf-8-sig"
            )
        )

    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Invalid UTF-8 case-set: {case_set_path}"
        ) from exc

    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid case-set JSON: {case_set_path}"
        ) from exc

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "case-set.json must contain a JSON object"
        )

    case_ids = payload.get(
        "case_ids"
    )

    if not isinstance(
        case_ids,
        list,
    ):
        raise ValueError(
            "case-set.json field 'case_ids' "
            "must be a list"
        )

    if not case_ids:
        raise ValueError(
            "case-set.json contains no case_ids"
        )

    normalized_case_ids = [
        str(case_id)
        for case_id in case_ids
    ]

    if len(
        normalized_case_ids
    ) != len(
        set(normalized_case_ids)
    ):
        raise ValueError(
            "case-set.json contains duplicate case_ids"
        )

    return payload


# ============================================================
# MCP TOOLS
# ============================================================

async def _show_tools(
    root: Path,
) -> None:
    """
    Connect to MCP and list available tools.
    """

    settings = Settings.load(
        root
    )

    contracts = Contracts(
        root
        / "contracts"
        / "schemas"
    )

    async with connect_gateway(
        settings.mcp_endpoint,
        settings.team_api_key,
        contracts,
    ) as gateway:
        tools = await gateway.list_tools()

        if not tools:
            raise RuntimeError(
                "MCP Gateway returned no tools"
            )

        print(
            f"Discovered {len(tools)} MCP tool(s):"
        )

        for tool in tools:
            print(
                f"  - {tool}"
            )


# ============================================================
# EXISTING RUN COMMAND
# ============================================================

async def _run(
    root: Path,
) -> None:
    """
    Existing project run command.

    This mode expects competition files under repository root.
    Task 2.3 should use _run_end_to_end() instead.
    """

    settings = Settings.load(
        root
    )

    case_set = load_case_set(
        root
    )

    contracts = Contracts(
        root
        / "contracts"
        / "schemas"
    )

    output_root = (
        root
        / "outputs"
    )

    trace_path = (
        root
        / "traces"
        / "trace.jsonl"
    )

    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    trace_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Remove old outputs.
    for stale in output_root.glob(
        "*.json"
    ):
        stale.unlink()

    # Remove old trace.
    trace_path.unlink(
        missing_ok=True
    )

    trace = TraceWriter(
        trace_path,
        contracts,
    )

    print(
        f"Running {len(case_set.case_ids)} case(s)"
    )

    async with connect_gateway(
        settings.mcp_endpoint,
        settings.team_api_key,
        contracts,
    ) as gateway:
        discovered_tools = (
            await gateway.list_tools()
        )

        if not discovered_tools:
            raise RuntimeError(
                "MCP Gateway returned no tools"
            )

        print(
            "MCP ready: "
            f"{len(discovered_tools)} tool(s)"
        )

        total = len(
            case_set.case_ids
        )

        for index, case_id in enumerate(
            case_set.case_ids,
            start=1,
        ):
            case = case_set.cases[
                case_id
            ]

            print(
                f"[{index}/{total}] "
                f"{case_id}"
            )

            trace.emit(
                case_id=case_id,
                event_type="task_assigned",
                actor="coordinator",
                target="workflow",
            )

            output = await solve_case(
                case,
                gateway,
                trace,
            )

            contracts.validate_output(
                output,
                f"outputs/{case_id}.json",
            )

            if (
                output.get("case_id")
                != case_id
            ):
                raise ValueError(
                    "Solver returned mismatched "
                    f"case_id for {case_id}"
                )

            target = (
                output_root
                / f"{case_id}.json"
            )

            temporary = (
                target.with_suffix(
                    ".json.tmp"
                )
            )

            temporary.write_text(
                json.dumps(
                    output,
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            temporary.replace(
                target
            )

            trace.emit(
                case_id=case_id,
                event_type="verification_completed",
                actor="coordinator",
                decision_code="OUTPUT_SCHEMA_VALID",
            )

    print(
        "Run complete: "
        f"{total} output(s)"
    )

    print(
        f"Outputs: {output_root}"
    )

    print(
        f"Trace:   {trace_path}"
    )


# ============================================================
# TASK 2.3 - END-TO-END
# ============================================================

async def _run_end_to_end(
    root: Path,
    input_dir: Path,
    output_dir: Path,
) -> None:
    """
    Task 2.3 implementation.

    Steps:
    1. Read external case-set.json.
    2. Read every L3B_CASE_XXX.json.
    3. Validate dataset membership.
    4. Connect to MCP Gateway.
    5. Send every case into solve_case().
    6. Save outputs.
    7. Write real trace events.
    """

    # Task 1.3 requires deterministic seed.
    random.seed(
        RANDOM_SEED
    )

    input_dir = _resolve_path(
        root,
        input_dir,
    )

    output_dir = _resolve_path(
        root,
        output_dir,
    )

    if not input_dir.exists():
        raise ValueError(
            f"Input directory does not exist: {input_dir}"
        )

    if not input_dir.is_dir():
        raise ValueError(
            f"Input path is not a directory: {input_dir}"
        )

    # --------------------------------------------------------
    # LOAD EXTERNAL CASE-SET
    # --------------------------------------------------------

    case_set_payload = (
        _load_external_case_set(
            input_dir
        )
    )

    case_set_path = (
        input_dir.parent
        / "case-set.json"
    )

    variant_id = (
        case_set_payload.get(
            "variant_id"
        )
    )

    case_set_version = (
        case_set_payload.get(
            "case_set_version"
        )
    )

    expected_ids = [
        str(case_id)
        for case_id
        in case_set_payload[
            "case_ids"
        ]
    ]

    # --------------------------------------------------------
    # LOAD ALL CASES
    # --------------------------------------------------------

    cases = (
        _load_cases_from_directory(
            input_dir
        )
    )

    cases_by_id = {
        str(case["case_id"]): case
        for case in cases
    }

    expected_set = set(
        expected_ids
    )

    actual_set = set(
        cases_by_id
    )

    missing_ids = sorted(
        expected_set
        - actual_set
    )

    extra_ids = sorted(
        actual_set
        - expected_set
    )

    if missing_ids:
        raise ValueError(
            "Dataset is missing case(s): "
            + ", ".join(
                missing_ids[:10]
            )
        )

    if extra_ids:
        raise ValueError(
            "Dataset contains unexpected case(s): "
            + ", ".join(
                extra_ids[:10]
            )
        )

    # Follow the exact order in case-set.json.
    ordered_cases = [
        cases_by_id[
            case_id
        ]
        for case_id
        in expected_ids
    ]

    # --------------------------------------------------------
    # LOAD PROJECT CONFIG
    # --------------------------------------------------------

    settings = Settings.load(
        root
    )

    contracts = Contracts(
        root
        / "contracts"
        / "schemas"
    )

    # --------------------------------------------------------
    # PREPARE OUTPUT AND TRACE
    # --------------------------------------------------------

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    trace_path = (
        root
        / "traces"
        / "trace.jsonl"
    )

    trace_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Delete old L3B outputs.
    for stale in output_dir.glob(
        "L3B_CASE_*.json"
    ):
        stale.unlink()

    # Remove old/fake Task 1.3 trace.
    #
    # This ensures trace.jsonl after Task 2.3
    # only contains events from the real run.
    trace_path.unlink(
        missing_ok=True
    )

    trace = TraceWriter(
        trace_path,
        contracts,
    )

    # --------------------------------------------------------
    # PRINT RUN INFORMATION
    # --------------------------------------------------------

    print(
        "=" * 60
    )

    print(
        "TASK 2.3 - END-TO-END"
    )

    print(
        "=" * 60
    )

    print(
        f"Seed:             {RANDOM_SEED}"
    )

    print(
        f"Variant:          {variant_id}"
    )

    print(
        "Case-set version: "
        f"{case_set_version}"
    )

    print(
        f"Case-set:         {case_set_path}"
    )

    print(
        f"Input:            {input_dir}"
    )

    print(
        f"Output:           {output_dir}"
    )

    print(
        "Cases:            "
        f"{len(ordered_cases)}"
    )

    print(
        f"Trace:            {trace_path}"
    )

    print(
        "=" * 60
    )

    # --------------------------------------------------------
    # CONNECT MCP
    # --------------------------------------------------------

    async with connect_gateway(
        settings.mcp_endpoint,
        settings.team_api_key,
        contracts,
    ) as gateway:
        tools = await gateway.list_tools()

        if not tools:
            raise RuntimeError(
                "MCP Gateway returned no tools"
            )

        print(
            f"MCP ready: {len(tools)} tool(s)"
        )

        print()

        total = len(
            ordered_cases
        )

        # ----------------------------------------------------
        # RUN EVERY CASE
        # ----------------------------------------------------

        for index, case in enumerate(
            ordered_cases,
            start=1,
        ):
            case_id = str(
                case["case_id"]
            )

            print(
                f"[{index:03d}/{total:03d}] "
                f"{case_id}"
            )

            # ------------------------------------------------
            # TRACE - task_assigned
            # ------------------------------------------------

            trace.emit(
                case_id=case_id,
                event_type="task_assigned",
                actor="coordinator",
                target="workflow",
                attributes={
                    "input_file":
                        f"{case_id}.json",
                },
            )

            # ------------------------------------------------
            # REAL WORKFLOW
            # ------------------------------------------------

            output = await solve_case(
                case,
                gateway,
                trace,
            )

            if not isinstance(
                output,
                dict,
            ):
                raise ValueError(
                    "solve_case() must return "
                    f"a dict for {case_id}"
                )

            # ------------------------------------------------
            # VALIDATE OUTPUT
            # ------------------------------------------------

            contracts.validate_output(
                output,
                f"outputs/{case_id}.json",
            )

            if (
                output.get("case_id")
                != case_id
            ):
                raise ValueError(
                    f"{case_id}: output case_id "
                    "does not match input case_id"
                )

            # ------------------------------------------------
            # WRITE OUTPUT
            # ------------------------------------------------

            target = (
                output_dir
                / f"{case_id}.json"
            )

            temporary = (
                target.with_suffix(
                    ".json.tmp"
                )
            )

            temporary.write_text(
                json.dumps(
                    output,
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            # Atomic replacement.
            temporary.replace(
                target
            )

            # ------------------------------------------------
            # TRACE - verification_completed
            # ------------------------------------------------

            trace.emit(
                case_id=case_id,
                event_type="verification_completed",
                actor="coordinator",
                decision_code="OUTPUT_SCHEMA_VALID",
                attributes={
                    "output_file":
                        target.name,
                },
            )

    # --------------------------------------------------------
    # FINISH
    # --------------------------------------------------------

    print()

    print(
        "=" * 60
    )

    print(
        "END-TO-END COMPLETED"
    )

    print(
        "=" * 60
    )

    print(
        "Processed: "
        f"{len(ordered_cases)} case(s)"
    )

    print(
        f"Outputs:   {output_dir}"
    )

    print(
        f"Trace:     {trace_path}"
    )


# ============================================================
# ARGPARSE
# ============================================================

def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Day09 L3B student workflow"
        )
    )

    # --------------------------------------------------------
    # PROJECT ROOT
    # --------------------------------------------------------

    result.add_argument(
        "--root",
        default=".",
        help=(
            "Repository root "
            "(default: current directory)"
        ),
    )

    # --------------------------------------------------------
    # TASK 2.3 OPTIONS
    # --------------------------------------------------------

    result.add_argument(
        "--input",
        dest="input_dir",
        type=Path,
        default=None,
        help=(
            "Directory containing "
            "L3B_CASE_XXX.json files"
        ),
    )

    result.add_argument(
        "--output",
        dest="output_dir",
        type=Path,
        default=None,
        help=(
            "Directory for generated "
            "case outputs"
        ),
    )

    # --------------------------------------------------------
    # EXISTING SUBCOMMANDS
    # --------------------------------------------------------

    commands = (
        result.add_subparsers(
            dest="command"
        )
    )

    commands.add_parser(
        "trace-demo",
        help=(
            "Write fake 4-event trace "
            "for Task 1.3"
        ),
    )

    commands.add_parser(
        "validate-inputs",
        help=(
            "Validate repository "
            "case-set.json and inputs"
        ),
    )

    commands.add_parser(
        "mcp-tools",
        help=(
            "Authenticate and list "
            "discovered MCP tools"
        ),
    )

    commands.add_parser(
        "run",
        help=(
            "Run existing repository "
            "workflow"
        ),
    )

    commands.add_parser(
        "validate",
        help=(
            "Validate outputs and "
            "observable trace"
        ),
    )

    package = (
        commands.add_parser(
            "package",
            help=(
                "Validate and build "
                "submission ZIP"
            ),
        )
    )

    # Different destination name so that this option does not
    # conflict with the global Task 2.3 --output option.
    package.add_argument(
        "--output",
        dest="package_output",
        default="dist/submission.zip",
        help=(
            "Destination submission ZIP"
        ),
    )

    return result


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    """
    CLI entry point.
    """

    # Task 1.3 requirement.
    random.seed(
        RANDOM_SEED
    )

    args = (
        parser().parse_args()
    )

    root = _root(
        args.root
    )

    try:
        # ====================================================
        # TASK 2.3
        #
        # python -m src.student_agent.cli
        #   --input D:\\l3b-inputs-v1\\inputs
        #   --output outputs
        # ====================================================

        if args.input_dir is not None:
            if args.command is not None:
                raise ValueError(
                    "--input/--output mode cannot "
                    "be combined with a subcommand"
                )

            if args.output_dir is None:
                raise ValueError(
                    "--output is required "
                    "when --input is used"
                )

            asyncio.run(
                _run_end_to_end(
                    root,
                    args.input_dir,
                    args.output_dir,
                )
            )

        # Prevent --output without --input.
        elif args.output_dir is not None:
            raise ValueError(
                "--input is required "
                "when --output is used"
            )

        # ====================================================
        # TASK 1.3 DEMO
        # ====================================================

        elif args.command in (
            None,
            "trace-demo",
        ):
            trace_path = (
                _write_demo_trace(
                    root
                )
            )

            print(
                "OK: "
                f"seed={RANDOM_SEED} "
                "/ fake trace -> "
                f"{trace_path}"
            )

        # ====================================================
        # VALIDATE REPOSITORY INPUTS
        # ====================================================

        elif (
            args.command
            == "validate-inputs"
        ):
            case_set = (
                load_case_set(
                    root
                )
            )

            print(
                "OK: "
                f"{case_set.variant_id} / "
                f"{case_set.version} / "
                f"{len(case_set.case_ids)} "
                "cases"
            )

        # ====================================================
        # MCP TOOLS
        # ====================================================

        elif (
            args.command
            == "mcp-tools"
        ):
            asyncio.run(
                _show_tools(
                    root
                )
            )

        # ====================================================
        # EXISTING RUN MODE
        # ====================================================

        elif (
            args.command
            == "run"
        ):
            asyncio.run(
                _run(
                    root
                )
            )

        # ====================================================
        # VALIDATE OUTPUTS
        # ====================================================

        elif (
            args.command
            == "validate"
        ):
            case_set = (
                load_case_set(
                    root
                )
            )

            contracts = Contracts(
                root
                / "contracts"
                / "schemas"
            )

            _, trace = (
                validate_artifacts(
                    root,
                    case_set,
                    contracts,
                )
            )

            print(
                "OK: "
                f"{len(case_set.case_ids)} "
                "outputs / "
                f"{len(trace)} "
                "trace events"
            )

        # ====================================================
        # PACKAGE SUBMISSION
        # ====================================================

        elif (
            args.command
            == "package"
        ):
            destination = (
                package_submission(
                    root,
                    root
                    / args.package_output,
                )
            )

            print(
                f"OK: {destination}"
            )

    except (
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )

        raise SystemExit(
            1
        ) from exc


if __name__ == "__main__":
    main()