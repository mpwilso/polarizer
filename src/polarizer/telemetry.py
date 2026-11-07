"""Spans from serve (docs/EXPORT-SPEC.md, section 2).

SpanMiddleware replaces the MCP SDK's own OpenTelemetryMiddleware in serve's server: the same
span name, kind, attributes and parenting from the request's _meta, but a failure's status
never carries the exception's or the upstream's text, and no exception event is recorded.
note() adds Polarizer's attributes to the current span as a call goes; none holds an argument,
a result or a reason.

Export is off by default. choose() reads the config's [telemetry] traces and the standard
OTEL_SDK_DISABLED and OTEL_TRACES_EXPORTER variables; start() builds the SDK's tracer provider,
from the optional otel extra, and installs it as the global one, so the SDK's own client spans
go with Polarizer's. flush() gives it a bounded time at shutdown.
"""

import os
import sys

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from polarizer import __version__

TRACES = ("off", "otlp", "console")
EXPORTERS = ("otlp", "console")  # the order the start line names them in
FLUSH_BOUND_MS = 1000
CONFIG = "polarizer.toml"
ENV = "OTEL_TRACES_EXPORTER"
UNKNOWN = (
    "polarizer: warning: OTEL_TRACES_EXPORTER names {}, which Polarizer does not export to; ignored"
)
NO_EXTRA = (
    "polarizer: warning: span export is on (from {}) but the otel extra is not installed; "
    "no spans are exported"
)
INVALID_PARAMS = -32602
_provider = None  # the provider start() installed, for flush()


def choose(traces: str, environ) -> tuple[tuple[str, ...], str | None, list[str]]:
    """(exporters, where the choice came from, warning lines). OTEL_SDK_DISABLED=true turns
    export off; a config value other than "off" wins over OTEL_TRACES_EXPORTER."""
    if environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return (), None, []
    if traces != "off":
        return (traces,), CONFIG, []
    names, warnings = set(), []
    for item in environ.get(ENV, "").split(","):
        item = item.strip().lower()
        if item in EXPORTERS:
            names.add(item)
        elif item and item != "none":
            warnings.append(UNKNOWN.format(item))
    exporters = tuple(name for name in EXPORTERS if name in names)
    return exporters, (ENV if exporters else None), warnings


def build(exporters: tuple[str, ...]):
    """The SDK's tracer provider with one batch processor per exporter. Raises ImportError
    without the otel extra."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    attributes = {"service.version": __version__}
    if not os.environ.get("OTEL_SERVICE_NAME") and "service.name=" not in os.environ.get(
        "OTEL_RESOURCE_ATTRIBUTES", ""
    ):
        attributes["service.name"] = "polarizer"
    provider = TracerProvider(resource=Resource.create(attributes))
    for name in exporters:
        if name == "otlp":
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            exporter = OTLPSpanExporter()
        else:
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter

            exporter = ConsoleSpanExporter(out=sys.stderr)  # stdout is the protocol channel
        provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def start(exporters: tuple[str, ...], source: str, install: bool = True):
    """(provider or None, the stderr lines to write). With `install`, the provider becomes the
    global one and flush() flushes it."""
    global _provider
    try:
        provider = build(exporters)
    except ImportError:
        return None, [NO_EXTRA.format(source)]
    if install:
        trace.set_tracer_provider(provider)
        _provider = provider
    return provider, [f"polarizer: exporting spans to {', '.join(exporters)} (from {source})"]


def flush(bound_ms: int = FLUSH_BOUND_MS) -> None:
    """Flush the installed provider for at most `bound_ms`; a collector that is down costs at
    most that."""
    if _provider is not None:
        try:
            _provider.force_flush(bound_ms)
        except Exception:
            pass


def note(attributes: dict) -> None:
    """Set Polarizer's attributes on the current span; a None value is left out. Outside a
    request (startup, the watch, planted calls) there is no recording span, and nothing
    happens."""
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attributes({k: v for k, v in attributes.items() if v is not None})


def _parent(meta):
    """The request's trace context from its _meta, as the SDK reads it, or None."""
    from mcp.shared._otel import extract_trace_context

    return extract_trace_context(meta)


class SpanMiddleware:
    """The SDK's server span with Polarizer's failure rule. `tracer` is for tests; serve uses
    the global provider's."""

    def __init__(self, tracer=None):
        self._tracer = tracer

    @property
    def tracer(self):
        return (
            self._tracer if self._tracer is not None else trace.get_tracer("polarizer", __version__)
        )

    async def __call__(self, ctx, call_next):
        import pydantic
        from mcp import MCPError
        from mcp_types import CallToolResult

        name = ctx.params.get("name") if ctx.params else None
        target = name if isinstance(name, str) else None
        attributes = {"mcp.method.name": ctx.method}
        if ctx.protocol_version is not None:
            attributes["mcp.protocol.version"] = ctx.protocol_version
        if ctx.request_id is not None:
            attributes["jsonrpc.request.id"] = str(ctx.request_id)
        if ctx.method == "tools/call":
            attributes["gen_ai.operation.name"] = "execute_tool"
            if target is not None:
                attributes["gen_ai.tool.name"] = target
        elif ctx.method == "prompts/get" and target is not None:
            attributes["gen_ai.prompt.name"] = target
        with self.tracer.start_as_current_span(
            f"{ctx.method} {target}" if target else ctx.method,
            kind=SpanKind.SERVER,
            attributes=attributes,
            context=_parent(ctx.meta),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                result = await call_next(ctx)
            except MCPError as e:
                code = str(e.code)
                span.set_attributes({"error.type": code, "rpc.response.status_code": code})
                span.set_status(Status(StatusCode.ERROR, f"error {code}"))
                raise
            except pydantic.ValidationError:
                code = str(INVALID_PARAMS)
                span.set_attributes({"error.type": code, "rpc.response.status_code": code})
                span.set_status(Status(StatusCode.ERROR, "Invalid request parameters"))
                raise
            except Exception as e:
                span.set_attribute("error.type", type(e).__qualname__)
                span.set_status(Status(StatusCode.ERROR))
                raise
            if ctx.method == "tools/call":
                is_error = isinstance(result, CallToolResult) and result.is_error is True
                if is_error or (isinstance(result, dict) and result.get("isError") is True):
                    span.set_attribute("error.type", "tool_error")
                    span.set_status(Status(StatusCode.ERROR))
            return result


def install(server, tracer=None) -> SpanMiddleware:
    """Replace the SDK's OpenTelemetryMiddleware in `server` with a SpanMiddleware, first in
    line, as the SDK's was."""
    from mcp.server._otel import OpenTelemetryMiddleware

    middleware = SpanMiddleware(tracer)
    kept = [m for m in server.middleware if not isinstance(m, OpenTelemetryMiddleware)]
    server.middleware[:] = [middleware, *kept]
    return middleware
