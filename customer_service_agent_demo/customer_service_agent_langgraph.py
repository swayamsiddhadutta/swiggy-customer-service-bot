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
import operator
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from customer_service_agent import (
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
    name: str
    args: dict[str, Any]


class Message(TypedDict, total=False):
    role: Literal["user", "assistant", "tool", "system"]
    content: Any
    tool_call: ToolCall
    tool_name: str


def merge_dict(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {**left, **right}


class CustomerServiceState(TypedDict, total=False):
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


def reason_node(state: CustomerServiceState) -> dict[str, Any]:
    node_input = {
        "messages": snapshot(state.get("messages", [])),
        "facts": snapshot(state.get("facts", {})),
        "reason_steps": state.get("reason_steps", 0),
    }
    response = choose_next_message(state)
    next_reason_steps = state.get("reason_steps", 0) + 1

    pending_tool_call = response.get("tool_call")
    update: dict[str, Any] = {
        "messages": [response],
        "pending_tool_call": pending_tool_call,
        "reason_steps": next_reason_steps,
    }

    if not pending_tool_call:
        update["final_answer"] = str(response["content"])

    trace_event = {
        "node": "reason",
        "input": node_input,
        "output": snapshot(update),
    }
    return {**update, "trace": [trace_event]}


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


def act_node(state: CustomerServiceState) -> dict[str, Any]:
    tool_call = state.get("pending_tool_call")
    if not tool_call:
        return {}

    node_input = {
        "tool_call": snapshot(tool_call),
        "guardrail_context": {
            "facts_available": list(state.get("facts", {}).keys()),
            "reason_steps": state.get("reason_steps", 0),
        },
    }

    guardrail = guardrails_for_tool_call(state, tool_call)
    if not guardrail["allow"]:
        tool_result = {
            "ok": False,
            "error": "blocked_by_guardrail",
            "guardrail": guardrail,
        }
    else:
        tool = TOOLS[tool_call["name"]]
        tool_result = tool(**tool_call["args"])

    tool_message: Message = {
        "role": "tool",
        "tool_name": tool_call["name"],
        "content": tool_result,
    }
    update = {
        "messages": [tool_message],
        "facts": {tool_call["name"]: tool_result},
        "pending_tool_call": None,
    }
    trace_event = {
        "node": "act",
        "input": node_input,
        "guardrail": snapshot(guardrail),
        "output": snapshot(update),
    }
    return {**update, "trace": [trace_event]}


def route_after_reason(state: CustomerServiceState) -> Literal["act", "end"]:
    if state.get("pending_tool_call"):
        return "act"
    return "end"


def build_graph():
    graph = StateGraph(CustomerServiceState)
    graph.add_node("reason", reason_node)
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
) -> CustomerServiceState:
    return {
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
) -> CustomerServiceState:
    app = build_graph()
    final_state = app.invoke(
        initial_state(customer_id, order_id, user_message),
        config={"recursion_limit": 20},
    )

    if verbose:
        print_trace(final_state)

    return final_state


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


def run_scenarios(verbose: bool) -> None:
    for scenario in SCENARIOS:
        print(f"\n{'=' * 80}")
        print(scenario["name"])
        print(f"USER: {scenario['message']}")
        state = run_agent(
            customer_id=scenario["customer_id"],
            order_id=scenario["order_id"],
            user_message=scenario["message"],
            verbose=verbose,
        )
        print(f"\nFINAL: {state.get('final_answer')}")


def print_real_llm_note() -> None:
    print(
        """
Real LLM replacement point:

    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)
    llm_with_tools = llm.bind_tools(tools)

Then `choose_next_message(state)` becomes a Reason node that calls:

    response = llm_with_tools.invoke(state["messages"])
    return {"messages": [response]}

This demo keeps deterministic local planning so it can run without an API key.
        """.strip()
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset-db", action="store_true", help="Recreate the local SQLite DB.")
    parser.add_argument("--quiet", action="store_true", help="Only print final answers.")
    parser.add_argument("--llm-note", action="store_true", help="Print the real LLM replacement point.")
    args = parser.parse_args()

    if args.reset_db or not Path(DB_PATH).exists():
        init_db(DB_PATH)

    if args.llm_note:
        print_real_llm_note()
        print()

    run_scenarios(verbose=not args.quiet)


if __name__ == "__main__":
    main()
