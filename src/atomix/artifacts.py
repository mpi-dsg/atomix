from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .epoch import Epoch


@dataclass
class Artifact:
    """Payload plus metadata used to align orchestrator events with frontiers."""

    epoch: Epoch
    trace_id: str
    node_id: str
    payload: Any
