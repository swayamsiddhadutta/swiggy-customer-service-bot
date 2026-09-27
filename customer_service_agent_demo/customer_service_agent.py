#!/usr/bin/env python3
"""
Runnable teaching demo for a customer-service ReAct agent.

This intentionally does not require LangGraph or an API key. The classes and
function names mirror the LangGraph shape:

    reason(state) -> returns a partial state update
    act(state)    -> executes the requested tool and returns a partial update
    observe       -> the merge of those returned updates into state

Swap SimulatedLLMReasoner for a real tool-calling LLM when you want the model to
choose tools dynamically.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, TypedDict


DB_PATH = Path(__file__).with_name("customer_service_demo.sqlite")
MAX_REASON_STEPS = 6

READ_ONLY_TOOLS = {
    "get_order_status",
    "get_order_details",
    "check_refund_eligibility",
    "get_order_history",
    "check_payment_transactions",
}

MUTATING_TOOLS = {
    "issue_refund",
    "cancel_order",
    "create_escalation_ticket",
}


class ToolCall(TypedDict):
    name: str
    args: dict[str, Any]


@dataclass
class Message:
    role: Literal["user", "assistant", "tool", "system"]
    content: Any
    tool_call: ToolCall | None = None
    tool_name: str | None = None


@dataclass
class AgentState:
    customer_id: str
    order_id: str | None
    messages: list[Message]
    facts: dict[str, Any] = field(default_factory=dict)
    pending_tool_call: ToolCall | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    reason_steps: int = 0
    finished: bool = False
    final_answer: str | None = None


def pretty(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def stable_id(prefix: str, value: str, digits: int) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    numeric = int(digest[:12], 16) % (10**digits)
    return f"{prefix}-{numeric:0{digits}d}"


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    cur = conn.cursor()

    cur.executescript(
        """
        DROP TABLE IF EXISTS customers;
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS order_items;
        DROP TABLE IF EXISTS payments;
        DROP TABLE IF EXISTS support_contacts;
        DROP TABLE IF EXISTS refunds;
        DROP TABLE IF EXISTS escalation_tickets;
        DROP TABLE IF EXISTS audit_log;

        CREATE TABLE customers (
            customer_id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );

        CREATE TABLE orders (
            order_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            status TEXT NOT NULL,
            eta_minutes INTEGER,
            delivered_minutes_ago INTEGER,
            cancellable INTEGER NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );

        CREATE TABLE order_items (
            order_id TEXT NOT NULL,
            item TEXT NOT NULL,
            price INTEGER NOT NULL,
            PRIMARY KEY (order_id, item),
            FOREIGN KEY (order_id) REFERENCES orders(order_id)
        );

        CREATE TABLE payments (
            transaction_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders(order_id)
        );

        CREATE TABLE support_contacts (
            contact_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            order_id TEXT,
            status TEXT NOT NULL
        );

        CREATE TABLE refunds (
            refund_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            item TEXT NOT NULL,
            amount INTEGER NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL
        );

        CREATE TABLE escalation_tickets (
            ticket_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            order_id TEXT,
            team TEXT NOT NULL,
            reasoning TEXT NOT NULL,
            trace_json TEXT NOT NULL,
            status TEXT NOT NULL
        );

        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        """
    )

    cur.executemany(
        "INSERT INTO customers(customer_id, name) VALUES (?, ?)",
        [
            ("CUST-1", "Asha"),
            ("CUST-2", "Kabir"),
            ("CUST-3", "Meera"),
            ("CUST-4", "Dev"),
            ("CUST-5", "Ira"),
        ],
    )

    cur.executemany(
        """
        INSERT INTO orders(
            order_id, customer_id, status, eta_minutes,
            delivered_minutes_ago, cancellable
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("ORD-101", "CUST-1", "out_for_delivery", 10, None, 0),
            ("ORD-202", "CUST-2", "delivered", None, 20, 0),
            ("ORD-301", "CUST-3", "delivered", None, 45, 0),
            ("ORD-302", "CUST-3", "created", None, None, 1),
            ("ORD-404", "CUST-4", "delivered", None, 2880, 0),
            ("ORD-505", "CUST-5", "preparing", 35, None, 1),
        ],
    )

    cur.executemany(
        "INSERT INTO order_items(order_id, item, price) VALUES (?, ?, ?)",
        [
            ("ORD-101", "paneer roll", 180),
            ("ORD-202", "burger", 220),
            ("ORD-202", "coke", 120),
            ("ORD-301", "biryani", 450),
            ("ORD-302", "biryani", 450),
            ("ORD-404", "pizza", 520),
            ("ORD-404", "garlic bread", 160),
            ("ORD-505", "dosa", 150),
            ("ORD-505", "filter coffee", 90),
        ],
    )

    cur.executemany(
        "INSERT INTO payments(transaction_id, order_id, customer_id, amount, status) VALUES (?, ?, ?, ?, ?)",
        [
            ("TXN-101", "ORD-101", "CUST-1", 180, "charged"),
            ("TXN-202", "ORD-202", "CUST-2", 340, "charged"),
            ("TXN-301", "ORD-301", "CUST-3", 450, "charged"),
            ("TXN-302", "ORD-302", "CUST-3", 450, "charged"),
            ("TXN-404", "ORD-404", "CUST-4", 680, "charged"),
            ("TXN-505", "ORD-505", "CUST-5", 240, "charged"),
        ],
    )

    cur.executemany(
        "INSERT INTO support_contacts(contact_id, customer_id, order_id, status) VALUES (?, ?, ?, ?)",
        [
            ("SUP-301-A", "CUST-3", "ORD-301", "open"),
            ("SUP-301-B", "CUST-3", "ORD-301", "open"),
        ],
    )

    conn.commit()
    conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def audit(conn: sqlite3.Connection, action: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO audit_log(action, payload_json) VALUES (?, ?)",
        (action, json.dumps(payload, sort_keys=True)),
    )


def get_order_status(order_id: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    """Read-only tool: fetch live order status."""
    with connect(db_path) as conn:
        order = row_to_dict(
            conn.execute(
                """
                SELECT order_id, customer_id, status, eta_minutes, cancellable
                FROM orders
                WHERE order_id = ?
                """,
                (order_id,),
            ).fetchone()
        )
    if not order:
        return {"ok": False, "error": "order_not_found", "order_id": order_id}
    return {"ok": True, **order}


def get_order_details(order_id: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    """Read-only tool: fetch order, items, and delivery age."""
    with connect(db_path) as conn:
        order = row_to_dict(
            conn.execute(
                """
                SELECT order_id, customer_id, status, delivered_minutes_ago, cancellable
                FROM orders
                WHERE order_id = ?
                """,
                (order_id,),
            ).fetchone()
        )
        items = [
            dict(row)
            for row in conn.execute(
                "SELECT item, price FROM order_items WHERE order_id = ? ORDER BY item",
                (order_id,),
            ).fetchall()
        ]
    if not order:
        return {"ok": False, "error": "order_not_found", "order_id": order_id}
    return {"ok": True, **order, "items": items}


def check_refund_eligibility(
    order_id: str,
    item: str,
    reason: str,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Read-only tool: policy check before issuing a refund."""
    details = get_order_details(order_id, db_path)
    if not details.get("ok"):
        return details

    normalized_item = item.lower()
    matched = next(
        (row for row in details["items"] if row["item"].lower() == normalized_item),
        None,
    )
    if matched is None:
        return {
            "ok": True,
            "eligible": False,
            "reason": "item_not_found_in_order",
            "refund_amount": 0,
        }

    if details["status"] != "delivered":
        return {
            "ok": True,
            "eligible": False,
            "reason": "order_not_delivered_yet",
            "refund_amount": 0,
        }

    delivered_minutes_ago = details["delivered_minutes_ago"] or 0
    if delivered_minutes_ago > 24 * 60:
        return {
            "ok": True,
            "eligible": False,
            "reason": "refund_window_expired",
            "refund_amount": 0,
            "delivered_minutes_ago": delivered_minutes_ago,
        }

    if reason not in {"missing_item", "wrong_item", "damaged_item"}:
        return {
            "ok": True,
            "eligible": False,
            "reason": "unsupported_refund_reason",
            "refund_amount": 0,
        }

    return {
        "ok": True,
        "eligible": True,
        "reason": reason,
        "refund_amount": matched["price"],
        "item": matched["item"],
    }


def issue_refund(
    order_id: str,
    item: str,
    idempotency_key: str,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Mutating tool: issue a refund. Guardrails must run before this."""
    eligibility = check_refund_eligibility(order_id, item, "missing_item", db_path)
    if not eligibility.get("eligible"):
        return {
            "ok": False,
            "error": "policy_denied",
            "eligibility": eligibility,
        }

    refund_id = stable_id("REF", idempotency_key, 5)
    with connect(db_path) as conn:
        existing = row_to_dict(
            conn.execute(
                "SELECT refund_id, status, amount FROM refunds WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        )
        if existing:
            return {
                "ok": True,
                "duplicate_request": True,
                "eta": "3-5 business days",
                **existing,
            }

        conn.execute(
            """
            INSERT INTO refunds(refund_id, order_id, item, amount, idempotency_key, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                refund_id,
                order_id,
                eligibility["item"],
                eligibility["refund_amount"],
                idempotency_key,
                "success",
            ),
        )
        audit(
            conn,
            "issue_refund",
            {
                "refund_id": refund_id,
                "order_id": order_id,
                "item": eligibility["item"],
                "amount": eligibility["refund_amount"],
                "idempotency_key": idempotency_key,
            },
        )
        conn.commit()

    return {
        "ok": True,
        "refund_id": refund_id,
        "status": "success",
        "amount": eligibility["refund_amount"],
        "eta": "3-5 business days",
    }


def get_order_history(customer_id: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    """Read-only tool: fetch orders and prior support contacts."""
    with connect(db_path) as conn:
        orders = [
            dict(row)
            for row in conn.execute(
                """
                SELECT order_id, status
                FROM orders
                WHERE customer_id = ?
                ORDER BY order_id
                """,
                (customer_id,),
            ).fetchall()
        ]
        support_contacts = [
            dict(row)
            for row in conn.execute(
                """
                SELECT contact_id, order_id, status
                FROM support_contacts
                WHERE customer_id = ?
                ORDER BY contact_id
                """,
                (customer_id,),
            ).fetchall()
        ]
    return {
        "ok": True,
        "customer_id": customer_id,
        "orders": orders,
        "prior_support_contacts": len(support_contacts),
        "support_contacts": support_contacts,
    }


def check_payment_transactions(
    customer_id: str,
    order_ids: list[str],
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Read-only tool: fetch payment transactions."""
    placeholders = ",".join("?" for _ in order_ids)
    params: list[Any] = [customer_id, *order_ids]
    with connect(db_path) as conn:
        transactions = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT transaction_id, order_id, amount, status
                FROM payments
                WHERE customer_id = ?
                AND order_id IN ({placeholders})
                ORDER BY transaction_id
                """,
                params,
            ).fetchall()
        ]

    charged = [row for row in transactions if row["status"] == "charged"]
    duplicate_charge_likely = len(charged) >= 2 and len({row["amount"] for row in charged}) == 1
    return {
        "ok": True,
        "transactions": transactions,
        "duplicate_charge_likely": duplicate_charge_likely,
    }


def cancel_order(order_id: str, idempotency_key: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    """Mutating tool: cancel an order if policy allows it."""
    status = get_order_status(order_id, db_path)
    if not status.get("ok"):
        return status
    if not status["cancellable"]:
        return {
            "ok": False,
            "error": "policy_denied",
            "reason": "order_not_cancellable",
            "current_status": status["status"],
        }

    with connect(db_path) as conn:
        conn.execute(
            "UPDATE orders SET status = ?, cancellable = 0 WHERE order_id = ?",
            ("cancelled", order_id),
        )
        audit(
            conn,
            "cancel_order",
            {"order_id": order_id, "idempotency_key": idempotency_key},
        )
        conn.commit()

    return {
        "ok": True,
        "order_id": order_id,
        "status": "cancelled",
        "idempotency_key": idempotency_key,
    }


def create_escalation_ticket(
    customer_id: str,
    order_id: str | None,
    reasoning: str,
    trace: list[str],
    team: str = "payments",
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Mutating tool: create a human escalation ticket with audit context."""
    ticket_id = stable_id("TICKET", customer_id + reasoning, 4)
    with connect(db_path) as conn:
        existing = row_to_dict(
            conn.execute(
                """
                SELECT ticket_id, status, team
                FROM escalation_tickets
                WHERE ticket_id = ?
                """,
                (ticket_id,),
            ).fetchone()
        )
        if existing:
            return {"ok": True, "duplicate_request": True, **existing}

        conn.execute(
            """
            INSERT INTO escalation_tickets(
                ticket_id, customer_id, order_id, team, reasoning, trace_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                customer_id,
                order_id,
                team,
                reasoning,
                json.dumps(trace, sort_keys=True),
                "created",
            ),
        )
        audit(
            conn,
            "create_escalation_ticket",
            {
                "ticket_id": ticket_id,
                "customer_id": customer_id,
                "order_id": order_id,
                "team": team,
                "reasoning": reasoning,
            },
        )
        conn.commit()

    return {
        "ok": True,
        "ticket_id": ticket_id,
        "status": "created",
        "team": team,
        "sla": "24 hours",
    }


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "get_order_status": get_order_status,
    "get_order_details": get_order_details,
    "check_refund_eligibility": check_refund_eligibility,
    "issue_refund": issue_refund,
    "get_order_history": get_order_history,
    "check_payment_transactions": check_payment_transactions,
    "cancel_order": cancel_order,
    "create_escalation_ticket": create_escalation_ticket,
}


class SimulatedLLMReasoner:
    """
    Deterministic stand-in for a tool-calling LLM.

    A real LLM would inspect state["messages"] and emit either tool_calls or
    final text. This class makes those decisions predictably so the demo can run
    without an API key.
    """

    def invoke(self, state: AgentState) -> Message:
        state.reason_steps += 1
        user_text = str(state.messages[0].content).lower()

        if state.reason_steps > MAX_REASON_STEPS:
            return Message(
                role="assistant",
                content="I could not finish safely within the step limit.",
                tool_call={
                    "name": "create_escalation_ticket",
                    "args": {
                        "customer_id": state.customer_id,
                        "order_id": state.order_id,
                        "reasoning": "Agent exceeded max reason steps.",
                        "trace": compact_trace(state),
                        "team": "support-ops",
                    },
                },
            )

        if "where" in user_text or "status" in user_text:
            return self._order_status_plan(state)

        if "missing" in user_text or "refund" in user_text:
            return self._refund_plan(state, user_text)

        if "charged twice" in user_text or "double charged" in user_text:
            return self._duplicate_charge_plan(state)

        if "cancel" in user_text:
            return self._cancel_plan(state)

        return Message(
            role="assistant",
            content="I need a little more detail to help with that order.",
        )

    def _order_status_plan(self, state: AgentState) -> Message:
        if "get_order_status" not in state.facts:
            return Message(
                role="assistant",
                content="Checking current order status.",
                tool_call={
                    "name": "get_order_status",
                    "args": {"order_id": state.order_id},
                },
            )

        result = state.facts["get_order_status"]
        if not result["ok"]:
            return Message(role="assistant", content="I could not find that order.")

        eta = result.get("eta_minutes")
        if eta is None:
            return Message(
                role="assistant",
                content=f"Your order is currently {result['status']}.",
            )
        return Message(
            role="assistant",
            content=f"Your order is {result['status']} and should arrive in about {eta} minutes.",
        )

    def _refund_plan(self, state: AgentState, user_text: str) -> Message:
        item = detect_item(user_text)

        if "get_order_details" not in state.facts:
            return Message(
                role="assistant",
                content="Checking the order items first.",
                tool_call={
                    "name": "get_order_details",
                    "args": {"order_id": state.order_id},
                },
            )

        if "check_refund_eligibility" not in state.facts:
            return Message(
                role="assistant",
                content="Checking refund policy.",
                tool_call={
                    "name": "check_refund_eligibility",
                    "args": {
                        "order_id": state.order_id,
                        "item": item,
                        "reason": "missing_item",
                    },
                },
            )

        eligibility = state.facts["check_refund_eligibility"]
        if not eligibility.get("eligible"):
            if "refund_window_expired" == eligibility.get("reason"):
                if "create_escalation_ticket" in state.facts:
                    ticket = state.facts["create_escalation_ticket"]
                    return Message(
                        role="assistant",
                        content=(
                            "This order is outside the automatic refund window, "
                            f"so I escalated it to support as {ticket['ticket_id']}."
                        ),
                    )

                return Message(
                    role="assistant",
                    content=(
                        "This order is outside the automatic refund window, "
                        "so I cannot issue the refund directly."
                    ),
                    tool_call={
                        "name": "create_escalation_ticket",
                        "args": {
                            "customer_id": state.customer_id,
                            "order_id": state.order_id,
                            "reasoning": "Refund requested outside automatic refund window.",
                            "trace": compact_trace(state),
                            "team": "support",
                        },
                    },
                )
            return Message(
                role="assistant",
                content=f"I cannot issue this refund automatically: {eligibility.get('reason')}.",
            )

        if "issue_refund" not in state.facts:
            return Message(
                role="assistant",
                content="Issuing approved refund.",
                tool_call={
                    "name": "issue_refund",
                    "args": {
                        "order_id": state.order_id,
                        "item": item,
                        "idempotency_key": f"refund-{state.order_id}-{item}",
                    },
                },
            )

        refund = state.facts["issue_refund"]
        if refund.get("duplicate_request"):
            return Message(
                role="assistant",
                content=(
                    f"A refund of Rs {refund['amount']} for the missing {item} "
                    "has already been created for this request."
                ),
            )

        return Message(
            role="assistant",
            content=(
                f"I refunded Rs {refund['amount']} for the missing {item}. "
                f"It should reflect in {refund['eta']}."
            ),
        )

    def _duplicate_charge_plan(self, state: AgentState) -> Message:
        if "get_order_history" not in state.facts:
            return Message(
                role="assistant",
                content="Checking order history.",
                tool_call={
                    "name": "get_order_history",
                    "args": {"customer_id": state.customer_id},
                },
            )

        history = state.facts["get_order_history"]
        order_ids = [row["order_id"] for row in history["orders"]]
        if "check_payment_transactions" not in state.facts:
            return Message(
                role="assistant",
                content="Checking payment transactions.",
                tool_call={
                    "name": "check_payment_transactions",
                    "args": {
                        "customer_id": state.customer_id,
                        "order_ids": order_ids,
                    },
                },
            )

        payments = state.facts["check_payment_transactions"]
        if payments["duplicate_charge_likely"] or history["prior_support_contacts"] >= 2:
            if "create_escalation_ticket" not in state.facts:
                return Message(
                    role="assistant",
                    content="Escalating payment dispute.",
                    tool_call={
                        "name": "create_escalation_ticket",
                        "args": {
                            "customer_id": state.customer_id,
                            "order_id": state.order_id,
                            "reasoning": (
                                "Duplicate charge likely and customer has "
                                f"{history['prior_support_contacts']} prior support contacts."
                            ),
                            "trace": compact_trace(state),
                            "team": "payments",
                        },
                    },
                )

            ticket = state.facts["create_escalation_ticket"]
            return Message(
                role="assistant",
                content=(
                    f"I escalated this to the payments team as {ticket['ticket_id']}. "
                    "They will follow up within 24 hours."
                ),
            )

        return Message(
            role="assistant",
            content="I do not see a duplicate charge from the current payment records.",
        )

    def _cancel_plan(self, state: AgentState) -> Message:
        if "get_order_status" not in state.facts:
            return Message(
                role="assistant",
                content="Checking whether the order can still be cancelled.",
                tool_call={
                    "name": "get_order_status",
                    "args": {"order_id": state.order_id},
                },
            )

        status = state.facts["get_order_status"]
        if not status.get("cancellable"):
            return Message(
                role="assistant",
                content=f"I cannot cancel this automatically because it is already {status['status']}.",
            )

        if "cancel_order" not in state.facts:
            return Message(
                role="assistant",
                content="Cancelling eligible order.",
                tool_call={
                    "name": "cancel_order",
                    "args": {
                        "order_id": state.order_id,
                        "idempotency_key": f"cancel-{state.order_id}",
                    },
                },
            )

        cancellation = state.facts["cancel_order"]
        return Message(
            role="assistant",
            content=f"Your order {cancellation['order_id']} has been cancelled.",
        )


def detect_item(user_text: str) -> str:
    for item in ["coke", "pizza", "garlic bread", "burger", "coffee"]:
        if item in user_text:
            return item
    return "coke"


def compact_trace(state: AgentState) -> list[str]:
    lines = []
    for event in state.trace:
        node = event["node"]
        output = event.get("output")
        lines.append(f"{node}: {output}")
    return lines[-8:]


def merge_state_update(state: AgentState, update: dict[str, Any]) -> None:
    """
    This is the Observe step.

    It mirrors LangGraph's reducer idea:
    - messages are appended
    - facts are merged by key
    - pending_tool_call/final flags are replaced
    """
    for message in update.get("messages", []):
        state.messages.append(message)

    for key, value in update.get("facts", {}).items():
        state.facts[key] = value

    if "pending_tool_call" in update:
        state.pending_tool_call = update["pending_tool_call"]

    if "finished" in update:
        state.finished = update["finished"]

    if "final_answer" in update:
        state.final_answer = update["final_answer"]


def reason_node(state: AgentState, reasoner: SimulatedLLMReasoner) -> dict[str, Any]:
    node_input = {
        "messages": [message_to_dict(message) for message in state.messages],
        "facts": copy.deepcopy(state.facts),
        "reason_steps": state.reason_steps,
    }
    response = reasoner.invoke(state)
    node_output: dict[str, Any] = {"messages": [response]}

    if response.tool_call:
        node_output["pending_tool_call"] = response.tool_call
    else:
        node_output["pending_tool_call"] = None
        node_output["finished"] = True
        node_output["final_answer"] = str(response.content)

    state.trace.append(
        {
            "node": "reason",
            "input": copy.deepcopy(node_input),
            "output": copy.deepcopy(serialize_update(node_output)),
        }
    )
    return node_output


def guardrails_for_tool_call(state: AgentState, tool_call: ToolCall) -> dict[str, Any]:
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
            eligibility = state.facts.get("check_refund_eligibility")
            if not eligibility or not eligibility.get("eligible"):
                return {"allow": False, "reason": "refund_not_preapproved"}

        if name == "create_escalation_ticket":
            if not args.get("reasoning") or not args.get("trace"):
                return {"allow": False, "reason": "escalation_requires_reasoning_and_trace"}

        return {"allow": True, "risk": "mutating_audited"}

    return {"allow": False, "reason": "unknown_tool_risk"}


def act_node(state: AgentState) -> dict[str, Any]:
    if not state.pending_tool_call:
        return {}

    tool_call = state.pending_tool_call
    node_input = {
        "tool_call": copy.deepcopy(tool_call),
        "guardrail_context": {
            "facts_available": list(state.facts.keys()),
            "reason_steps": state.reason_steps,
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

    tool_message = Message(
        role="tool",
        tool_name=tool_call["name"],
        content=tool_result,
    )
    update = {
        "messages": [tool_message],
        "facts": {tool_call["name"]: tool_result},
        "pending_tool_call": None,
    }
    state.trace.append(
        {
            "node": "act",
            "input": copy.deepcopy(node_input),
            "guardrail": copy.deepcopy(guardrail),
            "output": copy.deepcopy(serialize_update(update)),
        }
    )
    return update


def message_to_dict(message: Message) -> dict[str, Any]:
    data = {
        "role": message.role,
        "content": message.content,
    }
    if message.tool_call:
        data["tool_call"] = message.tool_call
    if message.tool_name:
        data["tool_name"] = message.tool_name
    return data


def serialize_update(update: dict[str, Any]) -> dict[str, Any]:
    serialized: dict[str, Any] = {}
    for key, value in update.items():
        if key == "messages":
            serialized[key] = [message_to_dict(message) for message in value]
        else:
            serialized[key] = value
    return serialized


def run_agent(
    customer_id: str,
    order_id: str | None,
    user_message: str,
    verbose: bool,
) -> AgentState:
    state = AgentState(
        customer_id=customer_id,
        order_id=order_id,
        messages=[Message(role="user", content=user_message)],
    )
    reasoner = SimulatedLLMReasoner()

    while not state.finished:
        reason_update = reason_node(state, reasoner)
        merge_state_update(state, reason_update)

        if state.pending_tool_call:
            act_update = act_node(state)
            merge_state_update(state, act_update)

        if state.reason_steps > MAX_REASON_STEPS + 1:
            state.finished = True
            state.final_answer = "Stopped by hard execution limit."

    if verbose:
        print_trace(state)

    return state


def print_trace(state: AgentState) -> None:
    for index, event in enumerate(state.trace, start=1):
        print(f"\n--- Step {index}: {event['node'].upper()} ---")
        print("INPUT")
        print(pretty(event["input"]))
        if "guardrail" in event:
            print("GUARDRAIL")
            print(pretty(event["guardrail"]))
        print("OUTPUT / STATE DELTA")
        print(pretty(event["output"]))


SCENARIOS = [
    {
        "name": "1. Where is my order?",
        "customer_id": "CUST-1",
        "order_id": "ORD-101",
        "message": "Where is my order?",
    },
    {
        "name": "2. Missing drink refund",
        "customer_id": "CUST-2",
        "order_id": "ORD-202",
        "message": "My order arrived without the coke I ordered. I want a refund.",
    },
    {
        "name": "3. Charged twice with repeat support contacts",
        "customer_id": "CUST-3",
        "order_id": "ORD-301",
        "message": (
            "I was charged twice, the app shows two orders but I only got one, "
            "and I already messaged support twice."
        ),
    },
    {
        "name": "4. Refund request outside automatic window",
        "customer_id": "CUST-4",
        "order_id": "ORD-404",
        "message": "The garlic bread was missing from my old order. I want a refund.",
    },
    {
        "name": "5. Cancel an order that is still cancellable",
        "customer_id": "CUST-5",
        "order_id": "ORD-505",
        "message": "Please cancel my order.",
    },
]


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
        print(f"\nFINAL: {state.final_answer}")


def print_llm_standard_note() -> None:
    print(
        """
Standard real-LLM setup:

    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model="gpt-4.1-mini",
        temperature=0,
    )
    llm_with_tools = llm.bind_tools(tools)

In the demo, SimulatedLLMReasoner is standing in for that llm_with_tools.invoke(...)
call so the whole thing runs locally without OPENAI_API_KEY.
        """.strip()
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset-db", action="store_true", help="Recreate the local SQLite DB.")
    parser.add_argument("--quiet", action="store_true", help="Only print final answers.")
    parser.add_argument("--llm-note", action="store_true", help="Print the standard llm variable setup.")
    args = parser.parse_args()

    if args.reset_db or not DB_PATH.exists():
        init_db(DB_PATH)

    if args.llm_note:
        print_llm_standard_note()
        print()

    run_scenarios(verbose=not args.quiet)


if __name__ == "__main__":
    main()
