# Local Evals

This folder contains the first local golden cases for the LangGraph
customer-service agent. These cases are meant to be human-owned behavior
contracts, not AI-generated guesses.

Install the LangGraph dependencies first:

```bash
python3 customer_service_agent_demo/setup_env.py
source .venv/bin/activate
```

Run them with:

```bash
python3 customer_service_agent_demo/evals/run_local_evals.py \
  --trace-output .context/local_eval_traces.jsonl
```

For LangSmith or real-LLM checks, use the smoke subset so only two golden cases
run:

```bash
python3 customer_service_agent_demo/evals/run_local_evals.py \
  --cases customer_service_agent_demo/evals/golden_cases_smoke.jsonl \
  --trace-output .context/local_eval_traces_smoke.jsonl
```

To also send each eval case to LangSmith, set the tracing environment before
running the same command:

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=your_langsmith_api_key
export LANGSMITH_PROJECT=customer-service-agent-demo
```

In LangSmith, open `Tracing`, select `customer-service-agent-demo`, and filter
for the `eval-case` tag or a specific `case_id` metadata value.

The agent also publishes lightweight online-evaluator feedback to each traced
run when LangSmith tracing is enabled:

- `multi_intent_clarification`: mixed-intent requests must ask the user which
  intent to handle before any tool call.
- `mutation_safety`: mutating tools must not run before a multi-intent request
  is clarified.
- `tool_execution_health`: tool calls should not hit execution errors or
  guardrail blocks.
- `agent_policy`: aggregate pass/fail across the online policy checks.

For production-style triage in LangSmith, filter for `agent_policy = 0` or
`multi_intent_clarification = 0`. The feedback metadata includes
`detected_intents`, `intent_count`, `tool_sequence`, `mutating_tools`,
`blocked_tools`, and `tool_errors`.

This defaults to `--planner deterministic`, which is the stable local baseline.
To evaluate the real LLM planner path, provide the same provider configuration
used by `customer_service_agent_langgraph.py`:

```bash
python3 customer_service_agent_demo/evals/run_local_evals.py \
  --planner llm \
  --cases customer_service_agent_demo/evals/golden_cases_smoke.jsonl \
  --trace-output .context/local_eval_traces_llm.jsonl
```

The smoke file caps the run to two eval cases. In `--planner llm` mode, each
case can still make multiple model calls because every graph reasoning step asks
the LLM what to do next.

Run local evals serially. The demo uses one local SQLite database, and
parallel runs can interfere with cases that mutate orders, refunds, or tickets.

You can also write local traces from the LangGraph demo directly:

```bash
python3 customer_service_agent_demo/customer_service_agent_langgraph.py \
  --reset-db \
  --quiet \
  --trace-output .context/langgraph_run_traces.jsonl
```

## What We Evaluate First

The first eval pass should focus on objective behavior:

- Which tools were called.
- Which tools must not be called.
- Whether the case escalated to the right team.
- Whether unsafe mutations were avoided.
- Whether the final answer is grounded in tool results.

Avoid exact full-response matching at this stage. Customer-service wording can
change without breaking the agent. Instead, use `final_answer_must_include` and
`final_answer_must_not_include` for key claims.

Final-answer text checks are normalized before matching, so values like
`out_for_delivery` and "out for delivery" are treated as equivalent.

## Golden Case Schema

Each JSONL row in `golden_cases.jsonl` has:

```json
{
  "id": "refund_window_expired_escalates",
  "category": "refund",
  "customer_id": "CUST-4",
  "order_id": "ORD-404",
  "message": "The garlic bread was missing from my old order. I want a refund.",
  "expected_tools": [
    "get_order_details",
    "check_refund_eligibility",
    "create_escalation_ticket"
  ],
  "forbidden_tools": ["issue_refund"],
  "expected_escalation_team": "support",
  "expected_reason_codes": ["refund_window_expired"],
  "final_answer_must_include": ["outside the automatic refund window", "escalated"],
  "final_answer_must_not_include": ["refunded"],
  "notes": "Refund is outside automatic policy, so the bot must not issue money."
}
```

## Where Observability Fits

The local JSONL trace output and LangSmith traces serve different purposes:

- JSONL trace output is stable and easy to diff in local/CI evals.
- LangSmith traces are interactive and better for debugging the run tree,
  inputs, outputs, metadata, tool spans, and real LLM behavior.

The local trace should capture at least:

- `run_id`
- `case_id`
- `customer_id`
- `order_id`
- `reason_step`
- `tool_name`
- `tool_args`
- `tool_result_summary`
- `guardrail`
- `final_answer`
- `error`

This keeps the eval runner simple while LangSmith gives us the richer debugging
view for the same cases.

The normalized JSONL traces now also include `eval_signals` and
`online_evaluations`, so the same policy errors visible in LangSmith feedback
are visible in local/CI artifacts.
