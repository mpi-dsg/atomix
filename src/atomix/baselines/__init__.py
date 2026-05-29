"""Mechanism baselines for E2/E4 (A1).

Each baseline implements `BaselineProtocol`: begin/record/commit/abort
with the same surface as `TransactionManager`, so harnesses can swap them
in without changes elsewhere.

Baselines:
- `MutexWalRollback`: per-resource mutex + WAL of operations + reverse-DAG rollback
- `TCCConfirm`: two-phase try/confirm/cancel
- `OCCRevalidateRetry`: version-stamp on read, abort+revalidate+retry on stale
"""

from .mutex_wal_rollback import MutexWalRollback
from .tcc_confirm import TCCConfirm
from .occ_revalidate_retry import OCCRevalidateRetry
from .protocol import BaselineProtocol

__all__ = [
    "BaselineProtocol",
    "MutexWalRollback",
    "TCCConfirm",
    "OCCRevalidateRetry",
]
