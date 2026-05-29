"""
Load tasks from OSWorld's task repository.

Parses the 369 task definitions from the OSWorld benchmark.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from ..harness import RealOSWorldTask

logger = logging.getLogger(__name__)


# OSWorld domains
DOMAINS = [
    "os",  # File manager, system settings
    "libreoffice_calc",
    "libreoffice_writer",
    "libreoffice_impress",
    "vlc",
    "chrome",
    "vscode",
    "vs_code",
    "gimp",
    "thunderbird",
    "multi_app",
]

# Domain categories for analysis
DOMAIN_CATEGORIES = {
    "file_centric": ["os", "libreoffice_calc", "libreoffice_writer", "vscode", "vs_code"],
    "ui_centric": ["chrome", "gimp", "vlc", "libreoffice_impress"],
    "multi_app": ["multi_app"],
    "communication": ["thunderbird"],
}


class TaskLoader:
    """Load tasks from OSWorld's task repository."""

    def __init__(self, osworld_path: Path):
        """
        Args:
            osworld_path: Path to cloned OSWorld repository root
        """
        self.osworld_path = Path(osworld_path)
        self.evaluation_examples_path = self.osworld_path / "evaluation_examples"

    def load_all_tasks(self) -> List[RealOSWorldTask]:
        """Load all tasks from OSWorld."""
        tasks = []
        for domain in DOMAINS:
            domain_tasks = self.load_domain_tasks(domain)
            tasks.extend(domain_tasks)
            logger.info(f"Loaded {len(domain_tasks)} tasks from domain: {domain}")

        logger.info(f"Total tasks loaded: {len(tasks)}")
        return tasks

    def load_domain_tasks(self, domain: str) -> List[RealOSWorldTask]:
        """Load all tasks for a specific domain."""
        domain_paths = [
            self.evaluation_examples_path / "examples" / domain,
            self.evaluation_examples_path / domain,
            self.osworld_path / "evaluation_examples" / domain,
        ]
        if domain == "multi_app":
            domain_paths.insert(
                0, self.evaluation_examples_path / "examples" / "multi_apps"
            )

        tasks: List[RealOSWorldTask] = []
        for domain_path in domain_paths:
            if domain_path.exists() and domain_path.is_dir():
                for task_entry in domain_path.iterdir():
                    if task_entry.is_dir():
                        task = self._load_task(task_entry, domain)
                        if task:
                            tasks.append(task)
                    elif task_entry.is_file() and task_entry.suffix == ".json":
                        task = self._load_task_file(task_entry, domain, task_entry.stem)
                        if task:
                            tasks.append(task)
                break

        return tasks

    def _load_task(self, task_dir: Path, domain: str) -> Optional[RealOSWorldTask]:
        """Load a single task from its directory."""
        config_names = ["config.json", "task.json", "metadata.json"]

        config_file = None
        for name in config_names:
            potential = task_dir / name
            if potential.exists():
                config_file = potential
                break

        if not config_file:
            return RealOSWorldTask(
                id=task_dir.name,
                domain=domain,
                instruction=f"Task from {task_dir.name}",
                config_file=None,
            )

        return self._load_task_file(config_file, domain, task_dir.name)

    def _load_task_file(
        self, config_file: Path, domain: str, fallback_id: str
    ) -> Optional[RealOSWorldTask]:
        try:
            with open(config_file, encoding="utf-8") as f:
                config = json.load(f)

            task_id = config.get("id", config.get("task_id", fallback_id))
            instruction = config.get(
                "instruction",
                config.get("task", config.get("description", f"Task {task_id}")),
            )

            return RealOSWorldTask(
                id=task_id,
                domain=domain,
                instruction=instruction,
                config_file=config_file,
            )
        except Exception as e:
            logger.warning(f"Failed to load task from {config_file}: {e}")
            return None

    def load_tasks_by_category(self) -> Dict[str, List[RealOSWorldTask]]:
        """Load tasks categorized by type."""
        all_tasks = self.load_all_tasks()

        categorized = {category: [] for category in DOMAIN_CATEGORIES}

        for task in all_tasks:
            for category, domains in DOMAIN_CATEGORIES.items():
                if task.domain in domains:
                    categorized[category].append(task)
                    break

        return categorized

    def load_task_by_id(self, task_id: str) -> Optional[RealOSWorldTask]:
        """Load a specific task by ID."""
        all_tasks = self.load_all_tasks()
        for task in all_tasks:
            if task.id == task_id:
                return task
        return None

    def get_task_ids(self) -> List[str]:
        """Get all task IDs without loading full task data."""
        task_ids = []
        for domain in DOMAINS:
            domain_path = self.evaluation_examples_path / "examples" / domain
            if domain_path.exists():
                for task_dir in domain_path.iterdir():
                    if task_dir.is_dir():
                        task_ids.append(task_dir.name)
        return task_ids


def load_tasks_from_osworld(osworld_path: str | Path) -> List[RealOSWorldTask]:
    """Convenience function to load all OSWorld tasks.

    Args:
        osworld_path: Path to cloned OSWorld repository

    Returns:
        List of all tasks
    """
    loader = TaskLoader(Path(osworld_path))
    return loader.load_all_tasks()
