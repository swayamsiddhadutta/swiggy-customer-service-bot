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

### 4. Refund Related Deterministic Code

Refund automation should not depend on the LLM proving that an item was truly
missing, wrong, or damaged. In food delivery, low-value refunds are usually a
risk-based business decision: verifying the physical truth of every claim can
cost more than the average refund or fraud loss.

The deterministic refund layer should therefore answer different questions:

- Is the claim grounded in a real item from this real order?
- Is the requested reason supported by automatic policy?
- Is the order delivered and inside the refund window?
- Is the refund amount below the automatic threshold?
- Is the customer, restaurant, item, or order risk acceptable?
- Has this item/order already been refunded?

This keeps deterministic code focused on policy and risk, not on rebuilding a
fragile phrase-understanding system beside the LLM. Phrase checks such as exact
item mention, aliases, fuzzy typos, or complementary wording are useful only as
lightweight grounding signals. They should not grow into the main intelligence
of the agent.

Useful concrete policy signals include:

- Customer refund history and recent support contacts.
- Restaurant missing-item or wrong-item rate over recent windows.
- Item category, such as drinks or sides that are commonly missed.
- Refund amount and order value.
- Delivery status, delivery age, and refund window.
- Duplicate refund attempts for the same order and item.

The preferred production pattern is:

```text
LLM extracts the customer claim
  -> deterministic policy checks order, item, reason, risk, and thresholds
  -> low-risk grounded claims are auto-approved
  -> unclear extraction asks for clarification
  -> high-risk or outside-policy claims escalate
```

Clarification should be reserved for cases where grounding fails or conflicts:
the item is not in the order, multiple items are plausible, the reason is
missing, extraction methods disagree, or risk thresholds require human review.
We should avoid asking for confirmation on every refund because that degrades
the agent experience without adding meaningful policy certainty.

### 5. RAG Over Policy Docs

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

### 6. Guarded Mutating Tools

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

### 7. Human In The Loop

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

### 8. Ad Hoc User Data Queries

Users may ask flexible questions about their own historical data, such as:

```text
What did I order two days ago?
Which restaurant did I order biryani from last month?
How much did I spend on delivery fees this week?
```

The first production choice should not be unrestricted text-to-SQL. Prefer this
ladder:

1. Add narrow read-only tools for common questions, such as
   `search_customer_orders(customer_id, filters)`.
2. Add structured query tools where the LLM extracts filters but code builds the
   SQL.
3. Add text-to-SQL only if the query surface is broad enough to justify it.

If text-to-SQL is added, it must run behind deterministic controls:

- Read-only database user.
- Read replica or analytics store, not the primary write database.
- Curated views or a semantic layer, not raw production tables.
- Customer/user permission filters injected by code, never trusted from the LLM.
- Table and column allowlists.
- SQL parser or AST validation.
- Only `SELECT` statements.
- Forced row limits, timeouts, and cost limits.
- Audit logs containing user, question, generated SQL, validation result, row
  count, and final answer.
- Golden evals with expected result sets or result hashes.

The LLM may propose a query plan, filters, or SQL draft, but code must own
authorization, validation, execution limits, and result shaping.

## Target Tool Categories

### Read-Only Lookup Tools

```text
get_order_status
get_order_details
get_order_history
search_customer_orders
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
- Make the LLM gateway explicit about multiple tool-call responses. Today the
  prompt says "call at most one tool at a time" and the adapter reads only the
  first returned tool call. In production, do not silently drop extra tool
  calls. Detect multiple returned tool calls and either reject/retry with a
  single-tool instruction or record a guardrail violation.

### Phase 1A: Make Guardrail Blocks Terminal Or Recoverable Outcomes

- Do not route a guardrail-blocked tool call back into the ordinary
  `act -> reason` loop as if it were a normal tool result.
- Add an explicit graph path for blocked tool calls:
  - recoverable blocks return a clarification message and end the current turn
  - policy blocks return a safe denial or escalation message and end the current
    turn
  - internal contract violations are recorded as errors and do not allow retries
    without new state
- Store guardrail-block metadata separately from successful tool facts. A blocked
  mutation should not populate `facts[tool_name]` in a way that later planning
  can mistake for a completed action.
- Include blocked tool name, reason code, risk category, and customer-facing
  outcome in trace metadata for evals and incident review.

### Phase 1B: Make Loop Limits Deterministic

- Treat the graph recursion limit as a final safety fuse, not the primary
  stopping mechanism.
- Make `MAX_REASON_STEPS` a true reasoning budget. Once the budget is exhausted,
  the graph should enter a terminal or explicitly recoverable path instead of
  continuing normal `reason -> act -> reason` execution.
- If the step-cap path creates an escalation ticket, it should do so at most once
  and then produce a final customer-facing response.
- Add state that distinguishes "step cap reached" and "step-cap escalation
  already attempted" so the agent cannot repeatedly call the same fallback tool
  until LangGraph's recursion limit is hit.
- Add evals that intentionally force the step cap and assert that the final state
  ends before the recursion limit, with a clear answer or escalation outcome.

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
- Add stable per-turn identifiers in addition to `thread_id`, so each
  `graph.invoke(...)` can be evaluated independently while still belonging to
  one customer conversation.
- Store pending clarification state, such as "asked user to choose between
  status and cancellation," so the next turn can validate that the user selected
  one intent before resuming tool use.
- Add conversation-level outcome fields:
  - resolved
  - escalated
  - abandoned
  - latest open clarification
  - mutating tools used across the thread
  - repeated tool calls across the thread
- Add scenarios where the agent asks for missing details, then continues:
  - user reports missing item without naming item
  - user changes order ID
  - user asks why refund was denied
  - user asks for human support after denial
- Add multi-turn evals where a first turn asks for clarification and a later
  turn must only pursue the chosen intent.

### Phase 6: Add Routing, Planning, And Controlled Fanout

- Add a route/decompose step that turns the user message into structured
  intents, such as `order_status`, `refund`, `cancellation`, and
  `payment_dispute`, and `order_history_query`.
- Add deterministic tool and intent metadata:
  - risk category: read-only, policy decision, or mutating workflow
  - whether the intent/tool is parallel-safe
  - dependencies on earlier facts or policy decisions
  - whether user confirmation or human review is required
- Add a plan node that decides execution shape from structured intents and
  metadata, not from free-form LLM judgment.
- Fan out only independent read-only branches. For example, order status and
  order history can run in parallel when neither depends on the other.
- Keep dependent workflows sequential. For example, payment transaction checks
  wait for order history, and refunds wait for refund policy decisions.
- Do not parallelize multiple mutating actions unless deterministic policy
  explicitly allows the sequence.
- Add a join node that merges branch results and produces one final
  customer-facing response.

### Phase 6A: Add Safe Ad Hoc Order Query Support

- Add `search_customer_orders(customer_id, filters)` as a narrow read-only tool
  before considering generic text-to-SQL.
- Let the LLM extract date ranges and filters from user wording, but have code
  inject the authenticated `customer_id` and build parameterized queries.
- Add evals for questions like "what did I order two days ago?" and "which
  restaurant did I order from last week?"
- If generic text-to-SQL is later needed, restrict it to curated read-only
  views with validation, limits, audit logs, and permission enforcement.

### Phase 7: Add Observability And Logs

Start locally, then move to production-grade observability.

Local logs should capture:

- Run ID.
- Thread ID.
- Customer ID and order ID.
- Reason step number.
- LLM request metadata.
- Count of tool calls returned by the LLM.
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

Production trace sampling should be explicit rather than accidental. Add a
small wrapper around the top-level graph invocation to decide whether a request
is traced, which project receives it, and which tags or metadata are attached.
Sampling can be request-based, user-based, or thread-based:

```text
request-based sampling: each graph invocation is sampled independently
user-based sampling: the same users are consistently traced or skipped
thread-based sampling: whole conversations are consistently traced or skipped
```

LangSmith sampling is useful for reducing normal trace volume, but it is
head-based: the decision happens before the run completes. That means a request
that was not traced from the start cannot later produce a complete trace tree if
it fails. Do not rely on sampled LangSmith traces as the only source of
production error visibility.

Future production observability action items:

| Action item | Why it matters | Target shape |
| --- | --- | --- |
| Add a top-level tracing wrapper | Centralizes LangSmith project routing, sampling, tags, metadata, redaction, and environment handling. | `invoke_agent_with_tracing(...)` wraps `graph.invoke(...)`. |
| Add OpenTelemetry tail-based sampling | Keeps 100% of error traces, slow traces, risky tool traces, and guardrail-blocked traces while sampling normal successes. | App emits OTel spans to a collector; collector decides keep/drop after seeing full trace status. |
| Always log application errors separately | LangSmith trace sampling may skip the run that later fails, so production needs complete error coverage. | Send 100% of errors to Sentry, Datadog, CloudWatch, ELK, or equivalent with `request_id`, `thread_id`, `agent_run_id`, and `langsmith_trace_id` when available. |
| Correlate logs and traces | Engineers should be able to jump from an error alert to the relevant agent trace when it exists. | Include trace IDs in app logs and error reports; include log/request IDs in LangSmith metadata. |
| Promote production failures into evals | Production observability should improve pre-deploy protection over time. | Convert recurring bad traces, user complaints, and guardrail blocks into golden eval cases. |

Recommended rollout:

1. **Phase 1: LangSmith wrapper and always-on error logs.** Add the tracing
   wrapper, sample normal LangSmith traces, always trace known risky routes when
   possible, and send 100% of real application errors to Sentry, Datadog,
   CloudWatch, ELK, or equivalent.
2. **Phase 2: OpenTelemetry instrumentation.** Add OTel spans, metrics, and log
   correlation while keeping the sampling policy simple. Export traces through
   a collector, but do not start with complex tail-sampling rules.
3. **Phase 3: Tail-based sampling when volume justifies it.** Once trace volume,
   cost, or debugging needs require it, let the OTel collector keep 100% of
   errors, slow traces, guardrail blocks, and risky tool traces while sampling
   ordinary successful runs.

### Phase 8: Evals And Regression Tests

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

Use different evaluator types at different deployment stages:

- **Pre-launch and early rollout:** use deterministic evaluators for
  tool/state safety, such as unsafe mutation, mutation before policy approval,
  guardrail blocks, tool errors, and reasoning-step limits. Use LLM-as-judge or
  human review for natural-language final-answer groundedness, confusing
  wording, and overpromising because free-form customer messages are hard to
  parse reliably with code.
- **After observing real traffic:** promote recurring judge failures into
  golden cases. Add small structured outcomes or claims only for repeated,
  high-risk categories, such as `refund_issued`, `refund_denied`,
  `cancellation_completed`, `cancellation_denied`, `status_reported`,
  `payment_dispute_escalated`, and `clarification_requested`.
- **At scale:** expand structured outcomes for stable, high-volume flows so
  deterministic LangSmith code evaluators can cover common cases cheaply. Keep
  LLM-as-judge for sampled traces, edge cases, subtle answer quality, and judge
  calibration.

Future online evaluator shape:

```text
Runtime guardrails:
  prevent unsafe actions before they happen

App-side online evaluators:
  score each completed graph invocation and attach feedback to the trace

LangSmith server-side online evaluators:
  continuously score sampled production traces, support backfills, and power
  dashboards, alerts, and review queues

Offline golden evals:
  prevent known regressions before deployment
```

Recommended split:

| Evaluator/check | Where it should run | Reason |
| --- | --- | --- |
| Tool allowlist | App guardrail before tool execution | Must block immediately. |
| Refund and cancellation policy | App policy tool and guardrail before mutation | Protects money and order state. |
| Multi-intent before tool use | App guardrail plus online evaluator | Prevents unsafe action and detects regressions. |
| Tool execution errors and guardrail blocks | App-side online evaluator and LangSmith | Immediate trace feedback plus production monitoring. |
| Tone, helpfulness, and conversation quality | LangSmith LLM-as-judge online evaluator | Too subjective and costly for the critical path. |
| Production drift and anomaly detection | LangSmith server-side online evaluator | Needs sampling, aggregation, and dashboards. |
| Known failure contracts | Offline golden evals | Blocks regressions before rollout. |

Turn-level online evaluators should run after every completed LangGraph
invocation. They answer: "Was this one response safe and policy-compliant?"

Conversation-level evaluators should run when a thread closes, times out,
escalates, or is sampled for review. They answer: "Did the agent behave well
across the whole interaction?" Examples include:

- If the agent asked the user to choose between multiple intents, the following
  turn should only pursue the selected intent.
- The agent should not repeat the same tool call without new user input or new
  backend state.
- The agent should not contradict a policy decision from an earlier turn.
- Mutating tools across the thread should be explainable from user intent,
  policy decisions, and confirmations.

For LangSmith grouping, every trace should include:

```json
{
  "thread_id": "conversation identifier",
  "turn_id": "per-user-message identifier",
  "run_id": "per-graph-invocation identifier",
  "customer_id": "customer identifier",
  "order_id": "current order identifier",
  "route": "selected route or flow",
  "risk_level": "read_only | policy | mutating"
}
```

The goal is not to choose app-side or LangSmith-side evaluation exclusively.
The production pattern should be prevention in the app, detection and trend
monitoring in LangSmith, and regression protection in offline datasets.

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
