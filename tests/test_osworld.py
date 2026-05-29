"""Tests for OSWorld workload."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from atomix.integrations.workloads.osworld import OSWORLD_TASKS, OSWorldHarness, Task
from atomix.integrations.workloads.osworld.real.vm_client import VMClient, VMConfig


class TestOSWorldAdapters:
    """Test OSWorld tool adapters."""

    def test_read_file_adapter_no_scopes(self) -> None:
        """Read file adapter should have empty scopes (read-only)."""
        from atomix.integrations.workloads.osworld.adapters import ReadFileAdapter
        adapter = ReadFileAdapter()
        scopes = adapter.scopes({"path": "/tmp/test.txt"})
        assert scopes == set()

    def test_write_file_adapter_scopes(self) -> None:
        """Write file adapter should scope to the file path."""
        from atomix.integrations.workloads.osworld.adapters import WriteFileAdapter
        adapter = WriteFileAdapter()
        scopes = adapter.scopes({"path": "/tmp/test.txt"})
        assert len(scopes) == 1
        assert "/tmp/test.txt" in list(scopes)[0] or "test.txt" in list(scopes)[0]

    def test_run_command_whitelist(self) -> None:
        """Run command adapter should only allow whitelisted commands."""
        from atomix.integrations.workloads.osworld.adapters import RunCommandAdapter
        adapter = RunCommandAdapter()
        assert adapter.is_allowed("mkdir foo")
        assert adapter.is_allowed("touch bar")
        assert adapter.is_allowed("cp src dst")
        assert not adapter.is_allowed("rm -rf /")
        assert not adapter.is_allowed("curl http://evil.com")
        assert not adapter.is_allowed("mkdir foo; touch bar")

    def test_run_command_rejects_shell_operators(self) -> None:
        """run_command should reject shell operators."""
        from atomix.integrations.workloads.osworld.adapters import (
            RunCommandAdapter,
            run_command,
        )

        adapter = RunCommandAdapter()
        with pytest.raises(PermissionError):
            run_command("mkdir foo; touch bar", ["foo"], adapter)


class TestOSWorldHarness:
    """Test OSWorld harness functionality."""

    def test_simple_task_atomix(self, tmp_path: Path) -> None:
        """Simple task should succeed with Atomix."""
        harness = OSWorldHarness(work_dir=tmp_path)
        task = Task(
            id="test-001",
            name="Test task",
            description="Simple test",
            steps=[
                {"tool": "write_file", "args": {"path": "test.txt", "content": "hello\n"}},
            ],
            verify=lambda d: (d / "test.txt").read_text() == "hello\n",
        )
        result = harness.run_atomix(task)
        assert result.success
        assert result.effects_applied >= 1

    def test_simple_task_baseline(self, tmp_path: Path) -> None:
        """Simple task should succeed with baseline."""
        harness = OSWorldHarness(work_dir=tmp_path)
        task = Task(
            id="test-002",
            name="Test task",
            description="Simple test",
            steps=[
                {"tool": "write_file", "args": {"path": "test.txt", "content": "hello\n"}},
            ],
            verify=lambda d: (d / "test.txt").read_text() == "hello\n",
        )
        result = harness.run_baseline(task)
        assert result.success
        assert result.effects_applied >= 1

    def test_multi_step_task(self, tmp_path: Path) -> None:
        """Multi-step task should complete all steps."""
        harness = OSWorldHarness(work_dir=tmp_path)
        task = Task(
            id="test-003",
            name="Multi-step",
            description="Multiple files",
            steps=[
                {"tool": "write_file", "args": {"path": "a.txt", "content": "A\n"}},
                {"tool": "write_file", "args": {"path": "b.txt", "content": "B\n"}},
                {"tool": "write_file", "args": {"path": "c.txt", "content": "C\n"}},
            ],
        )
        result = harness.run_atomix(task)
        assert result.success
        assert "a.txt" in result.final_state
        assert "b.txt" in result.final_state
        assert "c.txt" in result.final_state

    def test_compare_results(self, tmp_path: Path) -> None:
        """Comparison should capture key metrics."""
        harness = OSWorldHarness(work_dir=tmp_path)
        task = Task(
            id="test-004",
            name="Compare test",
            description="For comparison",
            steps=[
                {"tool": "write_file", "args": {"path": "file.txt", "content": "content\n"}},
            ],
        )
        result = harness.run_task(task)
        comparison = result["comparison"]
        assert "atomix_success" in comparison
        assert "baseline_success" in comparison
        assert "atomix_effects" in comparison

    @pytest.mark.parametrize("runner", ["run_atomix", "run_baseline"])
    def test_task_paths_cannot_escape_work_dir(
        self, tmp_path: Path, runner: str
    ) -> None:
        """Task-defined paths must stay inside the task sandbox."""
        harness = OSWorldHarness(work_dir=tmp_path)
        task = Task(
            id="escape",
            name="Escape attempt",
            description="Path traversal should be rejected",
            steps=[
                {
                    "tool": "write_file",
                    "args": {"path": "../escape.txt", "content": "nope"},
                },
            ],
        )

        result = getattr(harness, runner)(task)

        assert not result.success
        assert "escapes task directory" in (result.error or "")
        assert not (tmp_path / "escape.txt").exists()

    def test_run_command_paths_are_rewritten_into_work_dir(
        self, tmp_path: Path
    ) -> None:
        harness = OSWorldHarness(work_dir=tmp_path)
        task = Task(
            id="cmd",
            name="Command task",
            description="Command paths should execute inside the task sandbox",
            steps=[
                {
                    "tool": "run_command",
                    "args": {"command": "touch out.txt", "targets": ["out.txt"]},
                },
            ],
        )

        result = harness.run_atomix(task)

        assert result.success, result.error
        assert "out.txt" in result.final_state
        assert not (Path.cwd() / "out.txt").exists()


class TestOSWorldTasks:
    """Test the predefined OSWorld tasks."""

    def test_tasks_are_defined(self) -> None:
        """Should have defined tasks."""
        assert len(OSWORLD_TASKS) >= 10

    def test_all_tasks_have_required_fields(self) -> None:
        """All tasks should have required fields."""
        for task in OSWORLD_TASKS:
            assert task.id
            assert task.name
            assert task.description
            assert len(task.steps) > 0

    def test_first_task_runs_successfully(self, tmp_path: Path) -> None:
        """First task should run successfully."""
        harness = OSWorldHarness(work_dir=tmp_path)
        task = OSWORLD_TASKS[0]
        result = harness.run_atomix(task)
        assert result.success, f"Task {task.id} failed: {result.error}"

    @pytest.mark.parametrize("task_idx", range(min(5, len(OSWORLD_TASKS))))
    def test_first_five_tasks(self, tmp_path: Path, task_idx: int) -> None:
        """First 5 tasks should all run successfully."""
        harness = OSWorldHarness(work_dir=tmp_path)
        task = OSWORLD_TASKS[task_idx]
        result = harness.run_atomix(task)
        assert result.success, f"Task {task.id} failed: {result.error}"


class CapturingVMClient(VMClient):
    """VMClient test double that captures execute payloads."""

    def __init__(self) -> None:
        self.config = VMConfig()
        self.base_url = "mock://vm"
        self.payload: dict[str, Any] | None = None

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.payload = payload
        return {"status": "success", "output": "ok"}


class TestOSWorldVMClient:
    def test_execute_command_quotes_python_payload(self) -> None:
        client = CapturingVMClient()

        result = client.execute_command("echo \"it's ok\"")

        assert result.success
        assert client.payload is not None
        python_code = client.payload["command"][2]
        compile(python_code, "<vm-command>", "exec")
        assert "echo" in python_code

    def test_read_file_quotes_shell_path(self) -> None:
        client = CapturingVMClient()

        assert client.read_file("/tmp/a'b.txt") == "ok"

        assert client.payload is not None
        python_code = client.payload["command"][2]
        compile(python_code, "<vm-command>", "exec")
        assert "/tmp/a" in python_code
