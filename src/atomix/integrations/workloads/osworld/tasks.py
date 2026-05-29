"""
OSWorld task definitions.

Defines 10-15 Ubuntu/filesystem tasks for evaluating Atomix transactional
semantics. Tasks range from simple file operations to multi-step workflows.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from .harness import Task


def _setup_config_file(task_dir: Path) -> None:
    """Setup a config file for editing tasks."""
    config = task_dir / "config.ini"
    config.write_text(
        "[database]\nhost=localhost\nport=5432\nname=mydb\n\n[server]\nport=8080\n",
        encoding="utf-8",
    )


def _setup_log_file(task_dir: Path) -> None:
    """Setup a log file for append tasks."""
    log = task_dir / "app.log"
    log.write_text("2024-01-01 00:00:00 INFO Application started\n", encoding="utf-8")


def _setup_project_structure(task_dir: Path) -> None:
    """Setup a basic project structure."""
    (task_dir / "src").mkdir()
    (task_dir / "src" / "main.py").write_text(
        "def main():\n    print('Hello')\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    (task_dir / "README.md").write_text("# My Project\n\nA sample project.\n", encoding="utf-8")


def _setup_multi_file(task_dir: Path) -> None:
    """Setup multiple related files."""
    (task_dir / "data").mkdir()
    (task_dir / "data" / "users.json").write_text('{"users": []}\n', encoding="utf-8")
    (task_dir / "data" / "config.json").write_text('{"version": 1}\n', encoding="utf-8")


def _verify_file_content(task_dir: Path, path: str, expected: str) -> bool:
    """Verify a file contains expected content."""
    p = task_dir / path
    if not p.exists():
        return False
    return p.read_text(encoding="utf-8") == expected


# Task definitions

OSWORLD_TASKS: List[Task] = [
    # Task 1: Simple file creation
    Task(
        id="osw-001",
        name="Create single file",
        description="Create a new file with specific content",
        steps=[
            {"tool": "write_file", "args": {"path": "hello.txt", "content": "Hello, World!\n"}},
        ],
        verify=lambda d: _verify_file_content(d, "hello.txt", "Hello, World!\n"),
    ),

    # Task 2: Multi-step file creation
    Task(
        id="osw-002",
        name="Create multiple files",
        description="Create multiple related files in sequence",
        steps=[
            {"tool": "write_file", "args": {"path": "file1.txt", "content": "File 1 content\n"}},
            {"tool": "write_file", "args": {"path": "file2.txt", "content": "File 2 content\n"}},
            {"tool": "write_file", "args": {"path": "file3.txt", "content": "File 3 content\n"}},
        ],
        verify=lambda d: all(
            _verify_file_content(d, f"file{i}.txt", f"File {i} content\n")
            for i in range(1, 4)
        ),
    ),

    # Task 3: Read and modify config
    Task(
        id="osw-003",
        name="Modify config file",
        description="Read a config file and update a value",
        setup=_setup_config_file,
        steps=[
            {"tool": "read_file", "args": {"path": "config.ini"}},
            {"tool": "write_file", "args": {
                "path": "config.ini",
                "content": "[database]\nhost=production.db.example.com\nport=5432\nname=mydb\n\n[server]\nport=8080\n",
            }},
        ],
        verify=lambda d: "production.db.example.com" in (d / "config.ini").read_text(),
    ),

    # Task 4: Append to log file
    Task(
        id="osw-004",
        name="Append log entries",
        description="Append multiple log entries to a log file",
        setup=_setup_log_file,
        steps=[
            {"tool": "append_file", "args": {"path": "app.log", "content": "2024-01-01 00:01:00 INFO User logged in\n"}},
            {"tool": "append_file", "args": {"path": "app.log", "content": "2024-01-01 00:02:00 DEBUG Processing request\n"}},
            {"tool": "append_file", "args": {"path": "app.log", "content": "2024-01-01 00:03:00 INFO Request completed\n"}},
        ],
        verify=lambda d: "Request completed" in (d / "app.log").read_text(),
    ),

    # Task 5: Create nested directory structure
    Task(
        id="osw-005",
        name="Create project structure",
        description="Create a nested project directory structure with files",
        steps=[
            {"tool": "write_file", "args": {"path": "project/src/main.py", "content": "# Main module\n"}},
            {"tool": "write_file", "args": {"path": "project/src/utils.py", "content": "# Utilities\n"}},
            {"tool": "write_file", "args": {"path": "project/tests/test_main.py", "content": "# Tests\n"}},
            {"tool": "write_file", "args": {"path": "project/README.md", "content": "# Project\n"}},
        ],
        verify=lambda d: all([
            (d / "project" / "src" / "main.py").exists(),
            (d / "project" / "src" / "utils.py").exists(),
            (d / "project" / "tests" / "test_main.py").exists(),
            (d / "project" / "README.md").exists(),
        ]),
    ),

    # Task 6: Backup and update file
    Task(
        id="osw-006",
        name="Backup then update",
        description="Create a backup of a file before modifying it",
        setup=_setup_config_file,
        steps=[
            {"tool": "read_file", "args": {"path": "config.ini"}},
            {"tool": "write_file", "args": {"path": "config.ini.bak", "content": "[database]\nhost=localhost\nport=5432\nname=mydb\n\n[server]\nport=8080\n"}},
            {"tool": "write_file", "args": {"path": "config.ini", "content": "[database]\nhost=newhost\nport=5432\nname=mydb\n\n[server]\nport=9090\n"}},
        ],
        verify=lambda d: (
            (d / "config.ini.bak").exists() and
            "newhost" in (d / "config.ini").read_text()
        ),
    ),

    # Task 7: Create and populate data files
    Task(
        id="osw-007",
        name="Create data files",
        description="Create multiple data files with JSON content",
        steps=[
            {"tool": "write_file", "args": {"path": "data/users.json", "content": '{"users": [{"id": 1, "name": "Alice"}]}\n'}},
            {"tool": "write_file", "args": {"path": "data/products.json", "content": '{"products": [{"id": 1, "name": "Widget"}]}\n'}},
            {"tool": "write_file", "args": {"path": "data/orders.json", "content": '{"orders": []}\n'}},
        ],
        verify=lambda d: all([
            (d / "data" / "users.json").exists(),
            (d / "data" / "products.json").exists(),
            (d / "data" / "orders.json").exists(),
        ]),
    ),

    # Task 8: Update README with project info
    Task(
        id="osw-008",
        name="Update README",
        description="Read project files and create comprehensive README",
        setup=_setup_project_structure,
        steps=[
            {"tool": "read_file", "args": {"path": "src/main.py"}},
            {"tool": "read_file", "args": {"path": "README.md"}},
            {"tool": "write_file", "args": {
                "path": "README.md",
                "content": "# My Project\n\nA sample project.\n\n## Usage\n\n```python\npython src/main.py\n```\n\n## License\n\nMIT\n",
            }},
        ],
        verify=lambda d: "## Usage" in (d / "README.md").read_text(),
    ),

    # Task 9: Multi-file atomic update
    Task(
        id="osw-009",
        name="Atomic multi-file update",
        description="Update multiple related files that must stay consistent",
        setup=_setup_multi_file,
        steps=[
            {"tool": "write_file", "args": {"path": "data/users.json", "content": '{"users": [{"id": 1, "name": "Bob"}]}\n'}},
            {"tool": "write_file", "args": {"path": "data/config.json", "content": '{"version": 2, "updated": true}\n'}},
        ],
        verify=lambda d: (
            "Bob" in (d / "data" / "users.json").read_text() and
            '"version": 2' in (d / "data" / "config.json").read_text()
        ),
    ),

    # Task 10: Sequential log processing
    Task(
        id="osw-010",
        name="Log processing pipeline",
        description="Read log, process, and write results",
        setup=_setup_log_file,
        steps=[
            {"tool": "read_file", "args": {"path": "app.log"}},
            {"tool": "write_file", "args": {"path": "processed.log", "content": "[PROCESSED] 2024-01-01 00:00:00 INFO Application started\n"}},
            {"tool": "append_file", "args": {"path": "processed.log", "content": "[SUMMARY] 1 log entry processed\n"}},
        ],
        verify=lambda d: (
            (d / "processed.log").exists() and
            "[SUMMARY]" in (d / "processed.log").read_text()
        ),
    ),

    # Task 11: Create shell script
    Task(
        id="osw-011",
        name="Create executable script",
        description="Create a shell script with proper content",
        steps=[
            {"tool": "write_file", "args": {"path": "scripts/deploy.sh", "content": "#!/bin/bash\necho 'Deploying...'\nexit 0\n"}},
            {"tool": "write_file", "args": {"path": "scripts/test.sh", "content": "#!/bin/bash\necho 'Running tests...'\nexit 0\n"}},
        ],
        verify=lambda d: all([
            "#!/bin/bash" in (d / "scripts" / "deploy.sh").read_text(),
            "#!/bin/bash" in (d / "scripts" / "test.sh").read_text(),
        ]),
    ),

    # Task 12: Environment file setup
    Task(
        id="osw-012",
        name="Setup environment files",
        description="Create development and production environment files",
        steps=[
            {"tool": "write_file", "args": {"path": ".env.development", "content": "DEBUG=true\nAPI_URL=http://localhost:3000\n"}},
            {"tool": "write_file", "args": {"path": ".env.production", "content": "DEBUG=false\nAPI_URL=https://api.example.com\n"}},
            {"tool": "write_file", "args": {"path": ".env.example", "content": "DEBUG=\nAPI_URL=\n"}},
        ],
        verify=lambda d: all([
            (d / ".env.development").exists(),
            (d / ".env.production").exists(),
            (d / ".env.example").exists(),
        ]),
    ),
]
