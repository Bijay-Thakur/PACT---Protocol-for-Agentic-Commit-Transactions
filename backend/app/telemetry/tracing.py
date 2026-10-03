"""OpenTelemetry hooks.

PACT *emits* telemetry so LangSmith / Phoenix / Datadog / any OTLP backend can
consume it; it does not try to be a tracing product. Spans carry the standard
``pact.*`` attributes.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import trace

_tracer = trace.get_tracer("pact.runtime", "0.1.0")
_configured = False

log = logging.getLogger("pact")


def configure(exporter: str) -> None:
    """Install an SDK tracer provider. ``none`` keeps the no-op API provider."""
    global _configured
    if _configured or exporter == "none":
        return
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": "pact-runtime"}))
    if exporter == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif exporter == "otlp":  # pragma: no cover - optional dependency
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            log.warning("otlp exporter requested but opentelemetry-exporter-otlp is not installed")
        else:
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _configured = True


def _attrs(**kw: Any) -> dict[str, str]:
    return {f"pact.{k}": str(v) for k, v in kw.items() if v is not None}


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    """Start a span named ``pact.<name>`` with ``pact.*`` attributes.

    Accepted attribute keys: transaction_id, root_id, effect_id, operation_key,
    actor_id, state, effect_type (plus any extra context).
    """
    with _tracer.start_as_current_span(f"pact.{name}", attributes=_attrs(**attributes)) as s:
        yield s


def set_state(s: trace.Span, state: Any) -> None:
    s.set_attribute("pact.state", str(state))
