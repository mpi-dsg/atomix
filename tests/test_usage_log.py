"""Tests for atomix.usage_log."""

from __future__ import annotations

import json

import pytest

from atomix import usage_log


@pytest.fixture(autouse=True)
def _isolated_log(monkeypatch, tmp_path):
    monkeypatch.setenv("ATOMIX_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    usage_log.reset_path()
    yield
    usage_log.reset_path()


class _FakeAnthropicUsage:
    def __init__(self, in_t, out_t, cache=0):
        self.input_tokens = in_t
        self.output_tokens = out_t
        self.cache_read_input_tokens = cache


class _FakeAnthropicResponse:
    def __init__(self, usage):
        self.usage = usage


class _FakeOpenAIDetails:
    def __init__(self, cached):
        self.cached_tokens = cached


class _FakeOpenAIUsage:
    def __init__(self, in_t, out_t, cached=0):
        self.prompt_tokens = in_t
        self.completion_tokens = out_t
        self.prompt_tokens_details = _FakeOpenAIDetails(cached)


class _FakeOpenAIResponse:
    def __init__(self, usage):
        self.usage = usage


def test_compute_cost_known_model():
    # 100 tokens in, 50 tokens out at gpt-4o pricing.
    cost = usage_log.compute_cost("gpt-4o", 100, 50, cached_input_tokens=0)
    assert cost == pytest.approx(100 * 2.50 / 1_000_000 + 50 * 10.0 / 1_000_000)


def test_compute_cost_with_cached_input():
    cost = usage_log.compute_cost("gpt-4o", 100, 0, cached_input_tokens=40)
    # 60 charged at full, 40 at cached.
    expected = 60 * 2.50 / 1_000_000 + 40 * 1.25 / 1_000_000
    assert cost == pytest.approx(expected)


def test_compute_cost_unknown_model_zero():
    assert usage_log.compute_cost("unknown-model", 1000, 1000) == 0.0


def test_record_usage_appends(tmp_path, monkeypatch):
    monkeypatch.setenv("ATOMIX_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    usage_log.reset_path()
    rec = usage_log.record_usage(
        provider="openai", model="gpt-4o",
        input_tokens=100, output_tokens=50, run_id="test-run",
    )
    assert rec.cost_usd > 0
    p = tmp_path / "usage.jsonl"
    line = p.read_text().strip()
    d = json.loads(line)
    assert d["provider"] == "openai"
    assert d["run_id"] == "test-run"
    assert d["input_tokens"] == 100
    assert d["output_tokens"] == 50


def test_record_anthropic_extracts_usage():
    resp = _FakeAnthropicResponse(_FakeAnthropicUsage(123, 45, cache=20))
    rec = usage_log.record_anthropic(resp, model="claude-sonnet-4-5", run_id="r1")
    assert rec.input_tokens == 123
    assert rec.output_tokens == 45
    assert rec.cached_input_tokens == 20


def test_record_openai_extracts_usage():
    resp = _FakeOpenAIResponse(_FakeOpenAIUsage(200, 50, cached=80))
    rec = usage_log.record_openai(resp, model="gpt-4o", run_id="r2")
    assert rec.input_tokens == 200
    assert rec.output_tokens == 50
    assert rec.cached_input_tokens == 80


def test_aggregate_groups(tmp_path, monkeypatch):
    monkeypatch.setenv("ATOMIX_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    usage_log.reset_path()
    usage_log.record_usage(provider="openai", model="gpt-4o", input_tokens=100, output_tokens=50, run_id="A")
    usage_log.record_usage(provider="openai", model="gpt-4o", input_tokens=200, output_tokens=100, run_id="A")
    usage_log.record_usage(
        provider="anthropic", model="claude-sonnet-4-5",
        input_tokens=500, output_tokens=200, run_id="B",
    )
    agg = usage_log.aggregate()
    assert agg["total_records"] == 3
    assert agg["by_run_id"]["A"]["calls"] == 2
    assert agg["by_run_id"]["A"]["input"] == 300
    assert agg["by_run_id"]["B"]["calls"] == 1
    assert agg["by_provider"]["openai"]["calls"] == 2
    assert agg["by_model"]["gpt-4o"]["calls"] == 2


def test_record_handles_missing_usage():
    class _NoUsage:
        usage = None

    rec = usage_log.record_anthropic(_NoUsage(), model="claude-sonnet-4-5")
    assert rec.input_tokens == 0
    rec2 = usage_log.record_openai(_NoUsage(), model="gpt-4o")
    assert rec2.input_tokens == 0
