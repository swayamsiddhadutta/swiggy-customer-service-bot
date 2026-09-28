# Production Customer Service Agent Plan

This document is the working plan for evolving the local customer-service agent
demo into a production-shaped support agent.

## North Star

The LLM should not be the source of truth for policy decisions or mutating
business actions. It should orchestrate the conversation, gather context, call
the right tools, and explain outcomes. Deterministic tools and guardrails should
decide whether refunds, cancellations, payment actions, credits, or escalations
are allowed.

The production pattern is:

```text
User conversation
  -> LLM planner
  -> read-only data tools
  -> policy decision tools
  -> guarded mutating tools or escalation
  -> final customer-facing response
```

## Core Architecture

### 1. Prompt as Procedure, Not Policy Authority

The system prompt should tell the LLM how to behave:

- Use tools when backend data or policy decisions are needed.
- Do not invent order, payment, refund, or ticket facts.
- Call at most one tool at a time.
- Never perform mutating actions unless a policy decision allows them.
- Escalate when policy tools return an escalation decision.

The prompt may summarize policy workflows, but it should not be the only place
where real business policy lives.

### 2. Human-Owned Policy Source

Policies should live in a human-owned source of truth, such as:

- Internal wiki.
- Markdown docs in a policy repo.
- Policy CMS.
- PDFs, if operations or legal teams already use them.

For engineering, high-impact policies should also be converted into versioned
structured files:

```text
refund_policy_v3.yaml
cancellation_policy_v2.yaml
payment_dispute_policy_v4.yaml
escalation_matrix.yaml
```

The wiki or PDF is the readable source. The structured policy is the executable
source for the agent.

### 3. Policy Decision Tools

The LLM should call explicit policy tools instead of deciding eligibility from
prompt text alone.

Examples:

```python
evaluate_refund_policy(order_id, item, reason)
evaluate_cancellation_policy(order_id)
evaluate_payment_dispute_policy(customer_id, order_ids)
evaluate_escalation_policy(customer_id, order_id, issue_type)
```

These tools should return structured decisions:

```json
{
  "allowed": false,
  "decision": "escalate",
  "reason_code": "refund_window_expired",
  "customer_message": "This order is outside the automatic refund window.",
  "policy_refs": ["refund_policy_v3.section_2.1"],
  "team": "support"
}
```

The LLM should treat this decision packet as authoritative.

### 4. RAG Over Policy Docs

RAG is useful, but it should not be the only thing deciding consequential
actions.

Use RAG for:

- Retrieving policy explanations and citations.
- Helping a human support reviewer understand an escalation.
- Answering non-mutating policy questions.
- Finding related clauses for ambiguous cases.

Do not rely only on RAG for:

- Issuing refunds.
- Cancelling orders.
- Reversing payments.
- Granting credits.
- Making account-level risk decisions.

For these actions, RAG can provide context, but deterministic policy tools
should return the final action decision.

### 5. Guarded Mutating Tools

Mutating tools should verify that a policy decision exists in state before they
run.

Examples:

```text
issue_refund requires evaluate_refund_policy.allowed=true
cancel_order requires evaluate_cancellation_policy.allowed=true
create_escalation_ticket requires reason, trace, and policy decision context
payment reversal should require human approval
```

All mutating tools should use:

- Idempotency keys.
- Audit logs.
- Structured reason codes.
- Policy version or policy reference.
- Tool-call trace.

### 6. Human In The Loop

Escalate or pause for human review when:

- Payment dispute is detected.
- Customer has repeated unresolved support contacts.
- Refund value is above threshold.
- Policy decision is ambiguous.
- Customer is high risk or account risk is elevated.
- Tool calls fail.
- The LLM exceeds the step limit.
- The user asks for something outside automatic policy.

The graph should preserve enough state for a support reviewer to see:

- User request.
- Tool calls and outputs.
- Policy decision packet.
- Reason for escalation.
- Suggested team and priority.

## Target Tool Categories

### Read-Only Lookup Tools

```text
get_order_status
get_order_details
get_order_history
check_payment_transactions
search_policy_docs
retrieve_policy_clause
```

### Policy Decision Tools

```text
evaluate_refund_policy
evaluate_cancellation_policy
evaluate_payment_dispute_policy
evaluate_escalation_policy
```

### Mutating Tools

```text
issue_refund
cancel_order
create_escalation_ticket
```

## Implementation Roadmap

### Phase 1: Clarify LLM Prompt And Tool Categories

Status: in progress.

- Separate automatic resolution policy from execution guardrails.
- List read-only tools, policy decision tools, and mutating tools.
- Tell the LLM to use policy tool decisions as authoritative.
- Keep final responses grounded in tool results.

### Phase 2: Replace Ad Hoc Eligibility With Policy Decision Packets

- Rename or wrap `check_refund_eligibility` as `evaluate_refund_policy`.
- Return `allowed`, `decision`, `reason_code`, `customer_message`,
  `policy_refs`, and `team`.
- Store the latest policy decision in graph state.
- Make `issue_refund` require an allowed refund policy decision.

### Phase 3: Add Structured Policy Files

- Add local YAML or JSON policy files.
- Version each policy.
- Add tests for policy boundary cases:
  - eligible missing item refund
  - refund window expired
  - item not in order
  - order not delivered
  - duplicate payment dispute
  - repeated support contacts
  - cancellable order
  - non-cancellable order

### Phase 4: Add Policy RAG

- Convert policy docs to Markdown or load PDF/wiki content.
- Chunk by policy section.
- Add `search_policy_docs` and `retrieve_policy_clause`.
- Include policy references in escalation tickets.
- Use RAG for explanations, not final eligibility.

### Phase 5: Add Multi-Turn Conversation State

- Persist conversation state by `thread_id`.
- Allow follow-up user messages to reuse prior facts and decisions.
- Add scenarios where the agent asks for missing details, then continues:
  - user reports missing item without naming item
  - user changes order ID
  - user asks why refund was denied
  - user asks for human support after denial

### Phase 6: Add Observability And Logs

Start locally, then move to production-grade observability.

Local logs should capture:

- Run ID.
- Thread ID.
- Customer ID and order ID.
- Reason step number.
- LLM request metadata.
- Tool call name and arguments.
- Tool result summary.
- Guardrail result.
- Policy decision packet.
- Final answer.
- Errors and retries.

Later observability options:

- LangSmith for LangChain/LangGraph traces and evals.
- OpenTelemetry for vendor-neutral traces, metrics, and logs.
- Provider-level LLM request logs where available.
- Application logs for audit and business events.

Important separation:

```text
LLM observability shows reasoning workflow and model/tool behavior.
Business audit logs prove what action actually happened.
Policy logs prove why the action was allowed or escalated.
```

### Phase 7: Evals And Regression Tests

Build an eval suite for:

- Tool sequence correctness.
- Policy decision correctness.
- Guardrail blocking.
- Final answer grounding.
- Escalation routing.
- Multi-turn continuity.
- Prompt-injection resistance.
- Duplicate mutating action prevention.

Every production policy change should have corresponding eval cases.

## Interview Summary

Use this concise answer:

```text
For production, I would not rely on the system prompt or RAG alone for policy
decisions. I would keep human-readable policies in a wiki or document store,
convert the consequential parts into versioned policy tools, and make the LLM
call those tools before any mutating action. RAG can retrieve policy context and
citations, but deterministic policy tools decide eligibility. The LLM handles
conversation and orchestration; the policy layer decides; guardrails enforce;
human review handles edge cases.
```
