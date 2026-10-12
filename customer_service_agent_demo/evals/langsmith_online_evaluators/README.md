# LangSmith Online Evaluators

This folder is the source-controlled home for evaluator logic that is intended
to run inside LangSmith.

LangSmith code evaluators are pasted into the LangSmith UI, so these files are
the canonical copy. When changing an evaluator, edit it here first, test it
locally or on a recent trace, then paste the updated body into LangSmith.

## Initial Evaluators To Deploy

Deploy these first:

1. `multi_intent_clarification.py`
2. `mutation_safety.py`
3. `answer_groundedness_prompt.md`

The first two are deterministic code evaluators. The third is an LLM-as-judge
prompt.

Use evaluator failures as trace-review signals: inspect the trace, add or
update a golden eval, then fix the prompt, guardrail, tool, or graph route based
on the root cause.

## Project Setup

Before adding server-side evaluators, make sure normal tracing works:

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=your_langsmith_api_key
export LANGSMITH_PROJECT=customer-service-agent-demo
```

Then run a tiny smoke trace:

```bash
python3 customer_service_agent_demo/evals/run_local_evals.py \
  --cases customer_service_agent_demo/evals/golden_cases_smoke.jsonl \
  --trace-output .context/local_eval_traces_smoke_langsmith.jsonl
```

In LangSmith, open `Tracing -> customer-service-agent-demo` and confirm the two
smoke runs appear.

## Add Code Evaluators In LangSmith

For each code evaluator:

1. Open LangSmith.
2. Go to `Tracing`.
3. Select project `customer-service-agent-demo`.
4. Open the `Evaluators` tab.
5. Click `+ Evaluator`.
6. Select `Code Evaluator`.
7. Name the evaluator using the feedback key:
   - `multi_intent_clarification`
   - `mutation_safety`
8. Set `Source` to `Runs`.
9. Add a filter:
   - app metadata equals `customer-service-agent-demo`, or
   - tag contains `customer-service-demo`.
10. Start with a narrow sampling rate while testing:
    - `1.0` for smoke/eval-case traces
    - `0.1` for general production traces
11. Paste the full contents of the matching `.py` file into the editor.
12. Use `Test Code` against a recent smoke trace.
13. Save the evaluator.

Recommended filters:

```text
multi_intent_clarification:
  metadata.multi_intent == true

mutation_safety:
  tags contains customer-service-demo
```

If the LangSmith filter builder cannot express `metadata.multi_intent == true`
for your current trace shape, leave the evaluator broad. The code returns pass
for non-multi-intent traces.

## Add LLM-As-Judge Evaluator

For `answer_groundedness_prompt.md`:

1. Open `Tracing -> customer-service-agent-demo -> Evaluators`.
2. Click `+ Evaluator`.
3. Select `LLM-as-a-Judge Evaluator`.
4. Set `Source` to `Runs`.
5. Name it `answer_groundedness`.
6. Configure a low-cost judge model first.
7. Paste the prompt from `answer_groundedness_prompt.md`.
8. Map the trace input/output variables to the prompt variables in the UI.
9. Set output schema:

```json
{
  "answer_groundedness": "number",
  "reasoning": "string"
}
```

10. Set sampling to `0.1` for normal production traffic while validating.
11. Add a weekly spend limit in the evaluator advanced settings.
12. Save and test on recent smoke traces.

## Maintenance Rule

Do not edit evaluator logic only in LangSmith. Make the change here first, then
copy it into LangSmith. Treat this folder as the reviewable source of truth.

When a production trace fails an evaluator:

1. Inspect the trace in LangSmith.
2. Decide whether the evaluator is correct.
3. If the evaluator is correct, add or update a golden JSONL case.
4. If the evaluator is wrong, fix this source-controlled evaluator file and
   update the LangSmith evaluator.

## Cost Guardrails

For the first test:

- Run only the two smoke cases.
- Use 100% sampling only on traces tagged `eval-case`.
- Use 10% sampling or less for broad production traces.
- Put a weekly spend limit on LLM-as-judge evaluators.
- Keep deterministic code evaluators separate from LLM judges so high-volume
  policy checks do not call a model.

Suggested first rollout:

```text
multi_intent_clarification:
  evaluator type: code
  sampling: 1.0 on eval-case traces, then 1.0 for metadata.multi_intent=true

mutation_safety:
  evaluator type: code
  sampling: 1.0 on eval-case traces, then 0.1-1.0 for production

answer_groundedness:
  evaluator type: LLM-as-judge
  sampling: 1.0 on eval-case traces, then 0.05-0.1 for production
  spend limit: required
```

Use your own provider key through LangSmith workspace model configuration for
server-side LLM judges, or run the judge in your own worker if you want the
OpenAI call to happen completely outside LangSmith.
