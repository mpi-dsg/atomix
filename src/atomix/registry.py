from __future__ import annotations

from typing import Dict

from .adapters import ToolAdapter


class AdapterRegistry:
    """Registry of tool adapters keyed by tool name."""

    def __init__(self) -> None:
        self._adapters: Dict[str, ToolAdapter] = {}

    def register(self, name: str, adapter: ToolAdapter) -> None:
        self._adapters[name] = adapter

    def get(self, name: str) -> ToolAdapter:
        if name not in self._adapters:
            raise KeyError(f"Adapter not found for tool {name}")
        return self._adapters[name]

    def clear(self) -> None:
        self._adapters.clear()
