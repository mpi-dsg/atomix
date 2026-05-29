"""Filesystem speculation substrate.

Branch effects write under `<root>/<branch_id>/`. On commit, the runner
moves files atomically to the global commit dir. On abort, the runner
removes the branch dir. Residue: files in the commit dir that came from
an aborted branch.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set


@dataclass
class FilesystemSubstrate:
    root: Path
    commit_dir: Path = field(init=False)
    branch_writes: Dict[str, List[Path]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.commit_dir = self.root / "_committed"
        self.commit_dir.mkdir(parents=True, exist_ok=True)

    def open_branch(self, branch_id: str) -> Path:
        bdir = self.root / branch_id
        bdir.mkdir(parents=True, exist_ok=True)
        self.branch_writes.setdefault(branch_id, [])
        return bdir

    def write(self, branch_id: str, relpath: str, content: bytes) -> Path:
        bdir = self.open_branch(branch_id)
        target = (bdir / relpath).resolve()
        branch_root = bdir.resolve()
        if not target.is_relative_to(branch_root):
            raise ValueError(f"Branch write escapes branch directory: {relpath}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        self.branch_writes[branch_id].append(target)
        return target

    def commit(self, branch_id: str) -> List[Path]:
        moved: List[Path] = []
        bdir = self.root / branch_id
        if not bdir.exists():
            return moved
        for path in self.branch_writes.get(branch_id, []):
            rel = path.relative_to(bdir)
            dest = self.commit_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Atomic move; on same filesystem this is rename(2).
            shutil.move(str(path), str(dest))
            moved.append(dest)
        # Clean up the (now empty) branch dir.
        try:
            shutil.rmtree(bdir, ignore_errors=True)
        except OSError:
            pass
        self.branch_writes.pop(branch_id, None)
        return moved

    def abort(self, branch_id: str) -> None:
        bdir = self.root / branch_id
        if bdir.exists():
            shutil.rmtree(bdir, ignore_errors=True)
        self.branch_writes.pop(branch_id, None)

    def commit_dir_files(self) -> Set[Path]:
        return {p for p in self.commit_dir.rglob("*") if p.is_file()}
