#!/usr/bin/env python3
"""Run local golden-case evals against the LangGraph customer-service agent."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Literal


DEMO_DIR = Path(__file__).resolve().parents[1]
if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))

from customer_service_agent_langgraph import (  # noqa: E402
    DB_PATH,
    LLMConfig,
    OpenAICompatibleToolCallingClient,
    derive_reason_codes,
    escalation_team,
    init_db,
    normalized_run_trace,
    run_agent,
    tool_sequence,
)


DEFAULT_CASES_PATH = Path(__file__).with_name("golden_cases.jsonl")


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on {path}:{line_number}: {exc}") from exc
    return cases


def make_llm_client(args: argparse.Namespace) -> OpenAICompatibleToolCallingClient | None:
    if args.planner != "llm":
        return None
    config = LLMConfig.from_env(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
    )
    return OpenAICompatibleToolCallingClient(config)


def normalize_text(value: str) -> str:
    """Normalize generated text for non-brittle local substring checks."""
    value = value.lower().replace("_", " ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def normalized_contains(haystack: str, needle: str) -> bool:
    return normalize_text(needle) in normalize_text(haystack)


def evaluate_case(
    case: dict[str, Any],
    planner: Literal["deterministic", "llm"],
    llm_client: OpenAICompatibleToolCallingClient | None,
) -> tuple[bool, list[str], dict[str, Any]]:
    init_db(DB_PATH)
    state = run_agent(
        customer_id=case["customer_id"],
        order_id=case.get("order_id"),
        user_message=case["message"],
        verbose=False,
        planner=planner,
        llm_client=llm_client,
        case_id=case["id"],
    )

    tools = tool_sequence(state)
    reason_codes = derive_reason_codes(state)
    final_answer = state.get("final_answer") or ""

    failures = []

    expected_tools = case.get("expected_tools", [])
    if tools != expected_tools:
        failures.append(f"expected tools {expected_tools}, got {tools}")

    for forbidden_tool in case.get("forbidden_tools", []):
        if forbidden_tool in tools:
            failures.append(f"forbidden tool was called: {forbidden_tool}")

    expected_team = case.get("expected_escalation_team")
    actual_team = escalation_team(state)
    if actual_team != expected_team:
        failures.append(f"expected escalation team {expected_team!r}, got {actual_team!r}")

    for reason_code in case.get("expected_reason_codes", []):
        if reason_code not in reason_codes:
            failures.append(f"missing reason code: {reason_code}")

    for text in case.get("final_answer_must_include", []):
        if not normalized_contains(final_answer, text):
            failures.append(f"final answer missing text: {text!r}")

    for text in case.get("final_answer_must_not_include", []):
        if normalized_contains(final_answer, text):
            failures.append(f"final answer included forbidden text: {text!r}")

    run_trace = normalized_run_trace(state)
    return not failures, failures, run_trace


def write_trace(path: Path, traces: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for trace in traces:
            handle.write(json.dumps(trace, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--trace-output", type=Path)
    parser.add_argument("--planner", choices=["deterministic", "llm"], default="deterministic")
    parser.add_argument("--provider", choices=["xai", "openrouter", "litellm", "openai-compatible"])
    parser.add_argument("--model", help="Model name or gateway alias.")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL.")
    parser.add_argument("--api-key-env", help="Environment variable containing the provider API key.")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    llm_client = make_llm_client(args)
    traces = []
    failures_by_case = {}

    for case in cases:
        passed, failures, run_trace = evaluate_case(case, args.planner, llm_client)
        traces.append(run_trace)
        status = "PASS" if passed else "FAIL"
        print(f"{status} {case['id']}")
        if failures:
            failures_by_case[case["id"]] = failures
            for failure in failures:
                print(f"  - {failure}")

    if args.trace_output:
        write_trace(args.trace_output, traces)
        print(f"\nwrote traces to {args.trace_output}")

    passed_count = len(cases) - len(failures_by_case)
    print(f"\n{passed_count}/{len(cases)} cases passed")

    return 1 if failures_by_case else 0


if __name__ == "__main__":
    raise SystemExit(main())
