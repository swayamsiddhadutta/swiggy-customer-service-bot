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

This defaults to `--planner deterministic`, which is the stable local baseline.
To evaluate the real LLM planner path, provide the same provider configuration
used by `customer_service_agent_langgraph.py`:

```bash
python3 customer_service_agent_demo/evals/run_local_evals.py \
  --planner llm \
  --trace-output .context/local_eval_traces_llm.jsonl
```

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

## Where Logging Fits

Yes, we should add trace/log statements in the code. The clean order is:

1. Define golden case expectations.
2. Add local trace capture with the fields needed to score those expectations.
3. Write a local eval runner that compares traces against golden cases.
4. Later export the same run structure to LangSmith or OpenTelemetry.

The trace should capture at least:

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

This makes the local eval runner simple and keeps the later observability
integration from changing the eval semantics.
