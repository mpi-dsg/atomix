import threading
import time

from atomix.adapters import ToolAdapter
from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.runtime import AtomixRuntime
from atomix.tool_result import ToolResult


class DummyAdapter(ToolAdapter):
    name = "dummy"

    def scopes(self, args):
        return {"r1"}

    def to_effect(self, args, result: ToolResult, epoch: Epoch) -> Effect:
        return Effect(
            description="dummy",
            scopes={"r1"},
            payload=result.output,
            idempotency_key=f"{epoch.trace_id}:{epoch.value}",
        )


def test_run_tool_commits_and_advances_frontier():
    applied = []
    runtime = AtomixRuntime(apply_effect=lambda eff: applied.append(eff.payload))
    runtime.register_adapter("dummy", DummyAdapter())
    epoch = runtime.epochs.next(trace_id="trace")

    result, tx = runtime.run_tool(
        "dummy",
        lambda **kwargs: {"ok": True, **kwargs},
        {"value": 1},
        epoch,
    )

    assert result["ok"] is True
    assert tx.status == "committed"
    assert runtime.frontier.frontier_for("r1", trace_id="trace") >= epoch.value
    assert applied == [{"ok": True, "value": 1}]


def test_run_tool_with_branch_waits_for_explicit_frontier():
    applied = []
    runtime = AtomixRuntime(
        apply_effect=lambda eff: applied.append(eff.payload),
        effect_log_path=None,
    )
    runtime.register_adapter("dummy", DummyAdapter())
    epoch = runtime.epochs.next(trace_id="trace", branch_id="candidate-a")

    result, tx = runtime.run_tool(
        "dummy",
        lambda **kwargs: {"ok": True, **kwargs},
        {"value": 1},
        epoch,
    )

    assert result["ok"] is True
    assert tx.status == "waiting"
    assert applied == []

    runtime.advance_frontier(
        {"r1"}, Epoch(epoch.value, trace_id="trace", branch_id="candidate-a")
    )

    assert tx.status == "committed"
    assert applied == [{"ok": True, "value": 1}]


def test_branch_frontier_does_not_release_other_branch():
    applied = []
    runtime = AtomixRuntime(
        apply_effect=lambda eff: applied.append(eff.payload),
        effect_log_path=None,
    )
    runtime.register_adapter("dummy", DummyAdapter())
    winner_epoch = Epoch(0, trace_id="trace", branch_id="winner")
    loser_epoch = Epoch(0, trace_id="trace", branch_id="loser")

    _, winner_tx = runtime.run_tool(
        "dummy",
        lambda **kwargs: {"branch": "winner", **kwargs},
        {"value": 1},
        winner_epoch,
    )
    _, loser_tx = runtime.run_tool(
        "dummy",
        lambda **kwargs: {"branch": "loser", **kwargs},
        {"value": 2},
        loser_epoch,
    )

    runtime.advance_frontier({"r1"}, winner_epoch)

    assert winner_tx.status == "committed"
    assert loser_tx.status == "waiting"
    assert applied == [{"branch": "winner", "value": 1}]


def test_same_scope_auto_commit_preserves_epoch_order():
    applied = []
    runtime = AtomixRuntime(
        apply_effect=lambda eff: applied.append(eff.payload["value"]),
        effect_log_path=None,
    )
    runtime.register_adapter("dummy", DummyAdapter())
    epoch0 = Epoch(0, trace_id="trace")
    epoch1 = Epoch(1, trace_id="trace")
    started = threading.Event()

    def slow_tool(**kwargs):
        started.set()
        time.sleep(0.05)
        return {"value": 0}

    def fast_tool(**kwargs):
        started.wait(timeout=1)
        return {"value": 1}

    first = threading.Thread(
        target=lambda: runtime.run_tool("dummy", slow_tool, {"value": 0}, epoch0)
    )
    second = threading.Thread(
        target=lambda: runtime.run_tool("dummy", fast_tool, {"value": 1}, epoch1)
    )

    first.start()
    second.start()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert applied == [0, 1]
