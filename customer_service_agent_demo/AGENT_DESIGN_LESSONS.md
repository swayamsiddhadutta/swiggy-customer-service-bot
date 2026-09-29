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
