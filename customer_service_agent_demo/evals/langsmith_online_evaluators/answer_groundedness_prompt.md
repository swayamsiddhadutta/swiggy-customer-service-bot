# answer_groundedness

You are evaluating a customer-service agent trace for factual grounding.

Score whether the assistant's final answer is fully supported by the user
request and the tool results in the trace.

Return:

```json
{
  "answer_groundedness": 0_or_1,
  "reasoning": "brief reason"
}
```

Use score `1` only if every concrete claim in the final answer is supported.
Use score `0` if the assistant invents or overstates any of the following:

- order status
- ETA
- refund approval
- refund amount
- cancellation success
- escalation creation
- escalation team
- payment-dispute outcome
- policy reason code

Do not penalize harmless wording differences, empathy, or concise summaries.

Important business rules:

- If the final answer says an order was cancelled, the trace must show a
  successful `cancel_order` result.
- If the final answer says a refund was issued, the trace must show a successful
  `issue_refund` result.
- If the final answer says an escalation was created, the trace must show a
  successful `create_escalation_ticket` result.
- If the final answer gives an ETA or order status, it must come from
  `get_order_status`.
- If the final answer says the user must choose between multiple intents, it
  should not include live order facts or action results that required tools.

Trace input:

```text
{{inputs}}
```

Trace output:

```text
{{outputs}}
```
