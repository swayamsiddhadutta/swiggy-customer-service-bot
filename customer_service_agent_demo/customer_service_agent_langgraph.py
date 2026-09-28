#!/usr/bin/env python3
"""
LangGraph version of the customer-service agent demo.

This keeps the same local SQLite tools and deterministic reasoning policy as the
plain Python demo, but moves the loop into LangGraph:

    reason node -> conditional edge -> act node -> reason node

The reason node still uses a local deterministic policy so this file can run
without an LLM API key. Swap the planning logic in `choose_next_message` with a
real tool-calling model when you want model-selected tools.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import operator
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict
import os as _os

try:
    from langgraph.graph import END, START, StateGraph
except Exception as exc:  # pragma: no cover - friendly runtime message
    raise SystemExit(
        "Missing dependency 'langgraph'.\n"
        "Install dependencies with: python3 customer_service_agent_demo/setup_env.py\n"
        "Or run: python3 -m pip install -r customer_service_agent_demo/requirements.txt\n"
        f"Original error: {exc}"
    ) from exc

from llm_gateway import LLMConfig, OpenAICompatibleToolCallingClient
from swiggy_backend import (
    DB_PATH,
    MAX_REASON_STEPS,
    MUTATING_TOOLS,
    READ_ONLY_TOOLS,
    SCENARIOS,
    TOOLS,
    detect_item,
    init_db,
    pretty,
)


class ToolCall(TypedDict):
    id: str
    name: str
    args: dict[str, Any]


class Message(TypedDict, total=False):
    role: Literal["user", "assistant", "tool", "system"]
    content: Any
    tool_call: ToolCall
    tool_name: str
    tool_call_id: str


def merge_dict(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {**left, **right}


class CustomerServiceState(TypedDict, total=False):
    run_id: str
    case_id: str | None
    planner: str
    customer_id: str
    order_id: str | None
    user_message: str
    messages: Annotated[list[Message], operator.add]
    facts: Annotated[dict[str, Any], merge_dict]
    trace: Annotated[list[dict[str, Any]], operator.add]
    pending_tool_call: ToolCall | None
    reason_steps: int
    final_answer: str | None


def snapshot(value: Any) -> Any:
    return copy.deepcopy(value)


def compact_trace(state: CustomerServiceState) -> list[str]:
    lines = []
    for event in state.get("trace", []):
        lines.append(f"{event['node']}: {event.get('output')}")
    return lines[-8:]


def choose_next_message(state: CustomerServiceState) -> Message:
    """
    Deterministic local stand-in for `llm_with_tools.invoke(...)`.

    A real LLM would inspect `state["messages"]` and return either an assistant
    message with tool calls or a final assistant message.
    """
    user_text = state["user_message"].lower()
    facts = state.get("facts", {})
    reason_steps = state.get("reason_steps", 0) + 1

    if reason_steps > MAX_REASON_STEPS:
        return {
            "role": "assistant",
            "content": "I could not finish safely within the step limit.",
            "tool_call": {
                "name": "create_escalation_ticket",
                "args": {
                    "customer_id": state["customer_id"],
                    "order_id": state.get("order_id"),
                    "reasoning": "Agent exceeded max reason steps.",
                    "trace": compact_trace(state),
                    "team": "support-ops",
                },
            },
        }

    if "where" in user_text or "status" in user_text:
        return choose_order_status_message(state, facts)

    if "missing" in user_text or "refund" in user_text:
        return choose_refund_message(state, facts, user_text)

    if "charged twice" in user_text or "double charged" in user_text:
        return choose_duplicate_charge_message(state, facts)

    if "cancel" in user_text:
        return choose_cancel_message(state, facts)

    return {
        "role": "assistant",
        "content": "I need a little more detail to help with that order.",
    }


def forced_step_cap_message(state: CustomerServiceState) -> Message:
    return {
        "role": "assistant",
        "content": "I could not finish safely within the step limit.",
        "tool_call": {
            "id": "call_step_cap_escalation",
            "name": "create_escalation_ticket",
            "args": {
                "customer_id": state["customer_id"],
                "order_id": state.get("order_id"),
                "reasoning": "Agent exceeded max reason steps.",
                "trace": compact_trace(state),
                "team": "support-ops",
            },
        },
    }


def choose_order_status_message(
    state: CustomerServiceState,
    facts: dict[str, Any],
) -> Message:
    if "get_order_status" not in facts:
        return {
            "role": "assistant",
            "content": "Checking current order status.",
            "tool_call": {
                "name": "get_order_status",
                "args": {"order_id": state.get("order_id")},
            },
        }

    result = facts["get_order_status"]
    if not result["ok"]:
        return {"role": "assistant", "content": "I could not find that order."}

    eta = result.get("eta_minutes")
    if eta is None:
        return {
            "role": "assistant",
            "content": f"Your order is currently {result['status']}.",
        }
    return {
        "role": "assistant",
        "content": f"Your order is {result['status']} and should arrive in about {eta} minutes.",
    }


def choose_refund_message(
    state: CustomerServiceState,
    facts: dict[str, Any],
    user_text: str,
) -> Message:
    item = detect_item(user_text)

    if "get_order_details" not in facts:
        return {
            "role": "assistant",
            "content": "Checking the order items first.",
            "tool_call": {
                "name": "get_order_details",
                "args": {"order_id": state.get("order_id")},
            },
        }

    if "check_refund_eligibility" not in facts:
        return {
            "role": "assistant",
            "content": "Checking refund policy.",
            "tool_call": {
                "name": "check_refund_eligibility",
                "args": {
                    "order_id": state.get("order_id"),
                    "item": item,
                    "reason": "missing_item",
                },
            },
        }

    eligibility = facts["check_refund_eligibility"]
    if not eligibility.get("eligible"):
        if eligibility.get("reason") == "refund_window_expired":
            if "create_escalation_ticket" in facts:
                ticket = facts["create_escalation_ticket"]
                return {
                    "role": "assistant",
                    "content": (
                        "This order is outside the automatic refund window, "
                        f"so I escalated it to support as {ticket['ticket_id']}."
                    ),
                }

            return {
                "role": "assistant",
                "content": (
                    "This order is outside the automatic refund window, "
                    "so I cannot issue the refund directly."
                ),
                "tool_call": {
                    "name": "create_escalation_ticket",
                    "args": {
                        "customer_id": state["customer_id"],
                        "order_id": state.get("order_id"),
                        "reasoning": "Refund requested outside automatic refund window.",
                        "trace": compact_trace(state),
                        "team": "support",
                    },
                },
            }

        return {
            "role": "assistant",
            "content": f"I cannot issue this refund automatically: {eligibility.get('reason')}.",
        }

    if "issue_refund" not in facts:
        return {
            "role": "assistant",
            "content": "Issuing approved refund.",
            "tool_call": {
                "name": "issue_refund",
                "args": {
                    "order_id": state.get("order_id"),
                    "item": item,
                    "idempotency_key": f"refund-{state.get('order_id')}-{item}",
                },
            },
        }

    refund = facts["issue_refund"]
    if refund.get("duplicate_request"):
        return {
            "role": "assistant",
            "content": (
                f"A refund of Rs {refund['amount']} for the missing {item} "
                "has already been created for this request."
            ),
        }

    return {
        "role": "assistant",
        "content": (
            f"I refunded Rs {refund['amount']} for the missing {item}. "
            f"It should reflect in {refund['eta']}."
        ),
    }


def choose_duplicate_charge_message(
    state: CustomerServiceState,
    facts: dict[str, Any],
) -> Message:
    if "get_order_history" not in facts:
        return {
            "role": "assistant",
            "content": "Checking order history.",
            "tool_call": {
                "name": "get_order_history",
                "args": {"customer_id": state["customer_id"]},
            },
        }

    history = facts["get_order_history"]
    order_ids = [row["order_id"] for row in history["orders"]]
    if "check_payment_transactions" not in facts:
        return {
            "role": "assistant",
            "content": "Checking payment transactions.",
            "tool_call": {
                "name": "check_payment_transactions",
                "args": {
                    "customer_id": state["customer_id"],
                    "order_ids": order_ids,
                },
            },
        }

    payments = facts["check_payment_transactions"]
    if payments["duplicate_charge_likely"] or history["prior_support_contacts"] >= 2:
        if "create_escalation_ticket" not in facts:
            return {
                "role": "assistant",
                "content": "Escalating payment dispute.",
                "tool_call": {
                    "name": "create_escalation_ticket",
                    "args": {
                        "customer_id": state["customer_id"],
                        "order_id": state.get("order_id"),
                        "reasoning": (
                            "Duplicate charge likely and customer has "
                            f"{history['prior_support_contacts']} prior support contacts."
                        ),
                        "trace": compact_trace(state),
                        "team": "payments",
                    },
                },
            }

        ticket = facts["create_escalation_ticket"]
        return {
            "role": "assistant",
            "content": (
                f"I escalated this to the payments team as {ticket['ticket_id']}. "
                "They will follow up within 24 hours."
            ),
        }

    return {
        "role": "assistant",
        "content": "I do not see a duplicate charge from the current payment records.",
    }


def choose_cancel_message(
    state: CustomerServiceState,
    facts: dict[str, Any],
) -> Message:
    if "get_order_status" not in facts:
        return {
            "role": "assistant",
            "content": "Checking whether the order can still be cancelled.",
            "tool_call": {
                "name": "get_order_status",
                "args": {"order_id": state.get("order_id")},
            },
        }

    status = facts["get_order_status"]
    if not status.get("cancellable"):
        return {
            "role": "assistant",
            "content": f"I cannot cancel this automatically because it is already {status['status']}.",
        }

    if "cancel_order" not in facts:
        return {
            "role": "assistant",
            "content": "Cancelling eligible order.",
            "tool_call": {
                "name": "cancel_order",
                "args": {
                    "order_id": state.get("order_id"),
                    "idempotency_key": f"cancel-{state.get('order_id')}",
                },
            },
        }

    cancellation = facts["cancel_order"]
    return {
        "role": "assistant",
        "content": f"Your order {cancellation['order_id']} has been cancelled.",
    }


def make_reason_node(
    planner: Literal["deterministic", "llm"],
    llm_client: OpenAICompatibleToolCallingClient | None,
):
    def reason_node(state: CustomerServiceState) -> dict[str, Any]:
        node_input = {
            "planner": planner,
            "messages": snapshot(state.get("messages", [])),
            "facts": snapshot(state.get("facts", {})),
            "reason_steps": state.get("reason_steps", 0),
        }
        next_reason_steps = state.get("reason_steps", 0) + 1

        if next_reason_steps > MAX_REASON_STEPS:
            response = forced_step_cap_message(state)
        elif planner == "llm":
            if llm_client is None:
                raise RuntimeError("planner='llm' requires an LLM client")
            response = llm_client.next_message(state)
        else:
            response = choose_next_message(state)

        pending_tool_call = response.get("tool_call")
        update: dict[str, Any] = {
            "messages": [response],
            "pending_tool_call": pending_tool_call,
            "reason_steps": next_reason_steps,
        }

        if not pending_tool_call:
            update["final_answer"] = str(response["content"])

        trace_event = {
            "run_id": state.get("run_id"),
            "case_id": state.get("case_id"),
            "node": "reason",
            "reason_step": next_reason_steps,
            "input": node_input,
            "output": snapshot(update),
        }
        return {**update, "trace": [trace_event]}

    return reason_node


def guardrails_for_tool_call(
    state: CustomerServiceState,
    tool_call: ToolCall,
) -> dict[str, Any]:
    name = tool_call["name"]
    args = tool_call["args"]

    if name not in TOOLS:
        return {"allow": False, "reason": "tool_not_allowlisted"}

    if name in READ_ONLY_TOOLS:
        return {"allow": True, "risk": "read_only"}

    if name in MUTATING_TOOLS:
        if name in {"issue_refund", "cancel_order"} and not args.get("idempotency_key"):
            return {"allow": False, "reason": "missing_idempotency_key"}

        if name == "issue_refund":
            eligibility = state.get("facts", {}).get("check_refund_eligibility")
            if not eligibility or not eligibility.get("eligible"):
                return {"allow": False, "reason": "refund_not_preapproved"}

        if name == "create_escalation_ticket":
            if not args.get("reasoning") or not args.get("trace"):
                return {"allow": False, "reason": "escalation_requires_reasoning_and_trace"}

        return {"allow": True, "risk": "mutating_audited"}

    return {"allow": False, "reason": "unknown_tool_risk"}


def normalize_tool_call(state: CustomerServiceState, tool_call: ToolCall) -> ToolCall:
    """Attach system-owned arguments that should not depend on the LLM."""
    normalized: ToolCall = {
        "id": tool_call.get("id", f"call_{tool_call['name']}"),
        "name": tool_call["name"],
        "args": dict(tool_call["args"]),
    }
    args = normalized["args"]

    if normalized["name"] == "issue_refund" and not args.get("idempotency_key"):
        args["idempotency_key"] = f"refund-{args.get('order_id')}-{args.get('item')}"

    if normalized["name"] == "cancel_order" and not args.get("idempotency_key"):
        args["idempotency_key"] = f"cancel-{args.get('order_id')}"

    if normalized["name"] == "create_escalation_ticket":
        args.setdefault("order_id", state.get("order_id"))
        args.setdefault("team", "support")
        args.setdefault("trace", compact_trace(state))

    return normalized


def act_node(state: CustomerServiceState) -> dict[str, Any]:
    tool_call = state.get("pending_tool_call")
    if not tool_call:
        return {}
    tool_call = normalize_tool_call(state, tool_call)

    node_input = {
        "tool_call": snapshot(tool_call),
        "guardrail_context": {
            "facts_available": list(state.get("facts", {}).keys()),
            "reason_steps": state.get("reason_steps", 0),
        },
    }

    guardrail = guardrails_for_tool_call(state, tool_call)
    # Optionally prompt the user before executing mutating tools or risky actions.
    

    confirm_tools = os.getenv("AGENT_CONFIRM_TOOLS", "").lower()
    if confirm_tools in ("1", "true", "yes"):
        print("\n--- Tool execution preview ---")
        print("Tool:", tool_call["name"])
        print("Args:", json.dumps(tool_call.get("args", {}), indent=2))
        ans = input("Execute this tool? [y/N]: ")
        if ans.strip().lower() not in ("y", "yes"):
            return {
                "messages": [
                    {
                        "role": "tool",
                        "tool_name": tool_call["name"],
                        "tool_call_id": tool_call.get("id", f"call_{tool_call['name']}"),
                        "content": {"ok": False, "error": "user_declined_tool_execution"},
                    }
                ],
                "facts": {tool_call["name"]: {"ok": False, "error": "user_declined_tool_execution"}},
                "pending_tool_call": None,
            }
    if not guardrail["allow"]:
        tool_result = {
            "ok": False,
            "error": "blocked_by_guardrail",
            "guardrail": guardrail,
        }
    else:
        tool = TOOLS[tool_call["name"]]
        try:
            tool_result = tool(**tool_call["args"])
        except TypeError as exc:
            tool_result = {
                "ok": False,
                "error": "tool_argument_error",
                "detail": str(exc),
            }
        except Exception as exc:
            tool_result = {
                "ok": False,
                "error": "tool_execution_error",
                "detail": f"{type(exc).__name__}: {exc}",
            }

    tool_message: Message = {
        "role": "tool",
        "tool_name": tool_call["name"],
        "tool_call_id": tool_call.get("id", f"call_{tool_call['name']}"),
        "content": tool_result,
    }
    update = {
        "messages": [tool_message],
        "facts": {tool_call["name"]: tool_result},
        "pending_tool_call": None,
    }
    trace_event = {
        "run_id": state.get("run_id"),
        "case_id": state.get("case_id"),
        "node": "act",
        "reason_step": state.get("reason_steps", 0),
        "input": node_input,
        "guardrail": snapshot(guardrail),
        "output": snapshot(update),
    }
    return {**update, "trace": [trace_event]}


def route_after_reason(state: CustomerServiceState) -> Literal["act", "end"]:
    if state.get("pending_tool_call"):
        return "act"
    return "end"


def build_graph(
    planner: Literal["deterministic", "llm"] = "deterministic",
    llm_client: OpenAICompatibleToolCallingClient | None = None,
):
    graph = StateGraph(CustomerServiceState)
    graph.add_node("reason", make_reason_node(planner, llm_client))
    graph.add_node("act", act_node)
    graph.add_edge(START, "reason")
    graph.add_conditional_edges(
        "reason",
        route_after_reason,
        {
            "act": "act",
            "end": END,
        },
    )
    graph.add_edge("act", "reason")
    return graph.compile()


def initial_state(
    customer_id: str,
    order_id: str | None,
    user_message: str,
    planner: Literal["deterministic", "llm"] = "deterministic",
    case_id: str | None = None,
    run_id: str | None = None,
) -> CustomerServiceState:
    return {
        "run_id": run_id or str(uuid.uuid4()),
        "case_id": case_id,
        "planner": planner,
        "customer_id": customer_id,
        "order_id": order_id,
        "user_message": user_message,
        "messages": [{"role": "user", "content": user_message}],
        "facts": {},
        "trace": [],
        "pending_tool_call": None,
        "reason_steps": 0,
        "final_answer": None,
    }


def run_agent(
    customer_id: str,
    order_id: str | None,
    user_message: str,
    verbose: bool,
    planner: Literal["deterministic", "llm"] = "deterministic",
    llm_client: OpenAICompatibleToolCallingClient | None = None,
    case_id: str | None = None,
    run_id: str | None = None,
) -> CustomerServiceState:
    app = build_graph(planner=planner, llm_client=llm_client)
    final_state = app.invoke(
        initial_state(
            customer_id,
            order_id,
            user_message,
            planner=planner,
            case_id=case_id,
            run_id=run_id,
        ),
        config={"recursion_limit": 20},
    )

    if verbose:
        print_trace(final_state)

    return final_state


def tool_sequence(state: CustomerServiceState) -> list[str]:
    tools = []
    for event in state.get("trace", []):
        if event.get("node") != "act":
            continue
        output = event.get("output", {})
        for message in output.get("messages", []):
            tool_name = message.get("tool_name")
            if tool_name:
                tools.append(tool_name)
    return tools


def derive_reason_codes(state: CustomerServiceState) -> list[str]:
    facts = state.get("facts", {})
    reason_codes: list[str] = []

    refund_policy = facts.get("check_refund_eligibility")
    if refund_policy:
        reason = refund_policy.get("reason")
        if reason:
            reason_codes.append(str(reason))

    history = facts.get("get_order_history")
    if history and history.get("prior_support_contacts", 0) >= 2:
        reason_codes.append("repeat_support_contacts")

    payments = facts.get("check_payment_transactions")
    if payments and payments.get("duplicate_charge_likely"):
        reason_codes.append("duplicate_charge_likely")

    status = facts.get("get_order_status")
    user_text = state.get("user_message", "").lower()
    if status and "cancel" in user_text:
        if status.get("cancellable"):
            reason_codes.append("cancellable")
        elif "cancellable" in status:
            reason_codes.append("order_not_cancellable")

    return reason_codes


def escalation_team(state: CustomerServiceState) -> str | None:
    ticket = state.get("facts", {}).get("create_escalation_ticket")
    if not ticket:
        return None
    return ticket.get("team")


def normalized_run_trace(state: CustomerServiceState) -> dict[str, Any]:
    return {
        "run_id": state.get("run_id"),
        "case_id": state.get("case_id"),
        "customer_id": state.get("customer_id"),
        "order_id": state.get("order_id"),
        "message": state.get("user_message"),
        "planner": state.get("planner"),
        "tool_sequence": tool_sequence(state),
        "reason_codes": derive_reason_codes(state),
        "escalation_team": escalation_team(state),
        "final_answer": state.get("final_answer"),
        "reason_steps": state.get("reason_steps"),
        "trace_event_count": len(state.get("trace", [])),
    }


def write_run_traces(path: Path, states: list[CustomerServiceState]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for state in states:
            handle.write(json.dumps(normalized_run_trace(state), sort_keys=True) + "\n")


def print_trace(state: CustomerServiceState) -> None:
    for index, event in enumerate(state.get("trace", []), start=1):
        print(f"\n--- Step {index}: {event['node'].upper()} ---")
        print("INPUT")
        print(pretty(event["input"]))
        if "guardrail" in event:
            print("GUARDRAIL")
            print(pretty(event["guardrail"]))
        print("OUTPUT / STATE DELTA")
        print(pretty(event["output"]))


def selected_scenarios(scenario_number: int | None) -> list[dict[str, Any]]:
    if scenario_number is None:
        return SCENARIOS
    if scenario_number < 1 or scenario_number > len(SCENARIOS):
        raise ValueError(f"Scenario must be between 1 and {len(SCENARIOS)}")
    return [SCENARIOS[scenario_number - 1]]


def run_scenarios(
    verbose: bool,
    planner: Literal["deterministic", "llm"],
    llm_client: OpenAICompatibleToolCallingClient | None,
    scenario_number: int | None,
) -> list[CustomerServiceState]:
    states = []
    for scenario in selected_scenarios(scenario_number):
        print(f"\n{'=' * 80}")
        print(scenario["name"])
        print(f"USER: {scenario['message']}")
        state = run_agent(
            customer_id=scenario["customer_id"],
            order_id=scenario["order_id"],
            user_message=scenario["message"],
            verbose=verbose,
            planner=planner,
            llm_client=llm_client,
            case_id=scenario["name"],
        )
        states.append(state)
        print(f"\nFINAL: {state.get('final_answer')}")
    return states


def print_real_llm_note() -> None:
    print(
        """
Real LLM replacement point:

    export AGENT_LLM_PROVIDER=xai
    export AGENT_LLM_MODEL=grok-4.7
    export XAI_API_KEY=...

    python3 customer_service_agent_demo/customer_service_agent_langgraph.py \
      --planner llm --scenario 1 --reset-db

Provider switching is controlled by AGENT_LLM_PROVIDER, AGENT_LLM_MODEL,
AGENT_LLM_BASE_URL, and AGENT_LLM_API_KEY_ENV.
        """.strip()
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset-db", action="store_true", help="Recreate the local SQLite DB.")
    parser.add_argument("--quiet", action="store_true", help="Only print final answers.")
    parser.add_argument("--llm-note", action="store_true", help="Print the real LLM replacement point.")
    parser.add_argument("--planner", choices=["deterministic", "llm"], default="deterministic")
    parser.add_argument("--provider", choices=["xai", "openrouter", "litellm", "openai-compatible"])
    parser.add_argument("--model", help="Model name or gateway alias.")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL.")
    parser.add_argument("--api-key-env", help="Environment variable containing the provider API key.")
    parser.add_argument("--scenario", type=int, help="Run one 1-based scenario instead of all scenarios.")
    parser.add_argument("--customer-id", help="Run a custom single request for this customer.")
    parser.add_argument("--order-id", help="Run a custom single request for this order.")
    parser.add_argument("--message", help="Run a custom single user message.")
    parser.add_argument("--case-id", help="Optional eval case ID for trace output.")
    parser.add_argument("--trace-output", type=Path, help="Write normalized local run traces as JSONL.")
    args = parser.parse_args()

    if args.reset_db or not Path(DB_PATH).exists():
        init_db(DB_PATH)

    if args.llm_note:
        print_real_llm_note()
        return

    llm_client = None
    if args.planner == "llm":
        try:
            config = LLMConfig.from_env(
                provider=args.provider,
                model=args.model,
                base_url=args.base_url,
                api_key_env=args.api_key_env,
            )
            llm_client = OpenAICompatibleToolCallingClient(config)
        except (RuntimeError, ValueError) as exc:
            raise SystemExit(f"LLM configuration error: {exc}") from exc

    if args.message:
        state = run_agent(
            customer_id=args.customer_id or "CUST-1",
            order_id=args.order_id or "ORD-101",
            user_message=args.message,
            verbose=not args.quiet,
            planner=args.planner,
            llm_client=llm_client,
            case_id=args.case_id,
        )
        print(f"\nFINAL: {state.get('final_answer')}")
        states = [state]
    else:
        states = run_scenarios(
            verbose=not args.quiet,
            planner=args.planner,
            llm_client=llm_client,
            scenario_number=args.scenario,
        )

    if args.trace_output:
        write_run_traces(args.trace_output, states)
        print(f"\nTRACE_OUTPUT: {args.trace_output}")


if __name__ == "__main__":
    main()
