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
    from langsmith import Client as _LangSmithClient
except ImportError:  # pragma: no cover - keeps local demo runnable without LangSmith
    _langsmith_trace = None
    _langsmith_traceable = _noop_traceable
    _LangSmithClient = None
except Exception:  # pragma: no cover - defensive import failure logging
    logger.warning("LangSmith import failed unexpectedly; tracing is disabled.", exc_info=True)
    _langsmith_trace = None
    _langsmith_traceable = _noop_traceable
    _LangSmithClient = None


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
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = {
        "app": "customer-service-agent-demo",
        "run_id": run_id,
        "planner": planner,
        "customer_id": customer_id,
        "order_id": order_id,
        "case_id": case_id,
    }
    if extra:
        metadata.update(extra)
    return metadata


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


def publish_langsmith_feedback(
    *,
    trace_id: str,
    evaluations: list[dict[str, Any]],
) -> None:
    """Attach lightweight online-evaluator scores to the LangSmith trace."""
    if _LangSmithClient is None or not langsmith_tracing_enabled():
        return

    try:
        client = _LangSmithClient()
    except Exception:
        logger.warning("Could not create LangSmith client for feedback.", exc_info=True)
        return
    session_id = None
    project_name = os.getenv("LANGSMITH_PROJECT") or DEFAULT_LANGSMITH_PROJECT
    try:
        session_id = client.read_project(project_name=project_name).id
    except Exception:
        logger.warning(
            "Could not resolve LangSmith project %r for feedback session_id.",
            project_name,
            exc_info=True,
        )

    for evaluation in evaluations:
        try:
            client.create_feedback(
                trace_id=trace_id,
                key=evaluation["key"],
                score=evaluation["score"],
                value=evaluation.get("value"),
                comment=evaluation.get("comment"),
                source_info={
                    "source": "customer_service_agent_demo_online_evaluator",
                    **evaluation.get("metadata", {}),
                },
                session_id=session_id,
            )
        except TypeError:
            # Older SDKs may not accept value/source_info together.
            try:
                client.create_feedback(
                    trace_id=trace_id,
                    key=evaluation["key"],
                    score=evaluation["score"],
                    comment=evaluation.get("comment"),
                    session_id=session_id,
                )
            except Exception:
                logger.warning(
                    "Could not publish LangSmith feedback %s.",
                    evaluation.get("key"),
                    exc_info=True,
                )
        except Exception:
            logger.warning(
                "Could not publish LangSmith feedback %s.",
                evaluation.get("key"),
                exc_info=True,
            )
