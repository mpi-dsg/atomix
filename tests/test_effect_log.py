from __future__ import annotations

from pathlib import Path

from atomix.effects import Effect
from atomix.epoch import Epoch
from atomix.frontier import FrontierTracker
from atomix.transactions import EffectLog, TransactionManager


def test_effect_log_persists_entries(tmp_path: Path) -> None:
    log_path = tmp_path / "effects.jsonl"
    applied: list[str] = []

    def apply(effect: Effect) -> None:
        applied.append(effect.payload["value"])

    log = EffectLog(log_path)
    manager = TransactionManager(FrontierTracker(), apply, log)
    epoch = Epoch(0, "trace")
    tx = manager.begin({"res"}, epoch)
    eff = Effect(description="e1", scopes={"res"}, payload={"value": "v"}, idempotency_key="k1")
    manager.record_effect(tx, eff)
    try:
        manager.commit(tx)
    except Exception:
        pass
    manager.frontier.advance({"res"}, epoch)
    manager.flush_ready()

    contents = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert contents
    assert '"status": "committed"' in contents[-1]
    assert applied == ["v"]
