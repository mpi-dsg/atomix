"""Tool result normalization and artifact references."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping


@dataclass(frozen=True)
class ArtifactRef:
    kind: str
    sha256: str
    size: int
    content_type: str | None = None
    bytes: bytes | None = None

    @classmethod
    def from_bytes(
        cls, kind: str, data: bytes, content_type: str | None = None
    ) -> "ArtifactRef":
        digest = hashlib.sha256(data).hexdigest()
        return cls(
            kind=kind,
            sha256=digest,
            size=len(data),
            content_type=content_type,
            bytes=data,
        )


@dataclass(frozen=True)
class ToolMeta:
    tool_name: str
    trace_id: str
    branch_id: str | None
    attempt: int
    duration_ms: int | None = None


@dataclass(frozen=True)
class ToolResult:
    success: bool
    output: Any
    error: str | None
    meta: ToolMeta
    artifacts: Dict[str, ArtifactRef] = field(default_factory=dict)


def normalize_tool_result(raw: Any, meta: ToolMeta) -> ToolResult:
    if isinstance(raw, ToolResult):
        return raw

    success = True
    output = raw
    error = None
    artifacts: Dict[str, ArtifactRef] = {}

    if isinstance(raw, Mapping):
        if "success" in raw or "error" in raw or "output" in raw:
            success = bool(raw.get("success", raw.get("error") is None))
            output = raw.get("output")
            error = raw.get("error")
        if "artifacts" in raw and isinstance(raw["artifacts"], Mapping):
            for key, value in raw["artifacts"].items():
                if isinstance(value, ArtifactRef):
                    artifacts[key] = value
                elif isinstance(value, bytes):
                    artifacts[key] = ArtifactRef.from_bytes(key, value)

    return ToolResult(
        success=success,
        output=output,
        error=error,
        meta=meta,
        artifacts=artifacts,
    )
