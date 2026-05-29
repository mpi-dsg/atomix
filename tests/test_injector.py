from __future__ import annotations

from atomix.injector import FaultInjector, FaultProfile


def test_fault_injector_seed_replays_fault_sequence() -> None:
    def run_sequence(seed: int) -> list[str]:
        injector = FaultInjector(
            FaultProfile(
                duplicate_probability=0.2,
                exception_probability=0.5,
                f2_share_of_exception=0.4,
                seed=seed,
            )
        )
        outcomes: list[str] = []

        for _ in range(30):
            try:
                injector.call(lambda: "ok")
            except RuntimeError:
                pass
            outcomes.append(injector.last_event.f_class if injector.last_event else "ok")

        return outcomes

    assert run_sequence(1234) == run_sequence(1234)
