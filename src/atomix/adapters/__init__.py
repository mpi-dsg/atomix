from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Set

from ..effects import Effect, ResourceId
from ..epoch import Epoch
from ..tool_result import ToolResult


class ToolAdapter(ABC):
    """Base class for wrapping tools with scope/effect metadata."""

    name: str

    @abstractmethod
    def scopes(self, args: dict[str, Any]) -> Set[ResourceId]:
        ...

    @abstractmethod
    def to_effect(self, args: dict[str, Any], result: ToolResult, epoch: Epoch) -> Effect:
        ...


__all__ = ["ToolAdapter"]
