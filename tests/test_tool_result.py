"""Tests for ToolResult normalization and artifact handling."""

from __future__ import annotations

import hashlib

from atomix.tool_result import ArtifactRef, ToolMeta, ToolResult, normalize_tool_result


def test_artifact_ref_from_bytes_sets_hash_and_size() -> None:
    data = b"abc123"
    ref = ArtifactRef.from_bytes("screenshot", data, content_type="image/png")

    assert ref.kind == "screenshot"
    assert ref.sha256 == hashlib.sha256(data).hexdigest()
    assert ref.size == len(data)
    assert ref.content_type == "image/png"
    assert ref.bytes == data


def test_normalize_tool_result_from_raw_output() -> None:
    meta = ToolMeta(tool_name="demo", trace_id="t1", branch_id="b1", attempt=0)
    result = normalize_tool_result({"value": 123}, meta=meta)

    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.output == {"value": 123}
    assert result.error is None
    assert result.meta.tool_name == "demo"


def test_normalize_tool_result_from_dict_passthrough() -> None:
    meta = ToolMeta(tool_name="demo", trace_id="t1", branch_id=None, attempt=2)
    raw = {"success": False, "output": None, "error": "boom"}
    result = normalize_tool_result(raw, meta=meta)

    assert result.success is False
    assert result.output is None
    assert result.error == "boom"
    assert result.meta.attempt == 2
