"""Tests for effect reversibility taxonomy."""

import pytest
from atomix.effects import Effect, EffectReversibility
from atomix.transactions import TransactionManager, IrreversibleEffectError
from atomix.frontier import FrontierTracker
from atomix.epoch import Epoch


def test_reversible_effect_commits_without_confirmation() -> None:
    """Reversible effects commit normally."""
    frontier = FrontierTracker()
    applied = []
    tm = TransactionManager(frontier, lambda e: applied.append(e))

    tx = tm.begin({"scope"}, Epoch(1, "t1"))
    effect = Effect(
        description="test",
        scopes={"scope"},
        payload={},
        idempotency_key="k1",
        reversibility=EffectReversibility.REVERSIBLE,
    )
    tm.record_effect(tx, effect)
    frontier.advance({"scope"}, Epoch(1, "t1"))

    tm.commit(tx)  # Should succeed
    assert tx.status == "committed"


def test_irreversible_effect_requires_confirmation() -> None:
    """Irreversible effects must be confirmed before commit."""
    frontier = FrontierTracker()
    tm = TransactionManager(frontier, lambda e: None)

    tx = tm.begin({"scope"}, Epoch(1, "t1"))
    effect = Effect(
        description="send_email",
        scopes={"scope"},
        payload={"to": "user@example.com"},
        idempotency_key="k1",
        reversibility=EffectReversibility.IRREVERSIBLE,
        confirmed=False,  # Not confirmed
    )
    tm.record_effect(tx, effect)
    frontier.advance({"scope"}, Epoch(1, "t1"))

    with pytest.raises(IrreversibleEffectError) as exc_info:
        tm.commit(tx)

    assert "requires confirmation" in str(exc_info.value)
    assert tx.status == "pending"  # Not committed


def test_irreversible_effect_commits_when_confirmed() -> None:
    """Confirmed irreversible effects commit successfully."""
    frontier = FrontierTracker()
    applied = []
    tm = TransactionManager(frontier, lambda e: applied.append(e))

    tx = tm.begin({"scope"}, Epoch(1, "t1"))
    effect = Effect(
        description="send_email",
        scopes={"scope"},
        payload={"to": "user@example.com"},
        idempotency_key="k1",
        reversibility=EffectReversibility.IRREVERSIBLE,
        confirmed=True,  # Explicitly confirmed
    )
    tm.record_effect(tx, effect)
    frontier.advance({"scope"}, Epoch(1, "t1"))

    tm.commit(tx)  # Should succeed
    assert tx.status == "committed"
    assert len(applied) == 1


def test_reversible_with_cost_commits_without_confirmation() -> None:
    """Reversible-with-cost effects don't require confirmation."""
    frontier = FrontierTracker()
    applied = []
    tm = TransactionManager(frontier, lambda e: applied.append(e))

    tx = tm.begin({"scope"}, Epoch(1, "t1"))
    effect = Effect(
        description="cancel_order",
        scopes={"scope"},
        payload={"order_id": "123"},
        idempotency_key="k1",
        reversibility=EffectReversibility.REVERSIBLE_WITH_COST,
    )
    tm.record_effect(tx, effect)
    frontier.advance({"scope"}, Epoch(1, "t1"))

    tm.commit(tx)  # Should succeed (no confirmation needed)
    assert tx.status == "committed"


def test_default_reversibility_is_reversible() -> None:
    """Effects default to REVERSIBLE."""
    effect = Effect(
        description="test",
        scopes={"scope"},
        payload={},
        idempotency_key="k1",
    )
    assert effect.reversibility == EffectReversibility.REVERSIBLE
    assert effect.confirmed is False


def test_multiple_effects_mixed_reversibility() -> None:
    """Transaction with mixed reversibility effects."""
    frontier = FrontierTracker()
    applied = []
    tm = TransactionManager(frontier, lambda e: applied.append(e))

    tx = tm.begin({"scope"}, Epoch(1, "t1"))

    # Add reversible effect
    effect1 = Effect(
        description="write_file",
        scopes={"scope"},
        payload={},
        idempotency_key="k1",
        reversibility=EffectReversibility.REVERSIBLE,
    )
    tm.record_effect(tx, effect1)

    # Add unconfirmed irreversible effect
    effect2 = Effect(
        description="send_email",
        scopes={"scope"},
        payload={},
        idempotency_key="k2",
        reversibility=EffectReversibility.IRREVERSIBLE,
        confirmed=False,
    )
    tm.record_effect(tx, effect2)

    frontier.advance({"scope"}, Epoch(1, "t1"))

    # Should fail due to unconfirmed irreversible
    with pytest.raises(IrreversibleEffectError):
        tm.commit(tx)

    # Confirm and retry
    effect2.confirmed = True
    tm.commit(tx)
    assert tx.status == "committed"
    assert len(applied) == 2


def test_effect_reversibility_enum_values() -> None:
    """Test enum string values."""
    assert EffectReversibility.REVERSIBLE.value == "reversible"
    assert EffectReversibility.REVERSIBLE_WITH_COST.value == "with_cost"
    assert EffectReversibility.IRREVERSIBLE.value == "irreversible"
