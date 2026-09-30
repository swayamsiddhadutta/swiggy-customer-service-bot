from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from openai import OpenAI
import logging

from observability import add_span_outputs, langsmith_span


logging.basicConfig(level=logging.INFO)


PROVIDER_DEFAULTS = {
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "XAI_API_KEY",
        "model": "grok-4.7",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "model": "",
    },
    "litellm": {
        "base_url": "http://localhost:4000/v1",
        "api_key_env": "LITELLM_API_KEY",
        "model": "",
    },
    "openai-compatible": {
        "base_url": "",
        "api_key_env": "LLM_API_KEY",
        "model": "",
    },
}


SYSTEM_PROMPT = """You are a customer-service agent for a Swiggy-like food-delivery app.

You must solve the user's request by calling tools when fresh backend data is needed.
Call at most one tool at a time. Never invent order, payment, refund, or ticket facts.

Automatic resolution policy:
- Order status questions can be answered after get_order_status returns current order data.
- Missing, wrong, or damaged item refund requests require get_order_details followed by check_refund_eligibility.
- If a refund request does not identify the affected item, ask which item was affected before calling refund tools.
- If the affected item appears to be a typo or fuzzy match, ask the user to confirm the likely item before calling refund tools.
- If a refund request does not say the item was missing, wrong, or damaged, ask what was wrong before calling refund tools.
- If check_refund_eligibility returns eligible=true, you may call issue_refund for that item.
- If check_refund_eligibility returns eligible=false for a user's refund or remedy request, do not call issue_refund. Treat the request as outside automatic refund policy and call create_escalation_ticket with the returned reason.
- Payment disputes require get_order_history followed by check_payment_transactions. If duplicate_charge_likely=true or prior_support_contacts is 2 or more, call create_escalation_ticket for the payments team. Do not promise a payment reversal directly.
- Cancellation requests can be handled automatically only when get_order_status shows cancellable=true.
- If one user message contains multiple unrelated intents, especially a mix of read-only and mutating requests, ask the user which one to handle first instead of choosing silently.

Execution guardrails:
- Read-only lookup tools are safe.
- Call at most one tool at a time.
- Mutating tools must follow the automatic resolution policy above.
- If a request is outside automatic policy, create an escalation ticket instead of forcing an action.
- Mutating actions are audited by the system. Do not expose internal trace details to the user.
- Keep final replies concise, specific, and customer-friendly.
"""


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": "Fetch current order status, ETA, restaurant, delivery partner, and status events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_details",
            "description": "Fetch order items, totals, restaurant, delivery address area, and delivery age.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_refund_eligibility",
            "description": "Check whether an item-level refund is allowed by policy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "item": {"type": "string"},
                    "reason": {
                        "type": "string",
                        "enum": ["missing_item", "wrong_item", "damaged_item"],
                    },
                },
                "required": ["order_id", "item", "reason"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "issue_refund",
            "description": "Issue an approved item-level refund. The reason must match the prior check_refund_eligibility result. System will enforce idempotency.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "item": {"type": "string"},
                    "reason": {
                        "type": "string",
                        "enum": ["missing_item", "wrong_item", "damaged_item"],
                    },
                    "idempotency_key": {"type": "string"},
                },
                "required": ["order_id", "item"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_history",
            "description": "Fetch recent customer orders and prior support contacts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                },
                "required": ["customer_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_payment_transactions",
            "description": "Fetch payment transactions for order IDs and detect likely duplicate charges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "order_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["customer_id", "order_ids"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": "Cancel an order if it is still in a cancellable state. System will enforce idempotency.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "idempotency_key": {"type": "string"},
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_escalation_ticket",
            "description": "Create a human escalation ticket with reasoning and trace evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "order_id": {"type": "string"},
                    "reasoning": {"type": "string"},
                    "trace": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "team": {
                        "type": "string",
                        "enum": ["payments", "support", "support-ops"],
                    },
                },
                "required": ["customer_id", "reasoning"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    base_url: str
    api_key_env: str
    temperature: float = 0
    timeout_seconds: float = 30
    max_retries: int = 2

    @classmethod
    def from_env(
        cls,
        provider: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key_env: str | None = None,
    ) -> "LLMConfig":
        selected_provider = provider or os.getenv("AGENT_LLM_PROVIDER", "xai")
        defaults = PROVIDER_DEFAULTS.get(selected_provider)
        if defaults is None:
            raise ValueError(
                f"Unsupported provider {selected_provider!r}. "
                f"Choose one of: {', '.join(PROVIDER_DEFAULTS)}"
            )

        selected_model = model or os.getenv("AGENT_LLM_MODEL") or defaults["model"]
        selected_base_url = base_url or os.getenv("AGENT_LLM_BASE_URL") or defaults["base_url"]
        selected_api_key_env = api_key_env or os.getenv("AGENT_LLM_API_KEY_ENV") or defaults["api_key_env"]

        if not selected_model:
            raise ValueError(
                "No model configured. Set AGENT_LLM_MODEL or pass --model. "
                "For OpenRouter/LiteLLM this should be the provider model slug or gateway alias."
            )
        if not selected_base_url:
            raise ValueError("No base URL configured. Set AGENT_LLM_BASE_URL or pass --base-url.")

        return cls(
            provider=selected_provider,
            model=selected_model,
            base_url=selected_base_url,
            api_key_env=selected_api_key_env,
            temperature=float(os.getenv("AGENT_LLM_TEMPERATURE", "0")),
            timeout_seconds=float(os.getenv("AGENT_LLM_TIMEOUT_SECONDS", "30")),
            max_retries=int(os.getenv("AGENT_LLM_MAX_RETRIES", "2")),
        )

    def api_key(self) -> str:
        value = os.getenv(self.api_key_env)
        if not value:
            raise RuntimeError(
                f"Missing API key. Set {self.api_key_env} for provider {self.provider!r}."
            )
        return value


class OpenAICompatibleToolCallingClient:
    """Small adapter for OpenAI-compatible chat-completions providers."""

    def __init__(self, config: LLMConfig):
        self.config = config
        logging.info(
            "Creating OpenAI-compatible LLM client (provider=%s, model=%s)",
            config.provider,
            config.model,
        )
        self.client = OpenAI(
            api_key=config.api_key(),
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
        )

    def next_message(self, state: dict[str, Any]) -> dict[str, Any]:
        logging.info("Sending request to remote LLM (model=%s)", self.config.model)
        messages = to_openai_messages(state)
        with langsmith_span(
            "openai_compatible_tool_calling_llm",
            run_type="llm",
            inputs={
                "provider": self.config.provider,
                "model": self.config.model,
                "messages": messages,
                "tools": [schema["function"]["name"] for schema in TOOL_SCHEMAS],
            },
            metadata={
                "provider": self.config.provider,
                "model": self.config.model,
                "base_url": self.config.base_url,
                "temperature": self.config.temperature,
            },
            tags=["customer-service-demo", "llm", self.config.provider],
        ) as llm_span:
            completion = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=self.config.temperature,
            )
            message = completion.choices[0].message
            logging.info(
                "Received LLM response: tool_calls=%s, content_length=%d",
                bool(message.tool_calls),
                len((message.content or "")),
            )

            if message.tool_calls:
                tool_call = message.tool_calls[0]
                response = {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_call": {
                        "id": tool_call.id,
                        "name": tool_call.function.name,
                        "args": parse_tool_arguments(tool_call.function.arguments),
                    },
                }
            else:
                response = {
                    "role": "assistant",
                    "content": message.content or "I could not produce a response.",
                }
            add_span_outputs(llm_span, {"message": response})
            return response


def to_openai_messages(state: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "system",
            "content": (
                "Session context: "
                f"customer_id={state.get('customer_id')}, "
                f"current_order_id={state.get('order_id')}. "
                "Use these IDs when the user refers to their current order."
            ),
        },
    ]

    for message in state.get("messages", []):
        role = message["role"]
        if role == "tool":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": message.get("tool_call_id", "call_missing"),
                    "content": json.dumps(message["content"], sort_keys=True),
                }
            )
            continue

        if role == "assistant" and message.get("tool_call"):
            call = message["tool_call"]
            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": [
                        {
                            "id": call.get("id", "call_missing"),
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call["args"], sort_keys=True),
                            },
                        }
                    ],
                }
            )
            continue

        messages.append({"role": role, "content": str(message.get("content", ""))})

    return messages


def parse_tool_arguments(raw_arguments: str) -> dict[str, Any]:
    if not raw_arguments:
        return {}
    try:
        value = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model returned invalid tool JSON: {raw_arguments}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Model returned non-object tool arguments: {value!r}")
    return value
