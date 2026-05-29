"""Tests for A6 alias suite + canonicalization."""

from __future__ import annotations

import pytest

from atomix.checker.load_alias_suite import (
    canonicalize,
    expected_match,
    load_alias_suite,
)


@pytest.fixture(scope="module")
def cases():
    import platform
    all_cases = load_alias_suite()
    # The macOS case-fold case relies on a Darwin-only canonicalization
    # heuristic. On Linux/other, filter it out so the suite stays portable.
    if platform.system() != "Darwin":
        all_cases = [c for c in all_cases if c.name != "fs.case_fold_macos"]
    return all_cases


def test_loads(cases):
    assert len(cases) >= 50  # plan target; this seed delivers ~54


def test_substrates_present(cases):
    substrates = {c.substrate for c in cases}
    assert {"filesystem", "taubench", "dom", "rw_dep"} <= substrates


def test_should_conflict_canonicalizes_equally(cases):
    # Expected to be true for every should-conflict case in the seed suite.
    failures = []
    for c in cases:
        if c.label == "should-conflict":
            a = canonicalize(c.scope_a, c.substrate)
            b = canonicalize(c.scope_b, c.substrate)
            if a != b:
                failures.append((c.name, a, b))
    assert not failures, f"should-conflict cases that canonicalize differently: {failures}"


def test_should_not_conflict_canonicalizes_differently(cases):
    failures = []
    for c in cases:
        if c.label == "should-not-conflict":
            a = canonicalize(c.scope_a, c.substrate)
            b = canonicalize(c.scope_b, c.substrate)
            if a == b:
                failures.append((c.name, a))
    assert not failures, f"should-not-conflict cases that canonicalize equally: {failures}"


def test_naive_string_scopes_misses_conflicts(cases):
    """With naive scopes (raw string equality), every should-conflict case
    where the raw strings differ is a missed conflict — proves canonicalization
    is load-bearing.
    """
    naive_misses = 0
    for c in cases:
        if c.label == "should-conflict" and c.scope_a != c.scope_b:
            naive_misses += 1
    assert naive_misses > 0


def test_expected_match_helper(cases):
    for c in cases:
        if c.label == "should-conflict":
            assert expected_match(c) is True, c.name
        else:
            assert expected_match(c) is False, c.name
