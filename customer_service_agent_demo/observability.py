from __future__ import annotations

import os
import logging
from contextlib import contextmanager
from typing import Any, Iterator


DEFAULT_LANGSMITH_PROJECT = "customer-service-agent-demo"
_TRUE_VALUES = {"1", "true", "yes", "on"}
logger = logging.getLogger(__name__)


def _noop_traceable(*args: Any, **kwargs: Any) -> Any:
    if args and callable(args[0]) and len(args) == 1 and not kwargs:
        return args[0]

    def decorator(func: Any) -> Any:
        return func

    return decorator


try:
    from langsmith import trace as _langsmith_trace
    from langsmith import traceable as _langsmith_traceable
except ImportError:  # pragma: no cover - keeps local demo runnable without LangSmith
    _langsmith_trace = None
    _langsmith_traceable = _noop_traceable
except Exception:  # pragma: no cover - defensive import failure logging
    logger.warning("LangSmith import failed unexpectedly; tracing is disabled.", exc_info=True)
    _langsmith_trace = None
    _langsmith_traceable = _noop_traceable


traceable = _langsmith_traceable


def langsmith_tracing_enabled() -> bool:
    """Return true when a supported LangSmith/LangChain tracing flag is enabled."""
    return any(
        os.getenv(name, "").strip().lower() in _TRUE_VALUES
        for name in (
            "LANGSMITH_TRACING",
            "LANGSMITH_TRACING_V2",
            "LANGCHAIN_TRACING_V2",
        )
    )


def configure_langsmith_defaults(project_name: str | None = None) -> None:
    if not langsmith_tracing_enabled():
        return
    os.environ.setdefault("LANGSMITH_PROJECT", project_name or DEFAULT_LANGSMITH_PROJECT)


def base_trace_tags(planner: str | None = None, case_id: str | None = None) -> list[str]:
    tags = ["customer-service-demo", "langgraph"]
    if planner:
        tags.append(f"planner:{planner}")
    if case_id:
        tags.append("eval-case")
    return tags


def run_metadata(
    *,
    run_id: str,
    planner: str,
    customer_id: str,
    order_id: str | None,
    case_id: str | None,
) -> dict[str, Any]:
    return {
        "app": "customer-service-agent-demo",
        "run_id": run_id,
        "planner": planner,
        "customer_id": customer_id,
        "order_id": order_id,
        "case_id": case_id,
    }


@contextmanager
def langsmith_span(
    name: str,
    *,
    run_type: str = "chain",
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    project_name: str | None = None,
) -> Iterator[Any | None]:
    """Create a dynamic LangSmith child span when tracing is enabled."""
    if _langsmith_trace is None or not langsmith_tracing_enabled():
        yield None
        return

    configure_langsmith_defaults(project_name)
    with _langsmith_trace(
        name=name,
        run_type=run_type,
        inputs=inputs or {},
        metadata=metadata,
        tags=tags,
        project_name=project_name,
    ) as run:
        yield run


def add_span_outputs(span: Any | None, outputs: dict[str, Any]) -> None:
    if span is None:
        return
    if hasattr(span, "add_outputs"):
        span.add_outputs(outputs)
