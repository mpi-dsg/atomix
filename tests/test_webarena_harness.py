"""Tests for the WebArena harness (synthetic mode)."""

from __future__ import annotations

from atomix.integrations.workloads.webarena import WebArenaHarness, WebArenaTask


def test_webarena_harness_applies_effects() -> None:
    harness = WebArenaHarness(use_real_env=False)
    task = WebArenaTask(
        id="wa-1",
        name="demo",
        steps=[
            {
                "args": {
                    "action_type": "click",
                    "site": "example",
                    "url": "http://example.com",
                    "element_id": "e1",
                }
            },
            {
                "args": {
                    "action_type": "type",
                    "site": "example",
                    "url": "http://example.com",
                    "element_id": "e2",
                    "text": "hello",
                }
            },
        ],
    )

    result = harness.run_atomix(task)
    assert result["success"]
    assert result["effects_applied"] == 2
