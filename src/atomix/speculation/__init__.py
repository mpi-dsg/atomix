"""Speculation substrates and runner for E3 (A4).

Four effect classes per the paper:
  - buffered: in-memory dict; never visible after abort
  - filesystem: /tmp/atomix-spec/<branch_id>/; oracle diffs final state
  - taubench_db: per-run test DB
  - mailbox: A3 append-only sink

Each substrate exposes the same interface so the runner can sweep across
them without code changes.
"""

from .substrates.buffered import BufferedSubstrate
from .substrates.filesystem import FilesystemSubstrate
from .substrates.mailbox import MailboxSubstrate
from .substrates.taubench_db import TauBenchDBSubstrate
from .runner import SpeculationRunner, SpecBranchResult

__all__ = [
    "BufferedSubstrate",
    "FilesystemSubstrate",
    "MailboxSubstrate",
    "TauBenchDBSubstrate",
    "SpeculationRunner",
    "SpecBranchResult",
]
