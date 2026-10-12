# AI Agent Design Lessons

This document captures the important design choices, lessons, and unresolved
questions from the customer-service agent project. It is intended for both
humans and future AI coding agents working on this codebase.

The companion roadmap is `PRODUCTION_AGENT_PLAN.md`. That file describes where
we want to go. This file explains what we are learning while building.

## Project Context

This project is a local customer-service agent for a Swiggy-like food-delivery
app. The agent can answer order-status questions, process eligible item-level
refunds, handle cancellation requests, and escalate payment disputes.

The current implementation has two planning modes:

- Deterministic planner: local coded policy used for repeatable demos and evals.
- LLM planner: model-selected tool calls using the shared system prompt and tool
  schemas.

When reasoning about production behavior, the LLM planner path is the important
one. Deterministic planner fixes are useful for local tests, but they are not
production enforcement unless the same checks exist in guardrails, tools, or
policy decision code.

## Core Principle

The LLM should be intelligent, but it should not be the final authority for
mutating business actions.

Good division of responsibility:

```text
LLM:
  Understand the user request, extract intent, orchestrate tools, and explain.

Read-only tools:
  Fetch order, payment, customer, restaurant, and policy context.

Policy decision tools:
  Decide whether an action is allowed, denied, clarified, or escalated.

Guardrails:
  Enforce that mutating tools only run after the required policy decision.

Mutating tools:
  Execute approved actions with idempotency, audit logs, and reason codes.
```

The production goal is not to remove intelligence from the LLM. The goal is to
make sure consequential actions depend on explicit policy and risk decisions,
not only on a prompt instruction.

## Prompt Is Procedure, Not Policy Authority

The system prompt is useful for telling the LLM how to behave:

- Call tools when backend data is needed.
- Do not invent facts.
- Ask clarifying questions when required.
- Use policy tools before mutating actions.
- Keep final replies concise and grounded.

But the prompt should not be the only place where real business policy lives.
Any policy that controls refunds, cancellations, payments, credits, account
actions, or escalations should also exist in executable code or structured
policy files.

Important lesson: if changing one line of prompt can accidentally allow a refund
that policy should block, the system is not production-safe yet.

## Guardrails Are Enforcement, Not Understanding

Guardrails should block unsafe tool execution. They should not grow into a full
parallel natural-language understanding system.

The refund item guardrail we explored is useful as a defensive layer:

- exact item mention can allow a low-risk path
- aliases can allow a low-risk path
- fuzzy matches should usually require confirmation
- missing or unsupported item references should block mutation

But this guardrail is not a complete authority. It cannot prove that an item was
physically missing, and it can become fragile if we keep adding phrase-specific
logic.

Better long-term framing:

```text
LLM proposes an action.
Policy tools decide whether the action is allowed.
Guardrails verify that the required decision exists before mutation.
```

## Guardrail Blocks Should Change Control Flow

A blocked tool call is not just another failed tool result. It is the graph
saying, "this action should not execute under the current state." Treating that
as ordinary tool output and then sending the agent back through the normal
reasoning loop can create confusing behavior:

- The deterministic planner may see `facts[tool_name]` and assume that step is
  complete, even though the tool never ran.
- An LLM planner may ignore the blocked result and request the same tool again.
- The customer may receive a generic or inconsistent response instead of a clear
  clarification, denial, escalation, or internal-error handling path.

The safer lesson is that guardrail blocks should be explicit graph outcomes.
Recoverable blocks can become customer-facing clarification prompts, such as
"Which item was missing?" or "Please confirm the affected item." Unsafe or
contract-violation blocks should stop the current loop, record the reason, and
either return a policy-safe message or route to escalation/error handling.

Blocked tool calls should also be stored separately from successful business
facts. A blocked `issue_refund` result should not make the state look as if an
`issue_refund` fact exists. Prefer a distinct field such as `guardrail_block`
with the blocked tool name, reason code, and guardrail details.

## Refunds Are Risk Decisions, Not Truth Decisions

A key lesson from refund automation is that deterministic code usually cannot
verify the physical truth of a claim like "the coke was missing." For low-value
food-delivery claims, the practical production question is often not:

```text
Can we prove the item was missing?
```

It is:

```text
Is this claim grounded, low-risk, and inside automatic refund policy?
```

Useful deterministic refund checks:

- The item exists in this order.
- The reason is supported: missing, wrong, or damaged.
- The order is delivered.
- The refund window is valid.
- The refund amount is under the automatic threshold.
- The same item/order has not already been refunded.
- The customer does not have elevated abuse risk.
- Restaurant or item-level complaint patterns support plausibility.

Useful production signals:

- customer refund history
- recent support contacts
- restaurant missing-item rate over recent windows
- item category, such as drinks or sides
- delivery status and delivery age
- refund amount and order value
- duplicate refund attempts

This is better than endlessly adding phrase checks such as "only burger came,"
"everything except the drink," or "the side was missing." Those phrases matter,
but they are language-understanding inputs, not the full policy decision.

## Confirmation Should Be Targeted

Asking for confirmation improves safety when the agent has an uncertain
extraction. Asking for confirmation every time degrades the customer experience.

Use clarification or confirmation when:

- The user does not name the affected item and multiple order items are possible.
- The extracted item is not in the order.
- The extracted reason is unsupported or missing.
- The item is only a fuzzy or typo match.
- The LLM extraction and deterministic grounding disagree.
- The refund is high-value or high-risk.

Do not ask for confirmation just because the agent cannot prove the physical
truth of a low-value claim. That uncertainty is handled by risk policy.

## Complementary Wording Is A Hard Case

Example:

```text
Order contains: burger, coke
User says: "Only the burger was delivered. Please refund the missing item."
```

The user did not explicitly say "coke is missing," but coke is the likely
missing item. A simple exact-match guardrail may block the correct `coke` refund
or, worse, allow the wrong `burger` refund because burger is the only item
mentioned.

This shows why phrase-level guardrails are not enough. A better approach is:

```text
LLM extracts:
  delivered_items = ["burger"]
  issue = "missing_item"

Backend policy sees:
  order_items = ["burger", "coke"]
  inferred_missing_items = ["coke"]

Policy decides:
  allow, clarify, or escalate based on risk and thresholds.
```

The LLM can perform useful extraction. Code should decide whether the extraction
is grounded and low-risk enough to act on.

## One Tool At A Time Is A Safety And State Choice

The current agent asks the LLM to call at most one tool at a time. This matches
the graph state shape: each reasoning step produces one `pending_tool_call`,
then the `act` node executes it, stores the result, and the next reasoning step
can use that result.

This is especially important when tools depend on each other:

```text
get_order_details
  -> check_refund_eligibility
  -> issue_refund
```

The model should not call `check_refund_eligibility` or `issue_refund` before it
has seen the earlier tool result in state.

However, many tool-calling APIs can return multiple tool calls in a single LLM
message. The current demo adapter keeps only the first returned tool call. That
is simple, but it should not be the production behavior because silently
dropping extra calls can hide model mistakes.

Production options:

- Reject the model response and retry with an instruction to return one tool.
- Record a guardrail violation when multiple calls are returned.
- Allow multiple calls only for explicitly safe, independent, read-only tools.
- Execute allowed parallel calls in a dedicated fan-out/fan-in graph node.

Multiple tool calls are desirable only when calls are independent and safe to
parallelize. They are risky when one call depends on another result, or when any
call mutates business state.

## Parallel Versus Sequential Work Should Be Graph-Owned

For multi-intent requests, the graph should control which work can run in
parallel and which work must remain sequential. The LLM can help decompose the
user request, but code should decide execution order based on tool metadata,
dependencies, and risk.

Recommended shape:

```text
decompose_or_route
  -> validate_intents_and_risk
  -> plan_execution
  -> parallel read-only branches when independent
  -> sequential workflow branches when dependent
  -> join_results
  -> final_response
```

Examples:

- Status lookup and order-history lookup can run in parallel when both are
  read-only and independent.
- Payment-dispute checking may start with order history, but
  `check_payment_transactions` must wait for the order IDs returned by
  `get_order_history`.
- Refund workflows should remain sequential or hidden behind a higher-level
  policy tool because eligibility and mutation depend on earlier facts.
- Multiple mutating intents, such as cancel plus refund, should usually ask the
  user which action to handle first unless a deterministic policy explicitly
  allows a sequence.

The production rule is:

```text
Parallelize independent read-only branches.
Keep dependent work inside sequential subflows.
Do not parallelize mutating business actions without explicit policy approval.
```

## Text-To-SQL Should Be A Last Resort

For flexible user-data questions, it is tempting to give the LLM a SQL tool and
let it query the database directly. That should not be the default production
choice.

Example:

```text
User: "What did I order two days ago?"
```

Safer ladder:

1. Add a narrow read-only tool:

   ```text
   search_customer_orders(customer_id, filters)
   ```

2. Add a structured query tool where the LLM extracts filters, but code builds
   the SQL.
3. Use text-to-SQL only when narrow tools and structured query tools cannot
   cover the legitimate query surface.

The reason is the same core agent principle: the LLM can understand language,
but code should own authorization and execution. The LLM may extract:

```json
{
  "intent": "order_history_query",
  "date_range": "two_days_ago"
}
```

but code should inject the authenticated customer ID and build the query:

```text
WHERE customer_id = authenticated_customer_id
```

If text-to-SQL is eventually added, it should run only over restricted read-only
views or a semantic layer, with:

- read-only credentials
- row-level or application-enforced customer permissions
- table and column allowlists
- SQL parser or AST validation
- forced `LIMIT`
- timeouts and cost limits
- audit logs of the question, generated SQL, validation result, and result size
- evals with expected result sets

Do not let the model be responsible for remembering permissions like "only this
customer's data." Permission filters must be deterministic and enforced outside
the prompt.

## Evals Are How We Notice Architecture Pressure

Routing, separate prompts, and specialized subgraphs should not be added only
because they look cleaner. They should be added when evals and traces show that
the single shared agent is repeatedly confused by irrelevant context, tools, or
rules.

Useful eval categories:

- tool sequence correctness
- forbidden tool checks
- forbidden tool category checks
- policy reason codes
- escalation routing
- multi-intent handling
- prompt-injection resistance
- final answer grounding
- duplicate mutation prevention

Signals that routing may be needed:

- Refund requests trigger cancellation or payment tools.
- Payment disputes trigger item refunds.
- Status questions trigger policy checks or mutation tools.
- Fixing one category repeatedly breaks another.
- Multi-intent requests behave inconsistently.
- Traces show the LLM considering many irrelevant tools.

One failure is not enough to justify a new graph architecture. Repeated patterns
across evals and traces are the real signal.

## Online Evaluators Need Clear Ownership

Online evaluation is not just "log more fields and filter in the UI." The
complete loop is:

```text
trace useful facts
  -> run an evaluator automatically
  -> store the score as feedback
  -> alert, filter, review, or promote failures into offline evals
```

There are three different places evaluator-like logic can live:

1. **Runtime guardrails inside the app.** These run before a tool executes and
   must block unsafe behavior. They are prevention, not just evaluation.
2. **App-side online evaluators.** These run after one completed graph
   invocation and attach feedback to the trace. They are useful for cheap,
   deterministic checks that should be available immediately.
3. **LangSmith server-side online evaluators.** These run on traced production
   runs outside the app. They are better for sampling, backfills, dashboards,
   LLM-as-judge checks, drift monitoring, and review queues.

The best production pattern is hybrid:

```text
guardrails prevent unsafe behavior
app-side evaluators attach immediate deterministic feedback
LangSmith-side evaluators monitor production behavior over time
offline golden evals prevent known regressions before deployment
```

For example, a mixed status-plus-cancel request should be protected at multiple
layers:

- The app guardrail should prevent mutating tools before clarification.
- The app-side evaluator should score the completed run as a failure if tools
  were called before the user chose one intent.
- A LangSmith-side evaluator should monitor production traces for the same
  pattern, especially on sampled real LLM traffic.
- The golden eval should keep this behavior from regressing before deploy.

The division matters because evaluators that merely detect bad behavior after
the response cannot protect money, orders, account state, or user trust. Any
policy that must affect the current customer-visible action belongs in runtime
policy tools or guardrails.

## Prioritize Failures By Deployment Stage

Not every evaluator failure deserves the same urgency. For a food-delivery
support agent, prioritize by blast radius first, then user experience, then
cost and operational polish.

Before launch, fix failures that can wrongly change business state or materially
mislead the customer:

- Unsafe mutation, such as issuing a refund, cancelling an order, or creating a
  duplicate mutation without the required policy decision and idempotency.
- Final answers that are not grounded in tool results. Because the final answer
  is natural language, use an LLM-as-judge or human review early for answer
  groundedness, confusion, and overpromising.
- Multi-intent requests that lead to mutating tools before the user clarifies
  which request to handle.
- Tool errors and guardrail blocks that make safe, common requests fail. These
  are still best tracked with deterministic state and trace checks.

Do not over-engineer a large claim taxonomy before real traffic. In early
rollouts, combine deterministic evaluators for tool/state safety with
LLM-as-judge evaluation for customer-facing prose. After traces show repeated
answer failure patterns, add small structured outcomes or claims for the
high-volume, high-risk cases only, such as `refund_issued`,
`refund_denied`, `cancellation_completed`, `cancellation_denied`,
`status_reported`, `payment_dispute_escalated`, and
`clarification_requested`.

During canary or early production, keep measuring the above and start watching
for reliability and cost signals:

- Slow runs, broken down by graph step, tool call, LLM call, and evaluator.
- Too many reasoning steps, especially repeated tool calls or loops without new
  information.
- Wrong escalation team, if it affects SLA or causes repeated manual rerouting.

In mature production, optimize the agent shape using patterns from real traces:

- Remove repeated unnecessary read-only tool calls when they materially affect
  latency or cost.
- Split into route-specific flows only after evals show repeated confusion from
  a shared prompt or broad tool surface.
- Expand structured outcomes or claims once the domain flows are stable enough
  that deterministic checks can replace judge calls for common cases.
- Calibrate LLM-as-judge evaluators by reviewing false positives and false
  negatives before using judge scores for release gates or dashboards.

The sequencing rule is: prevent harm first, reduce user confusion second,
improve reliability third, and optimize cost or latency once the basic behavior
is safe and observable.

## Multi-Turn Conversations Need Thread-Level Evals

A single LangGraph invocation usually corresponds to one user turn:

```text
user message
  -> graph.invoke(...)
  -> assistant response
  -> turn-level evaluator feedback
```

That is necessary but not sufficient for a support conversation. Multi-turn
agents need two evaluation scopes:

| Scope | When it runs | What it answers |
| --- | --- | --- |
| Turn-level eval | After each graph invocation | Was this response safe, grounded, and policy-compliant? |
| Thread-level eval | At close, timeout, escalation, or sampling | Did the agent behave well across the whole conversation? |

Thread-level evaluation catches failures that a single turn cannot see:

- The agent asked for clarification but ignored the user's answer.
- The agent contradicted an earlier policy decision.
- The agent repeated the same tool call without new information.
- The agent handled one intent safely in isolation but failed the overall task.
- The agent performed multiple mutations across turns without a coherent user
  confirmation and policy trail.

For the multi-intent case, the thread-level contract is:

```text
Turn 1:
  User asks: "Where is my order and can you cancel it?"
  Agent asks the user to choose one request.

Turn 2:
  User says: "cancel it"
  Agent may pursue cancellation only.
```

The first turn should not call tools. The second turn can call the tools needed
for cancellation because the user selected that intent. This means future
state must preserve both the pending clarification and the selected intent.

Every production trace should include a stable `thread_id` and per-turn
metadata such as `turn_id`, `run_id`, `customer_id`, `order_id`, route, risk
level, and detected intents. Without that shared thread identity, LangSmith can
show individual runs but cannot reliably evaluate conversation-level behavior.

## Routing Should Narrow Context, Not Replace Safety

Route-specific flows can help reduce prompt pollution and irrelevant tool
exposure.

Possible production-shaped routes:

```text
router
  -> order_status_flow
  -> refund_flow
  -> cancellation_flow
  -> payment_dispute_flow
  -> multi_intent_clarification
  -> fallback
```

Benefits:

- Smaller prompt per flow.
- Smaller tool surface per flow.
- Cleaner policy ownership.
- Easier eval grouping by intent.
- Less chance of cross-flow tool calls.

Risks:

- The router can misclassify.
- Multi-intent messages require special handling.
- Flow-specific prompts can drift apart.
- Global policy and mutation guardrails are still required.

Routing is a prompt/tool hygiene tool. It is not the primary safety mechanism.

## Observability Is Part Of The Product

Production agent development needs traces, metrics, and audit logs. The team
should be able to answer:

- What did the user ask?
- What route did the agent choose?
- Which tools were called?
- What did each tool return?
- Which guardrails ran?
- Which policy decision allowed or denied mutation?
- What did the customer see?
- Was there a human escalation?

Local JSONL traces are useful at the beginning. Later, use a production tracing
system such as LangSmith for LangGraph-native traces and evals, and/or
OpenTelemetry for vendor-neutral traces, metrics, and logs.

Important separation:

```text
LLM traces explain model/tool behavior.
Policy logs explain why an action was allowed or denied.
Business audit logs prove what action actually happened.
```

## Trace Sampling Is Not Error Logging

LangSmith is trace-first, not log-level-first. It records traces and spans with
inputs, outputs, metadata, tags, status, and errors. It is not a replacement for
normal application logging levels such as `debug`, `info`, `warning`, and
`error`.

`LANGSMITH_TRACING_SAMPLING_RATE=0.1` means roughly 10% of traces are recorded.
The sampling unit is usually one top-level agent invocation, such as
`graph.invoke(...)`, `agent.invoke(...)`, or this demo's `run_agent(...)`. It is
not automatically "10% of users." If the same user sends a second message later,
that second invocation can be sampled independently and may be missed even if
the first one was captured.

If we want consistent sampling by user or conversation, the application should
make that decision before calling the graph:

```text
sample by user_id    -> the same 10% of users are always traced
sample by thread_id  -> the whole conversation is traced or skipped together
sample by request    -> each agent invocation is sampled independently
```

The natural production shape is a small wrapper around the top-level agent call.
That wrapper decides whether to trace, which LangSmith project to use, and which
tags and metadata to attach. It can include environment, user/thread IDs, model
version, prompt version, route, risk flags, and whether the request is from
production, eval, canary, or local development.

The hard caveat is that ordinary sampling is decided before the run starts. If
a request is not traced and then crashes, LangSmith cannot reconstruct the full
nested trace tree after the fact. This is why error observability should not
depend only on sampled LangSmith traces.

Brief production options:

1. **Wrapper/head sampling:** decide before `graph.invoke(...)` whether to trace
   this user, thread, request, tenant, or route. This is simple and cheap, but
   it cannot know future errors.
2. **Trace more important paths up front:** trace 100% during launch, canaries,
   risky rollouts, or high-risk flows such as refund, cancellation, payment, and
   account actions.
3. **Create a forced error trace on exception:** if an unsampled request fails,
   catch the exception and create a small LangSmith trace with request metadata
   and the error summary. This preserves an error record but not the original
   missing span tree.
4. **Use OpenTelemetry tail-based sampling later:** emit spans to an OTel
   collector, let the collector see the completed trace, then keep 100% of
   errors, slow runs, guardrail blocks, or risky tool runs while sampling normal
   successes.
5. **Always log real errors separately:** send 100% of application errors to
   Sentry, Datadog, CloudWatch, ELK, or a similar logging/alerting system.
   Include request ID, thread ID, agent run ID, and LangSmith trace ID when one
   exists.

Useful rule of thumb:

```text
LangSmith = rich sampled agent behavior.
OpenTelemetry = vendor-neutral trace routing and tail-based sampling later.
App error logs = complete error coverage and alerting.
Business audit logs = authoritative record of mutations.
```

## LangSmith And OpenTelemetry Solve Different Problems

LangSmith is the best first tool for this project because it understands the
agent development loop: LangGraph traces, spans, tool calls, inputs, outputs,
metadata, evals, online evaluators, feedback, and trace inspection. It helps us
answer "what did the agent do and why did this run look wrong?"

OpenTelemetry is a broader telemetry standard. It is useful when agent traces
need to join the rest of the production stack: HTTP request traces, service
latency, queue processing, database spans, logs, metrics, alerts, and vendor
routing. OTel is also where tail-based sampling becomes natural, because a
collector can see the completed trace before deciding whether to keep it.

Tail-based sampling should not normally add customer-facing latency if telemetry
export is asynchronous. The cost is observability pipeline latency and
complexity: the collector buffers incomplete traces, waits for completion or a
timeout, then decides whether to export them. Traces may appear later in the UI,
and the collector needs memory, queues, sizing, retry policies, and monitoring.

That makes the sensible rollout:

1. **Start with LangSmith and normal error logging.** Add a top-level tracing
   wrapper, sample ordinary successful traffic, always trace risky flows when
   known up front, and log 100% of real application errors to the normal
   logging/alerting system.
2. **Add OpenTelemetry after the agent shape is stable.** Emit OTel spans and
   correlate request IDs, thread IDs, trace IDs, logs, metrics, and service
   spans, but keep sampling simple at first.
3. **Use tail-based sampling only when needed.** Introduce collector-side rules
   once volume or cost justifies the extra moving pieces: keep all errors, slow
   traces, guardrail blocks, and risky tool traces while sampling ordinary
   successes.

The main learning is sequencing. LangSmith is the quickest way to debug and
evaluate the agent. OpenTelemetry is the production integration layer once we
need cross-service observability, vendor-neutral routing, and smarter sampling.

## Practical Rules For Future Changes

When adding or changing agent behavior:

1. Decide whether the change affects LLM planning, policy decision, guardrails,
   tools, or final response wording.
2. If the behavior must be enforced, implement it outside the prompt.
3. Add or update a golden eval before relying on the behavior.
4. Check both positive and negative cases.
5. Keep mutating tools behind policy decisions and idempotency keys.
6. Avoid expanding phrase checks into a hidden second NLP system.
7. Promote real production failures into eval cases.

When changing refund behavior specifically:

1. Confirm whether the issue is extraction, policy, guardrail, or tool execution.
2. Use order details and policy signals before mutation.
3. Prefer structured policy decisions over prompt-only rules.
4. Ask for clarification only when grounding or risk policy requires it.
5. Do not confuse "we cannot prove the claim" with "we cannot auto-refund."

## Current Open Questions

- Should refund policy become a dedicated `evaluate_refund_policy` tool that
  returns a decision packet?
- Should the local eval runner track tool categories and expected route?
- When should we introduce a real router node in LangGraph?
- Should multi-intent requests always ask the user to choose, or can some
  read-only intent be handled before asking about mutation?
- Which concrete risk fields should be added first: customer refund count,
  restaurant missing-item rate, item category, or refund amount threshold?
- How should local traces map to LangSmith and OpenTelemetry fields later?

## Short Interview Summary

For a production customer-service agent, I would let the LLM understand the
conversation and propose actions, but I would not let it be the final authority
for refunds, cancellations, or payment actions. Consequential actions should go
through read-only context tools, structured policy decision tools, guardrails,
and audited mutating tools. Evals and traces should decide when architecture
needs to become more specialized, such as adding route-specific flows.
