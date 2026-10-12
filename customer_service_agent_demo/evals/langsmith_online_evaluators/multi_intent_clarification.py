import re


def _normalize(value):
    text = str(value or "").lower().replace("_", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _walk(value, key_names, max_depth=8):
    if max_depth < 0:
        return None
    if isinstance(value, dict):
        for key in key_names:
            if key in value:
                return value[key]
        for child in value.values():
            found = _walk(child, key_names, max_depth - 1)
            if found is not None:
                return found
    if isinstance(value, list):
        for child in value:
            found = _walk(child, key_names, max_depth - 1)
            if found is not None:
                return found
    return None


def _metadata(run):
    extra = run.get("extra") or {}
    return run.get("metadata") or extra.get("metadata") or {}


def _inputs(run):
    return run.get("inputs") or {}


def _outputs(run):
    return run.get("outputs") or {}


def _detect_intents(text):
    normalized = _normalize(text)
    intents = []
    if "where" in normalized or "status" in normalized:
        intents.append("status")
    if (
        "missing" in normalized
        or "refund" in normalized
        or "wrong" in normalized
        or "damaged" in normalized
    ):
        intents.append("refund")
    if "charged twice" in normalized or "double charged" in normalized:
        intents.append("payment_dispute")
    if "cancel" in normalized:
        intents.append("cancel")
    return intents


def _tool_sequence_from_trace(trace):
    tools = []
    if not isinstance(trace, list):
        return tools
    for event in trace:
        if not isinstance(event, dict) or event.get("node") != "act":
            continue
        output = event.get("output") or {}
        for message in output.get("messages") or []:
            if isinstance(message, dict) and message.get("tool_name"):
                tools.append(message["tool_name"])
    return tools


def _tool_sequence(run):
    outputs = _outputs(run)
    eval_signals = outputs.get("eval_signals") or {}
    if isinstance(eval_signals, dict) and isinstance(eval_signals.get("tool_sequence"), list):
        return eval_signals["tool_sequence"]
    if isinstance(outputs.get("tool_sequence"), list):
        return outputs["tool_sequence"]
    return _tool_sequence_from_trace(outputs.get("trace"))


def _final_answer(run):
    outputs = _outputs(run)
    found = _walk(outputs, ["final_answer", "answer"])
    if found is not None:
        return str(found)
    return str(outputs.get("output") or "")


def _user_message(run):
    found = _walk(_inputs(run), ["user_message", "message"])
    if found is not None:
        return str(found)
    messages = _walk(_inputs(run), ["messages"])
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "user":
                return str(message.get("content") or "")
    return ""


def _asks_user_to_choose(final_answer, intents):
    normalized_answer = _normalize(final_answer)
    clarification_markers = [
        "one request",
        "one request at a time",
        "which one",
        "which request",
        "choose",
        "handle first",
        "do you want",
    ]
    if not any(marker in normalized_answer for marker in clarification_markers):
        return False

    intent_labels = {
        "status": ["status", "where"],
        "refund": ["refund"],
        "payment_dispute": ["payment", "charge", "charged"],
        "cancel": ["cancel"],
    }
    for intent in intents:
        if not any(label in normalized_answer for label in intent_labels.get(intent, [intent])):
            return False
    return True


def perform_eval(run):
    metadata = _metadata(run)
    intents = metadata.get("detected_intents") or _detect_intents(_user_message(run))
    if not isinstance(intents, list):
        intents = []

    is_multi_intent = bool(metadata.get("multi_intent")) or len(intents) > 1
    if not is_multi_intent:
        return {"multi_intent_clarification": 1}

    tools = _tool_sequence(run)
    asks_to_choose = _asks_user_to_choose(_final_answer(run), intents)
    passed = not tools and asks_to_choose
    return {"multi_intent_clarification": 1 if passed else 0}
