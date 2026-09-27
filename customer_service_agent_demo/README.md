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

Run only final answers from the LangGraph version:

```bash
python3 customer_service_agent_demo/customer_service_agent_langgraph.py --reset-db --quiet
```

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
