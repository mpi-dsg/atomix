"""Tests for the ablation flag config (A1)."""

from __future__ import annotations

import pytest

from atomix.config import AblationFlags, parse_flags


def test_default_is_tx_full():
    assert AblationFlags().name == "Tx-Full"


def test_each_flag_changes_name():
    assert AblationFlags(no_scope_on_read=True).name == "Tx-NoScopeOnRead"
    assert AblationFlags(no_abort_on_stale=True).name == "Tx-NoAbortOnStale"
    assert AblationFlags(global_frontier=True).name == "Tx-GlobalFrontier"
    assert (
        AblationFlags(misclassified_irreversible=True).name
        == "Atomix-MisclassifiedIrreversible"
    )
    assert AblationFlags(naive_string_scopes=True).name == "Tx-NaiveStringScopes"
    assert (
        AblationFlags(no_residue_classification=True).name
        == "Tx-Full-NoResidueClassification"
    )


def test_compose_with_plus():
    assert (
        AblationFlags(no_scope_on_read=True, global_frontier=True).name
        == "Tx-NoScopeOnRead+Tx-GlobalFrontier"
    )


def test_parse_round_trip():
    for spec in [
        "Tx-Full",
        "Tx-NoScopeOnRead",
        "Tx-NoAbortOnStale",
        "Tx-GlobalFrontier",
        "Atomix-MisclassifiedIrreversible",
        "Tx-NaiveStringScopes",
        "Tx-Full-NoResidueClassification",
    ]:
        assert parse_flags(spec).name == spec


def test_parse_compose():
    f = parse_flags("Tx-NoScopeOnRead+Tx-GlobalFrontier")
    assert f.no_scope_on_read is True
    assert f.global_frontier is True


def test_parse_empty_is_full():
    assert parse_flags("").name == "Tx-Full"
    assert parse_flags(None).name == "Tx-Full"


def test_parse_rejects_unknown_flags():
    with pytest.raises(ValueError, match="Typo"):
        parse_flags("Tx-NoScopeOnRead+Typo")
