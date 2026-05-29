from __future__ import annotations

import asyncio
import builtins
import io
import sys
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

from atomix.__main__ import main
from atomix.adapters import ToolAdapter
from atomix.artifacts import Artifact
from atomix.client import AtomixClient
from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.integrations.middleware import wrap_async_middleware, wrap_middleware
from atomix.integrations.workloads.osworld import OSWorldHarness, Task
from atomix.integrations.workloads.osworld.adapters import (
    RunCommandAdapter,
    plan_run_command,
)
from atomix.integrations.workloads.swebench.harness import SwebenchHarness, SwebenchTask
from atomix.integrations.workloads.webarena.harness import (
    WebArenaHarness,
    _action_from_args,
    _resolve_webarena_root,
)
from atomix.logging import get_logger, setup_logging
from atomix.oracles import clean_success
from atomix.oracles.osworld import OSWorldEvaluationContext, take_process_snapshot
from atomix.oracles.taubench import TauBenchEvaluationContext
from atomix.oracles.webarena import WebArenaEvaluationContext
from atomix.runtime import AtomixRuntime
from atomix.sinks.smtp_sink import _LoggingHandler, _extract_body
from atomix.sinks.append_only_log import AppendOnlyLog
from atomix.sinks.smtp_sink import SMTPSink
from atomix.sinks.webhook_sink import WebhookSink
from atomix.tool_result import ToolResult

import importlib.util


RUN_WORKLOAD_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "run_workload_experiment.py"
)
workload_spec = importlib.util.spec_from_file_location(
    "run_workload_experiment", RUN_WORKLOAD_PATH
)
assert workload_spec and workload_spec.loader
run_workload_experiment = importlib.util.module_from_spec(workload_spec)
sys.modules["run_workload_experiment"] = run_workload_experiment
workload_spec.loader.exec_module(run_workload_experiment)  # type: ignore[arg-type]


class DummyAdapter(ToolAdapter):
    name = "dummy"

    def scopes(self, args):
        return {args["key"]}

    def to_effect(self, args, result: ToolResult, epoch):
        return Effect(
            description=f"dummy:{args['key']}@{epoch.value}",
            scopes={args["key"]},
            payload={"value": result.output["value"]},
            idempotency_key=f"{args['key']}:{epoch.trace_id}:{epoch.value}:{epoch.branch_id or 'main'}",
        )


def test_cli_main_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert main([]) == 2
    assert main(["version"]) == 0
    assert main(["demo"]) == 0
    assert main(["osworld", "--limit", "1"]) == 0
    assert main(["osworld", "--task", "missing"]) == 1


def test_logging_helpers_reuse_handler() -> None:
    stream = io.StringIO()
    logger = setup_logging(stream=stream)
    first_count = len(logger.handlers)
    logger = setup_logging(stream=stream)
    assert len(logger.handlers) == first_count
    assert get_logger("demo").name == "atomix.demo"
    assert get_logger("atomix.demo").name == "atomix.demo"


def test_client_and_artifact_thin_wrappers() -> None:
    applied = []
    runtime = AtomixRuntime(
        apply_effect=lambda effect: applied.append(effect.payload),
        effect_log_path=None,
    )
    client = AtomixClient(runtime)
    epoch = Epoch(0, trace_id="client")
    tx = client.begin({"scope"}, epoch)
    client.record(
        tx,
        Effect(
            description="client-effect",
            scopes={"scope"},
            payload={"ok": True},
            idempotency_key="client-key",
        ),
    )
    client.advance({"scope"}, epoch)
    client.commit(tx)

    artifact = Artifact(epoch=epoch, trace_id="client", node_id="n1", payload={"x": 1})
    assert artifact.payload == {"x": 1}
    assert applied == [{"ok": True}]
    assert client.log_entries()[0]["status"] == "committed"


def test_sync_and_async_middleware_wrappers() -> None:
    applied = []
    runtime = AtomixRuntime(
        apply_effect=lambda effect: applied.append(effect.payload["value"]),
        effect_log_path=None,
    )
    runtime.register_adapter("dummy", DummyAdapter())

    wrapped = wrap_middleware(
        runtime,
        "dummy",
        trace_fn=lambda ctx: ctx["trace_id"],
        branch_fn=lambda ctx: ctx.get("branch_id"),
    )(lambda args, ctx: {"value": args["value"]})

    assert wrapped({"key": "res", "value": "sync"}, {"trace_id": "t1"}) == {
        "value": "sync"
    }
    assert applied == ["sync"]

    # Branch executions wait until the orchestrator advances the frontier.
    assert wrapped(
        {"key": "res", "value": "branch"},
        {"trace_id": "t2", "branch_id": "candidate"},
    ) == {"value": "branch"}
    assert applied == ["sync"]
    runtime.advance_frontier({"res"}, Epoch(0, trace_id="t2", branch_id="candidate"))
    assert applied == ["sync", "branch"]

    async def run_async_case() -> None:
        async_wrapped = wrap_async_middleware(
            runtime,
            "dummy",
            trace_fn=lambda ctx: ctx["trace_id"],
        )(lambda args, ctx: asyncio.sleep(0, result={"value": args["value"]}))
        assert await async_wrapped(
            {"key": "res", "value": "async"}, {"trace_id": "t3"}
        ) == {"value": "async"}

    asyncio.run(run_async_case())
    assert applied[-1] == "async"


def test_smtp_handler_and_body_extraction(tmp_path: Path) -> None:
    msg = EmailMessage()
    msg["Subject"] = "hello"
    msg.set_content("plain body")
    assert "plain body" in _extract_body(msg)

    multipart = EmailMessage()
    multipart["Subject"] = "multi"
    multipart.set_content("plain")
    multipart.add_alternative("<p>html</p>", subtype="html")
    assert _extract_body(multipart) == "plain\n"

    log = AppendOnlyLog(tmp_path / "smtp.log")
    handler = _LoggingHandler(log)
    envelope = SimpleNamespace(
        content=b"Subject: accepted\n\nbody",
        mail_from="from@example.com",
        rcpt_tos=["to@example.com"],
    )
    status = asyncio.run(handler.handle_DATA(None, None, envelope))
    log.close()

    records = log.read_all()
    assert status.startswith("250")
    assert handler.received_count == 1
    assert records[0].payload["subject"] == "accepted"


def test_smtp_sink_missing_dependency(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("aiosmtpd"):
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    sink = SMTPSink(tmp_path / "smtp.log")
    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError, match="aiosmtpd"):
        asyncio.run(sink.start())
    assert sink.received_count == 0
    asyncio.run(sink.stop())


def test_webhook_sink_with_fake_fastapi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeApp:
        def __init__(self) -> None:
            self.routes = {}

        def post(self, path):
            def decorate(fn):
                self.routes[("POST", path)] = fn
                return fn

            return decorate

        def get(self, path):
            def decorate(fn):
                self.routes[("GET", path)] = fn
                return fn

            return decorate

    class FakeRequest:
        def __init__(self, body: bytes):
            self._body = body
            self.headers = {"x-test": "1"}

        async def body(self) -> bytes:
            return self._body

    monkeypatch.setitem(
        sys.modules,
        "fastapi",
        SimpleNamespace(FastAPI=FakeApp, Request=FakeRequest),
    )

    sink = WebhookSink(tmp_path / "webhook.log")
    app = sink.app
    receive = app.routes[("POST", "/")]
    health = app.routes[("GET", "/health")]

    assert asyncio.run(receive(FakeRequest(b'{"a": 1}'))) == {
        "status": "ok",
        "count": 1,
    }
    assert asyncio.run(receive(FakeRequest(b"[1, 2]"))) == {
        "status": "ok",
        "count": 2,
    }
    assert asyncio.run(receive(FakeRequest(b"not-json"))) == {
        "status": "ok",
        "count": 3,
    }
    assert asyncio.run(health()) == {"status": "ok", "count": 3}
    sink.close()
    assert len(sink.log.read_all()) == 3


def test_webarena_helpers_and_fake_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBARENA_DATA_DIR", str(tmp_path))
    assert _resolve_webarena_root(Path("/unused")) == tmp_path

    assert _action_from_args({"action": "click [7]"}) == "click [7]"
    assert _action_from_args({"action_type": "click", "element_id": "7"}) == "click [7]"
    assert _action_from_args({"action_type": "type", "element_id": "7", "text": "x"}) == "type [7] [x]"
    assert _action_from_args({"action_type": "hover", "element_id": "7"}) == "hover [7]"
    assert _action_from_args({"action_type": "scroll", "direction": "down"}) == "scroll [down]"
    assert _action_from_args({"action_type": "press", "key": "ENTER"}) == "press [ENTER]"
    assert _action_from_args({"action_type": "goto", "url": "https://example.com"}) == "goto [https://example.com]"
    assert _action_from_args({"action_type": "new_tab"}) == "new_tab"
    assert _action_from_args({"action_type": "close_tab"}) == "close_tab"
    assert _action_from_args({"action_type": "go_back"}) == "go_back"
    assert _action_from_args({"action_type": "go_forward"}) == "go_forward"
    assert _action_from_args({"action_type": "page_focus", "page_number": 2}) == "page_focus [2]"
    assert _action_from_args({"action_type": "stop", "answer": "done"}) == "stop [done]"
    assert _action_from_args({"action_type": "unknown"}) is None

    class FakeEnv:
        def __init__(self) -> None:
            self.actions = []
            self.closed = False

        def step(self, action):
            self.actions.append(action)
            return {"obs": True}, 0, False, False, {"info": True}

        def close(self) -> None:
            self.closed = True

    harness = WebArenaHarness()
    fake_env = FakeEnv()
    harness._env = fake_env
    harness._apply_effect(
        Effect(
            description="web",
            scopes={"browser"},
            payload={"action": {"type": "noop"}},
            idempotency_key="web-key",
        )
    )
    harness.close()

    assert fake_env.actions == [{"type": "noop"}]
    assert fake_env.closed is True
    assert harness._last_obs == {"obs": True}


def test_osworld_saga_compensates_after_later_failure(tmp_path: Path) -> None:
    harness = OSWorldHarness(work_dir=tmp_path)
    task = Task(
        id="saga-fail",
        name="Saga failure",
        description="write then fail",
        steps=[
            {"tool": "write_file", "args": {"path": "a.txt", "content": "A"}},
            {"tool": "unknown", "args": {}},
        ],
    )

    result = harness.run_baseline_saga(task)

    assert not result.success
    assert result.effects_applied == 1
    assert result.effects_compensated == 1
    assert "a.txt" not in result.final_state


def test_run_command_planning_and_swebench_harness(tmp_path: Path) -> None:
    adapter = RunCommandAdapter()
    plan = plan_run_command("touch out.txt", "out.txt", adapter)
    assert plan["command_parts"] == ["touch", "out.txt"]

    data_root = tmp_path / "data"
    repo = data_root / "repo"
    repo.mkdir(parents=True)
    task = SwebenchTask(
        id="ok",
        name="ok",
        repo_path=repo,
        patch_file=None,
        test_command="true",
    )
    assert SwebenchHarness(data_root).run(task).success is True

    quoted = SwebenchHarness(data_root).run(
        SwebenchTask(
            id="quoted",
            name="quoted",
            repo_path=repo,
            patch_file=None,
            test_command=(
                f"{sys.executable} -c \"import sys; "
                "assert sys.argv[1] == 'hello world'\" \"hello world\""
            ),
        )
    )
    assert quoted.success is True

    failed = SwebenchHarness(data_root).run(
        SwebenchTask(
            id="fail",
            name="fail",
            repo_path=repo,
            patch_file=None,
            test_command="false",
        )
    )
    assert failed.success is False

    parsed = SwebenchHarness.from_config(
        {"id": "cfg", "repo_path": "repo", "patch_file": "change.patch"},
        data_root,
    )
    assert parsed.repo_path == repo
    assert parsed.patch_file == repo / "change.patch"


def test_oracle_edge_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    web_ctx = WebArenaEvaluationContext(
        evaluation_fn=lambda: {"score": 1.0},
        dom_before={"url": {"#old": "1", "#same": "same"}},
        dom_after={"url": {"#new": "2", "#same": "changed"}},
        magento_before={"orders": {"1": {"total": 1}, "2": {"total": 2}}},
        magento_after={"orders": {"1": {"total": 3}, "3": {"total": 4}}},
        services_before={"reviews": {"count": 0}},
        services_after={"reviews": {"count": 1}},
    )
    clean, residue = clean_success("webarena", task_id="w1", ctx=web_ctx)
    assert clean is False
    assert {r.note for r in residue} >= {
        "dom_create",
        "dom_delete",
        "dom_modify",
        "db_insert",
        "db_delete",
        "db_modify",
        "service_change",
    }

    tau_ctx = TauBenchEvaluationContext(
        evaluation_fn=lambda: {"success": True},
        db_before={"orders": {"1": {"total": 1}, "2": {"total": 2}}},
        db_after={"orders": {"1": {"total": 3}, "3": {"total": 4}}},
    )
    clean, residue = clean_success("taubench", task_id="t1", ctx=tau_ctx)
    assert clean is False
    assert {r.note for r in residue} == {"update", "insert", "delete"}

    class Goal:
        success = True

    os_ctx = OSWorldEvaluationContext(evaluation_fn=lambda: Goal())
    clean, residue = clean_success("osworld", task_id="o1", ctx=os_ctx)
    assert clean is True
    assert residue == []

    def fake_run(*args, **kwargs):
        raise OSError("ps unavailable")

    monkeypatch.setattr("atomix.oracles.osworld.subprocess.run", fake_run)
    assert take_process_snapshot() == set()


def test_workload_runner_returns_failed_exit_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.json"
    output = tmp_path / "out.json"
    config.write_text(
        '{"experiment": "smoke", "workload": "osworld", "mode": "Tx-Full"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        run_workload_experiment,
        "_run_osworld",
        lambda config, output, mode: {"returncode": 7},
    )

    assert run_workload_experiment.run(config, output) is False
    assert run_workload_experiment.main.__module__ == "run_workload_experiment"
