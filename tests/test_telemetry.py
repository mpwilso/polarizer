"""Spans from serve (docs/EXPORT-SPEC.md, section 2): Polarizer's attributes with the ledger's
own seq and hash, nothing from arguments, results, reasons or upstream text, the SDK's server
middleware replaced with parenting kept, the switch, the console exporter on stderr, the OTLP
exporter against a local stub, and the warning when the otel extra is missing."""

import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anyio
import pytest
from helpers import rig
from helpers.fakes import FakeUpstream
from mcp import Client, MCPError
from mcp.server._otel import OpenTelemetryMiddleware
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from polarizer import config, telemetry
from polarizer.config import ToolRule

SECRET = "s3cr3t-argument-value"
FAST = {"watch_interval": 0.05, "hold_watch_interval": 0.05}
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def tracing():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, provider.get_tracer("test")


def call_spans(exporter) -> list:
    return [s for s in exporter.get_finished_spans() if s.name.startswith("tools/call")]


def everything(span) -> str:
    """All the text a span carries: its name, attributes, events, links and status."""
    return json.dumps(
        {
            "name": span.name,
            "attributes": dict(span.attributes or {}),
            "events": [(e.name, dict(e.attributes or {})) for e in span.events],
            "links": [dict(link.attributes or {}) for link in span.links],
            "status": span.status.description,
            "resource": dict(span.resource.attributes),
        },
        default=str,
    )


def polarizer_attrs(span) -> dict:
    return {k: v for k, v in (span.attributes or {}).items() if k.startswith("polarizer.")}


def setup(**classes):
    fake = FakeUpstream()
    specs = [rig.spec("f", fake.server)]
    pol = rig.held(specs, **classes)
    return fake, specs, pol


def test_forwarded_call_span_carries_the_ledger_position(tmp_path):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    exporter, tracer = tracing()

    async def main():
        async with rig.proxied(ld, specs, policy=pol, tracer=tracer) as (client, gw):
            result = await client.call_tool("f__echo", {"text": SECRET})
            return gw.session, gw.writer.chain_id, result

    session, chain_id, result = anyio.run(main)
    assert SECRET in result.content[0].text  # the result holds it; no span may
    (span,) = call_spans(exporter)
    sent = rig.kinds(ld, "call.sent")[-1]
    returned = rig.kinds(ld, "call.returned")[-1]
    assert span.name == "tools/call f__echo" and span.kind == SpanKind.SERVER
    assert span.attributes["gen_ai.tool.name"] == "f__echo"
    assert span.attributes["mcp.method.name"] == "tools/call"
    assert polarizer_attrs(span) == {
        "polarizer.session": session,
        "polarizer.chain_id": chain_id,
        "polarizer.tool": "f__echo",
        "polarizer.decision": "allow",
        "polarizer.class": "local-read",
        "polarizer.rule": sent["data"]["allowed_by"],
        "polarizer.outcome": "ok",
        "polarizer.latency_ms": returned["data"]["latency_ms"],
        "polarizer.ledger.sent_seq": sent["seq"],
        "polarizer.ledger.seq": returned["seq"],
        "polarizer.ledger.hash": returned["hash"],
    }
    assert span.end_time >= span.start_time
    for each in exporter.get_finished_spans():
        assert SECRET not in everything(each)


def test_protocol_error_span_has_no_upstream_text(tmp_path):
    fake, specs, pol = setup()
    fake.refuse_message = f"upstream says no to {SECRET}"
    ld = tmp_path / "ledger"
    exporter, tracer = tracing()

    async def main():
        async with rig.proxied(ld, specs, policy=pol, tracer=tracer) as (client, _):
            with pytest.raises(MCPError) as raised:
                await client.call_tool("f__refuse", {"q": SECRET})
            return raised.value

    error = anyio.run(main)
    assert SECRET in error.message  # the client gets the upstream's message unchanged
    (span,) = call_spans(exporter)
    assert span.status.status_code == StatusCode.ERROR
    assert span.status.description == "error -32042"
    assert span.attributes["error.type"] == "-32042"
    assert span.attributes["polarizer.outcome"] == "protocol-error"
    assert span.events == ()
    returned = rig.kinds(ld, "call.returned")[-1]
    assert span.attributes["polarizer.ledger.hash"] == returned["hash"]
    for each in exporter.get_finished_spans():
        assert SECRET not in everything(each)


def test_tool_error_span(tmp_path):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    exporter, tracer = tracing()

    async def main():
        async with rig.proxied(ld, specs, policy=pol, tracer=tracer) as (client, _):
            return await client.call_tool("f__fail", {})

    assert anyio.run(main).is_error
    (span,) = call_spans(exporter)
    assert span.attributes["error.type"] == "tool_error"
    assert span.status.status_code == StatusCode.ERROR and not span.status.description
    assert span.attributes["polarizer.outcome"] == "tool-error"


def held_call(tmp_path, decision, reason=None):
    fake, specs, pol = setup(echo="egress")
    ld = tmp_path / "ledger"
    exporter, tracer = tracing()

    async def main():
        async with rig.proxied(ld, specs, policy=pol, tracer=tracer, **FAST) as (client, _):
            async with anyio.create_task_group() as tasks:
                results = []

                async def call():
                    results.append(await client.call_tool("f__echo", {"text": SECRET}))

                tasks.start_soon(call)
                [hold] = await rig.holds_created(ld, 1)
                await anyio.to_thread.run_sync(rig.decide_hold, ld, hold, decision, reason)
                await rig.until(lambda: results, 10)
                return hold

    hold = anyio.run(main)
    return ld, exporter, hold


def test_held_then_allowed_span(tmp_path):
    ld, exporter, hold = held_call(tmp_path, "allow")
    (span,) = call_spans(exporter)
    created = rig.kinds(ld, "hold.created")[-1]
    sent = rig.kinds(ld, "call.sent")[-1]
    returned = rig.kinds(ld, "call.returned")[-1]
    attrs = polarizer_attrs(span)
    assert attrs["polarizer.decision"] == "hold"
    assert attrs["polarizer.class"] == "egress" and attrs["polarizer.rule"] == "egress"
    assert attrs["polarizer.hold"] == hold
    assert attrs["polarizer.hold.ending"] == "allow"
    assert attrs["polarizer.ledger.hold_seq"] == created["seq"]
    assert attrs["polarizer.ledger.sent_seq"] == sent["seq"]
    assert (attrs["polarizer.ledger.seq"], attrs["polarizer.ledger.hash"]) == (
        returned["seq"],
        returned["hash"],
    )
    assert attrs["polarizer.outcome"] == "ok"
    for each in exporter.get_finished_spans():
        assert SECRET not in everything(each)
        assert "held on every call" not in everything(each)  # the hold's reason


def test_held_then_denied_span(tmp_path):
    ld, exporter, hold = held_call(tmp_path, "deny", f"not {SECRET}")
    (span,) = call_spans(exporter)
    refused = rig.kinds(ld, "call.refused")[-1]
    attrs = polarizer_attrs(span)
    assert attrs["polarizer.decision"] == "hold"
    assert attrs["polarizer.hold.ending"] == "deny"
    assert (attrs["polarizer.ledger.seq"], attrs["polarizer.ledger.hash"]) == (
        refused["seq"],
        refused["hash"],
    )
    assert "polarizer.outcome" not in attrs and "polarizer.ledger.sent_seq" not in attrs
    for each in exporter.get_finished_spans():
        assert SECRET not in everything(each)


def test_deny_tier_and_unknown_name_spans(tmp_path):
    fake, specs, pol = setup()
    pol.upstreams["f"].tools["echo"] = ToolRule("local-read", deny_values=(("text", ("s3cr3t",)),))
    ld = tmp_path / "ledger"
    exporter, tracer = tracing()

    async def main():
        async with rig.proxied(ld, specs, policy=pol, tracer=tracer) as (client, _):
            await client.call_tool("f__echo", {"text": SECRET})
            await client.call_tool("f__nope", {"text": SECRET})

    anyio.run(main)
    denied, unknown = call_spans(exporter)
    first, second = rig.kinds(ld, "call.refused")
    assert polarizer_attrs(denied)["polarizer.decision"] == "deny"
    assert polarizer_attrs(denied)["polarizer.rule"] == "deny-value"
    assert polarizer_attrs(denied)["polarizer.ledger.hash"] == first["hash"]
    assert polarizer_attrs(unknown)["polarizer.decision"] == "not-routed"
    assert polarizer_attrs(unknown)["polarizer.ledger.seq"] == second["seq"]
    assert "polarizer.rule" not in polarizer_attrs(unknown)
    assert fake.calls == []
    for each in exporter.get_finished_spans():
        assert SECRET not in everything(each)
        assert "deny_values" not in everything(each)  # the deny tier's reason


def test_sdk_middleware_replaced_and_parenting_kept(tmp_path):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    exporter, tracer = tracing()

    async def main():
        async with rig.proxied(ld, specs, policy=pol, tracer=tracer) as (client, gw):
            middleware = list(gw.server.middleware)
            await client.call_tool("f__echo", {}, meta={"traceparent": TRACEPARENT})
            await client.list_tools()
            return middleware

    middleware = anyio.run(main)
    assert not any(isinstance(m, OpenTelemetryMiddleware) for m in middleware)
    assert isinstance(middleware[0], telemetry.SpanMiddleware)
    (span,) = call_spans(exporter)
    assert span.context.trace_id == 0x0AF7651916CD43DD8448EB211C80319C
    assert span.parent.span_id == 0xB7AD6B7169203331
    forwarded = fake.calls[-1][2]["traceparent"]
    assert forwarded.startswith("00-0af7651916cd43dd8448eb211c80319c-")
    listed = [s for s in exporter.get_finished_spans() if s.name == "tools/list"]
    assert listed and not polarizer_attrs(listed[0])


@pytest.mark.parametrize(
    "traces, environ, want",
    [
        ("off", {}, ((), None, [])),
        ("otlp", {}, (("otlp",), "polarizer.toml", [])),
        ("console", {"OTEL_TRACES_EXPORTER": "otlp"}, (("console",), "polarizer.toml", [])),
        ("off", {"OTEL_TRACES_EXPORTER": "otlp"}, (("otlp",), "OTEL_TRACES_EXPORTER", [])),
        (
            "off",
            {"OTEL_TRACES_EXPORTER": "console, otlp"},
            (("otlp", "console"), "OTEL_TRACES_EXPORTER", []),
        ),
        ("off", {"OTEL_TRACES_EXPORTER": "none"}, ((), None, [])),
        ("off", {"OTEL_TRACES_EXPORTER": ""}, ((), None, [])),
        (
            "off",
            {"OTEL_TRACES_EXPORTER": "zipkin,otlp"},
            (
                ("otlp",),
                "OTEL_TRACES_EXPORTER",
                [
                    "polarizer: warning: OTEL_TRACES_EXPORTER names zipkin, which Polarizer "
                    "does not export to; ignored"
                ],
            ),
        ),
        ("otlp", {"OTEL_SDK_DISABLED": "TRUE"}, ((), None, [])),
        ("off", {"OTEL_SDK_DISABLED": "true", "OTEL_TRACES_EXPORTER": "otlp"}, ((), None, [])),
        ("otlp", {"OTEL_SDK_DISABLED": "false"}, (("otlp",), "polarizer.toml", [])),
    ],
)
def test_the_switch(traces, environ, want):
    assert telemetry.choose(traces, environ) == want


@pytest.mark.parametrize(
    "toml, line",
    [
        ('telemetry = "otlp"', 'polarizer.toml: "telemetry" must be a table'),
        ('[telemetry]\nexporter = "otlp"', 'polarizer.toml: [telemetry] unknown key "exporter"'),
        (
            '[telemetry]\ntraces = "jaeger"',
            'polarizer.toml: [telemetry] traces must be "off", "otlp" or "console"',
        ),
        (
            "[telemetry]\ntraces = true",
            'polarizer.toml: [telemetry] traces must be "off", "otlp" or "console"',
        ),
    ],
)
def test_config_errors(tmp_path, toml, line):
    path = tmp_path / "polarizer.toml"
    path.write_text(f'{toml}\n\n[upstream.a]\ncommand = "x"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError) as raised:
        config.load(path)
    assert str(raised.value) == line


def test_config_default_and_value(tmp_path):
    path = tmp_path / "polarizer.toml"
    path.write_text('[upstream.a]\ncommand = "x"\n', encoding="utf-8")
    assert config.load(path).telemetry.traces == "off"
    path.write_text('[telemetry]\ntraces = "console"\n\n[upstream.a]\ncommand = "x"\n', "utf-8")
    assert config.load(path).telemetry.traces == "console"


def test_console_exporter_writes_to_stderr_only(monkeypatch):
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    provider = telemetry.build(("console",))
    with provider.get_tracer("test").start_as_current_span("tools/call probe"):
        pass
    assert provider.force_flush(5000)
    provider.shutdown()
    assert out.getvalue() == ""
    assert '"name": "tools/call probe"' in err.getvalue()
    assert '"service.name": "polarizer"' in err.getvalue()


def test_service_name_from_the_environment(monkeypatch):
    monkeypatch.setenv("OTEL_SERVICE_NAME", "my-proxy")
    provider = telemetry.build(())
    assert provider.resource.attributes["service.name"] == "my-proxy"
    monkeypatch.delenv("OTEL_SERVICE_NAME")
    provider = telemetry.build(())
    assert provider.resource.attributes["service.name"] == "polarizer"
    assert provider.resource.attributes["service.version"] == telemetry.__version__


def test_missing_extra_warns(monkeypatch):
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace", None)
    provider, lines = telemetry.start(("otlp",), "polarizer.toml", install=False)
    assert provider is None
    assert lines == [
        "polarizer: warning: span export is on (from polarizer.toml) but the otel extra is not "
        "installed; no spans are exported"
    ]


def test_start_line(monkeypatch):
    provider, lines = telemetry.start(("otlp", "console"), "OTEL_TRACES_EXPORTER", install=False)
    assert provider is not None
    assert lines == ["polarizer: exporting spans to otlp, console (from OTEL_TRACES_EXPORTER)"]
    provider.shutdown()


class _Collector(BaseHTTPRequestHandler):
    bodies: list = []

    def do_POST(self):  # noqa: N802 (http.server's name)
        length = int(self.headers.get("Content-Length", "0"))
        type(self).bodies.append(
            (self.path, self.headers.get("Content-Type"), self.rfile.read(length))
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def test_otlp_export_end_to_end(tmp_path):
    """serve over stdio with OTEL_TRACES_EXPORTER=otlp posts the call's span, with its ledger
    hash, to a local collector stub when it shuts down."""
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    _Collector.bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/traces"
        toml = (
            f"[upstream.p]\ncommand = {rig.toml_str(sys.executable)}\n"
            f"args = [{rig.toml_str(rig.PROBE)}]\n"
        )
        cfg = rig.serve_config(tmp_path, toml, tmp_path / "ledger")
        rig.prime(cfg)
        env = {"OTEL_TRACES_EXPORTER": "otlp", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": url}

        async def main():
            async with Client(rig.serve_params(cfg, env)) as client:
                await client.call_tool("p__wait", {"seconds": 0, "note": SECRET})

        anyio.run(main)
        rig.wait_for(lambda: _Collector.bodies, 30)
    finally:
        server.shutdown()
    spans, resources = [], []
    for path, content_type, body in _Collector.bodies:
        assert path == "/v1/traces" and content_type == "application/x-protobuf"
        assert SECRET.encode() not in body
        request = ExportTraceServiceRequest()
        request.ParseFromString(body)
        for rs in request.resource_spans:
            resources.append({a.key: a.value.string_value for a in rs.resource.attributes})
            for ss in rs.scope_spans:
                spans.extend(ss.spans)
    (span,) = [s for s in spans if s.name == "tools/call p__wait"]
    attrs = {a.key: a.value for a in span.attributes}
    returned = rig.kinds(tmp_path / "ledger", "call.returned")[-1]
    assert attrs["polarizer.ledger.hash"].string_value == returned["hash"]
    assert attrs["polarizer.ledger.seq"].int_value == returned["seq"]
    assert any(r.get("service.name") == "polarizer" for r in resources)
