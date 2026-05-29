#!/usr/bin/env python3
"""Temporal worker for the B5 Saga port.

Run with: python scripts/temporal_saga_worker.py
Requires: a Temporal dev server reachable on localhost:7233.
"""

from __future__ import annotations

import asyncio
import random

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker


@activity.defn
async def saga_forward(abort_source: str, valid_send: bool, seed: int) -> dict:
    """Forward action: would externalize the irreversible if not vetoed."""
    rng = random.Random(seed)
    # Saga compensates after-the-fact; cannot un-send. Mirrors the
    # in-harness Saga-Compensation behavior exactly.
    if valid_send:
        externalized = True
    else:
        externalized = abort_source != "pre_commit_veto"
    return {
        "abort_source": abort_source,
        "valid_send": valid_send,
        "externalized": externalized,
        "leak": (not valid_send) and externalized,
    }


@workflow.defn
class SagaPortWorkflow:
    @workflow.run
    async def run(self, params: dict) -> dict:
        from datetime import timedelta
        return await workflow.execute_activity(
            saga_forward,
            args=[params["abort_source"], params["valid_send"], params["seed"]],
            start_to_close_timeout=timedelta(seconds=10),
        )


async def main() -> None:
    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue="atomix-b5-port",
        workflows=[SagaPortWorkflow],
        activities=[saga_forward],
    )
    print("Saga port worker listening on atomix-b5-port", flush=True)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
