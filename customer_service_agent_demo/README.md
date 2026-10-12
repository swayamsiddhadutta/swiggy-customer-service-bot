# Customer Service Agent Demo

This is a local, dependency-free teaching version of the customer-service agent
from the interview discussion.

It shows:

- `reason_node`: the "Reason" phase.
- `act_node`: the "Act" phase.
- `merge_state_update`: the "Observe" phase where node return values update state.
- SQLite tools for orders, payments, refunds, escalations, and audit logs.
- Guardrails for tool allowlisting, risky mutations, idempotency, refund policy,
  escalation evidence, and max loop steps.
- Five simulated scenarios.

See `PRODUCTION_AGENT_PLAN.md` for the production roadmap covering policy
tools, RAG, guardrails, human review, observability, and evals.

Run it:

```bash
python3 customer_service_agent_demo/customer_service_agent.py --reset-db
```

Run only final answers:

```bash
python3 customer_service_agent_demo/customer_service_agent.py --reset-db --quiet
```

Run the LangGraph version:

```bash
python3 -m pip install -r customer_service_agent_demo/requirements.txt
python3 customer_service_agent_demo/customer_service_agent_langgraph.py --reset-db
```

Or use the included setup helper which creates a virtualenv and installs
dependencies:

```bash
python3 customer_service_agent_demo/setup_env.py
source .venv/bin/activate
python3 customer_service_agent_demo/customer_service_agent_langgraph.py --reset-db
```

Run only final answers from the LangGraph version:

```bash
python3 customer_service_agent_demo/customer_service_agent_langgraph.py --reset-db --quiet
```

## LangSmith Observability

This branch instruments the LangGraph demo so each agent run can show up in
LangSmith as a top-level trace with child spans for:

- the graph run (`customer_service_agent`)
- deterministic planner decisions
- refund item resolution
- tool-call normalization
- guardrail checks
- backend tool execution
- OpenAI-compatible LLM calls when `--planner llm` is used

Install the observability dependencies:

```bash
python3 -m pip install -r customer_service_agent_demo/requirements.txt
```

Then enable LangSmith tracing:

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=your_langsmith_api_key
export LANGSMITH_PROJECT=customer-service-agent-demo
```

Run one deterministic scenario:

```bash
python3 customer_service_agent_demo/customer_service_agent_langgraph.py \
  --reset-db \
  --scenario 1 \
  --quiet
```

Open LangSmith, go to `Tracing`, select the `customer-service-agent-demo`
project, and click the latest `customer_service_agent` run. The trace tree
should show the graph run, node execution, planner spans, guardrail spans, and
tool spans.

Run golden-case evals with the same tracing enabled:

```bash
python3 customer_service_agent_demo/evals/run_local_evals.py \
  --trace-output .context/langgraph_run_traces.jsonl
```

Use the two-case smoke file when tracing real LLM evals to keep token usage low:

```bash
python3 customer_service_agent_demo/evals/run_local_evals.py \
  --planner llm \
  --cases customer_service_agent_demo/evals/golden_cases_smoke.jsonl \
  --trace-output .context/langgraph_run_traces_smoke.jsonl
```

Eval traces include the `eval-case` tag and the case ID in metadata.
When LangSmith tracing is enabled, each run also gets online-evaluator feedback
keys such as `multi_intent_clarification`, `mutation_safety`,
`tool_execution_health`, and aggregate `agent_policy`. Filter for
`agent_policy = 0` in LangSmith to find policy failures quickly.

### LangGraph Studio / Debugger

This repo includes a root `langgraph.json` that points Studio at the compiled
graph named `customer_service_agent`.

Start the local LangGraph dev server:

```bash
langgraph dev
```

The CLI prints local API, docs, and Studio URLs. Open the Studio URL to inspect
the graph visually and step through runs locally. With the LangSmith environment
variables above enabled, the same executions should also appear in LangSmith.

Show the standard real LLM setup:

```bash
python3 customer_service_agent_demo/customer_service_agent.py --llm-note --quiet
```

## Where the `llm` variable goes

In a real LangGraph/LangChain version, the deterministic `SimulatedLLMReasoner`
would be replaced by a real chat model with tools bound to it:

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="gpt-4.1-mini",
    temperature=0,
)

llm_with_tools = llm.bind_tools(tools)
```

Then the Reason node would call:

```python
response = llm_with_tools.invoke(state["messages"])
return {"messages": [response]}
```

That returned dictionary is a partial state update. In LangGraph, the reducer for
`messages` appends/merges the returned message into the existing state.

## Why this demo does not call a real LLM

The purpose is to make the loop, state updates, function inputs/outputs, and
guardrails inspectable. A real LLM would make the tool choices dynamically, but
that requires provider credentials and installed LangChain/LangGraph packages.

## LangGraph Version

`customer_service_agent_langgraph.py` implements the same five scenarios with
`StateGraph`.

The mapping is:

```text
Plain Python demo        LangGraph demo
run_agent while-loop     StateGraph edges
reason_node              graph node: reason
act_node                 graph node: act
merge_state_update       LangGraph reducers
if pending_tool_call     conditional edge reason -> act/end
```

The LangGraph demo still uses deterministic local planning instead of a real
LLM so it can run without an API key.

## Real LLM Mode

The LangGraph demo has two planner modes:

```text
deterministic  local rule-based planner for repeatable tests
llm            real OpenAI-compatible tool-calling model
```

The production-shaped design is:

```text
LangGraph graph
  reason node
    deterministic planner OR OpenAI-compatible LLM gateway
  act node
    normalizes system-owned fields
    enforces guardrails
    executes backend tools

Swiggy-like SQLite backend
  customers
  customer_addresses
  restaurants
  menu_items
  delivery_partners
  orders
  order_items
  order_status_events
  payments
  support_contacts
  refunds
  escalation_tickets
  audit_log

Provider gateway
  xAI / Grok directly
  OpenRouter
  LiteLLM proxy
  any OpenAI-compatible endpoint
```

The key production idea is that provider replacement should happen in config,
not across the graph and tools. The code uses `llm_gateway.py` as a small
OpenAI-compatible adapter so the graph does not care whether the model is Grok,
OpenRouter, LiteLLM, or another compatible endpoint.

### xAI / Grok

```bash
export AGENT_LLM_PROVIDER=xai
export AGENT_LLM_MODEL=grok-4.7
export XAI_API_KEY=your_xai_key

python3 customer_service_agent_demo/customer_service_agent_langgraph.py \
  --reset-db \
  --planner llm \
  --scenario 1
```

### OpenRouter

Use this when you want one hosted API key for many model providers and are okay
with an external gateway.

```bash
export AGENT_LLM_PROVIDER=openrouter
export AGENT_LLM_MODEL=replace-with-openrouter-model-slug
export OPENROUTER_API_KEY=your_openrouter_key

python3 customer_service_agent_demo/customer_service_agent_langgraph.py \
  --reset-db \
  --planner llm \
  --scenario 1
```

### LiteLLM

Use this when you want a company-style internal gateway with model aliases,
fallbacks, budgets, logging, and one OpenAI-compatible endpoint for apps.

```bash
export AGENT_LLM_PROVIDER=litellm
export AGENT_LLM_BASE_URL=http://localhost:4000/v1
export AGENT_LLM_MODEL=swiggy-support-primary
export LITELLM_API_KEY=your_litellm_proxy_key

python3 customer_service_agent_demo/customer_service_agent_langgraph.py \
  --reset-db \
  --planner llm \
  --scenario 1
```

### Generic OpenAI-Compatible Provider

```bash
export AGENT_LLM_PROVIDER=openai-compatible
export AGENT_LLM_BASE_URL=https://provider.example.com/v1
export AGENT_LLM_MODEL=provider-model-name
export AGENT_LLM_API_KEY_ENV=LLM_API_KEY
export LLM_API_KEY=your_provider_key

python3 customer_service_agent_demo/customer_service_agent_langgraph.py \
  --reset-db \
  --planner llm \
  --scenario 1
```

You can also run a custom request:

```bash
python3 customer_service_agent_demo/customer_service_agent_langgraph.py \
  --planner llm \
  --customer-id CUST-2 \
  --order-id ORD-202 \
  --message "My coke was missing. Please refund it."
```
