from __future__ import annotations

import asyncio
import random
import threading
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, TypeVar


T = TypeVar("T")


# F-class taxonomy (paper §5.0 fault model):
#   F1 pre-execution    — exception before any side effect
#   F2 post-effect/pre-return — effect externalized, then tool failed to return
#   F3 in-compensation  — forward succeeded, cancel handler fails (handled at TM level)
#   F4 duplicate-delivery — tool actually ran twice
#   F5 timeout/hang      — exceeded budget while side effect may have escaped
F_CLASSES = ("F1", "F2", "F3", "F4", "F5")


@dataclass
class FaultProfile:
    """Configurable fault profile."""

    duplicate_probability: float = 0.0
    exception_probability: float = 0.0
    min_delay_s: float = 0.0
    max_delay_s: float = 0.0
    # Probability split: of the exception_probability mass, how much is F1
    # (pre-execution, no side effect) vs F2 (post-effect / pre-return).
    # 0.5 = half the exceptions fire before the tool function runs, half after.
    f2_share_of_exception: float = 0.5
    # Timeout fault rate (F5): if delay > timeout_threshold_s and timeout_fire
    # is set, surface as F5.
    timeout_threshold_s: float = 0.0
    timeout_fire_probability: float = 0.0
    seed: int | None = None


@dataclass
class FaultEvent:
    """One injection record. Captured for the F1–F5 breakdown table."""

    f_class: str  # one of F_CLASSES
    delay_s: float = 0.0


class FaultInjector:
    """Lightweight fault injector for tool calls.

    Captures per-call F-class events. Read via `events_drain()` or
    `last_event` (None if the call had no fault). The runner harvests
    these between transactions to populate the F1-F5 breakdown.
    """

    def __init__(self, profile: FaultProfile | None = None) -> None:
        self.profile = profile or FaultProfile()
        self._rng = random.Random(self.profile.seed)
        self._events: List[FaultEvent] = []
        self._lock = threading.Lock()
        self.last_event: Optional[FaultEvent] = None

    def _record(self, f_class: str, delay_s: float = 0.0) -> FaultEvent:
        ev = FaultEvent(f_class=f_class, delay_s=delay_s)
        with self._lock:
            self._events.append(ev)
            self.last_event = ev
        return ev

    def events_drain(self) -> List[FaultEvent]:
        with self._lock:
            out = list(self._events)
            self._events.clear()
            return out

    def call(self, fn: Callable[[], T]) -> T:
        p = self.profile
        self.last_event = None
        delay = 0.0
        if p.max_delay_s > 0:
            delay = self._rng.uniform(p.min_delay_s, p.max_delay_s)
            time.sleep(delay)
            if (
                p.timeout_threshold_s > 0
                and delay >= p.timeout_threshold_s
                and self._rng.random() < p.timeout_fire_probability
            ):
                self._record("F5", delay_s=delay)
                raise RuntimeError("Injected fault: timeout (F5)")
        if self._rng.random() < p.exception_probability:
            # F1 vs F2 split. F1 fires before the tool runs.
            if self._rng.random() >= p.f2_share_of_exception:
                self._record("F1", delay_s=delay)
                raise RuntimeError("Injected fault: pre-execution (F1)")
            # F2: run the tool, then raise after the side effect.
            result = fn()
            self._record("F2", delay_s=delay)
            raise RuntimeError("Injected fault: post-effect/pre-return (F2)")
        result = fn()
        if self._rng.random() < p.duplicate_probability:
            fn()
            self._record("F4", delay_s=delay)
        return result

    async def call_async(self, fn: Callable[[], Awaitable[T]]) -> T:
        p = self.profile
        self.last_event = None
        delay = 0.0
        if p.max_delay_s > 0:
            delay = self._rng.uniform(p.min_delay_s, p.max_delay_s)
            await asyncio.sleep(delay)
            if (
                p.timeout_threshold_s > 0
                and delay >= p.timeout_threshold_s
                and self._rng.random() < p.timeout_fire_probability
            ):
                self._record("F5", delay_s=delay)
                raise RuntimeError("Injected fault: timeout (F5)")
        if self._rng.random() < p.exception_probability:
            if self._rng.random() >= p.f2_share_of_exception:
                self._record("F1", delay_s=delay)
                raise RuntimeError("Injected fault: pre-execution (F1)")
            result = await fn()
            self._record("F2", delay_s=delay)
            raise RuntimeError("Injected fault: post-effect/pre-return (F2)")
        result = await fn()
        if self._rng.random() < p.duplicate_probability:
            await fn()
            self._record("F4", delay_s=delay)
        return result
