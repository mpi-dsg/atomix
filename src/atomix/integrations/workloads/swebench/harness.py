from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass
class SwebenchTask:
    id: str
    name: str
    repo_path: Path
    patch_file: Optional[Path]
    test_command: str


@dataclass
class SwebenchResult:
    task_id: str
    success: bool
    duration_ms: float
    error: Optional[str]


class SwebenchHarness:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root

    def run(self, task: SwebenchTask) -> SwebenchResult:
        import time

        start = time.time()
        error: Optional[str] = None
        success = False
        repo_path = task.repo_path
        try:
            if task.patch_file and task.patch_file.exists():
                subprocess.run(
                    ["git", "apply", str(task.patch_file)],
                    cwd=repo_path,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            subprocess.run(
                shlex.split(task.test_command),
                cwd=repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            success = True
        except subprocess.CalledProcessError as exc:  # noqa: BLE001
            error = exc.stderr or exc.stdout or str(exc)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        duration_ms = (time.time() - start) * 1000
        return SwebenchResult(
            task_id=task.id, success=success, duration_ms=duration_ms, error=error
        )

    @staticmethod
    def from_config(entry: Dict[str, str], data_root: Path) -> SwebenchTask:
        repo_path = Path(entry["repo_path"])
        if not repo_path.is_absolute():
            repo_path = data_root / repo_path
        patch = entry.get("patch_file")
        patch_path = Path(patch) if patch else None
        if patch_path and not patch_path.is_absolute():
            patch_path = repo_path / patch_path
        return SwebenchTask(
            id=entry["id"],
            name=entry.get("name", entry["id"]),
            repo_path=repo_path,
            patch_file=patch_path,
            test_command=entry.get("test_command", "pytest -q"),
        )
