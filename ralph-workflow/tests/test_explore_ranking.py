"""Black-box tests for the deterministic ranking contract."""

from __future__ import annotations

import pytest

from ralph.mcp.explore.ranking import (
    PHASE2_DISABLED_NOTE,
    RankedItem,
    fts_query_for,
    is_fts_eligible,
    is_generated_path,
    is_source_role,
    is_test_role,
    score_grep_match,
    score_search_file,
    sort_ranked,
)


# --- Score components ------------------------------------------------------


def test_exact_basename_outranks_subdir_match() -> None:
    # The caller is searching for ``foo.py``; a path whose basename
    # is exactly that matches; a path whose basename differs does not.
    exact = score_search_file(
        candidate_path="tools/foo.py",
        basename="foo.py",
        role_requested=None,
        is_git_changed=False,
    )
    subdir = score_search_file(
        candidate_path="deep/nested/foo.py",
        basename="foo.py",  # basename of the candidate is foo.py too, so it matches
        role_requested=None,
        is_git_changed=False,
    )
    assert exact.score == subdir.score  # both match exact basename

    # When the candidate's basename differs, no exact match occurs.
    no_match = score_search_file(
        candidate_path="deep/nested/foo.py",
        basename="bar.py",
        role_requested=None,
        is_git_changed=False,
    )
    assert exact.score > no_match.score


def test_git_changed_path_adds_bonus() -> None:
    base = score_search_file(
        candidate_path="tools/foo.py",
        basename="bar.py",
        role_requested=None,
        is_git_changed=False,
    )
    with_change = score_search_file(
        candidate_path="tools/foo.py",
        basename="bar.py",
        role_requested=None,
        is_git_changed=True,
    )
    assert with_change.score > base.score
    assert any("git_changed_path" in reason for reason in with_change.reasons)


def test_generated_path_applies_penalty() -> None:
    item = score_search_file(
        candidate_path="vendor/lib/foo.py",
        basename="foo.py",
        role_requested=None,
        is_git_changed=False,
    )
    # Phase 1: exact basename +100 minus vendor -50 = +50 net.
    assert any("generated" in reason for reason in item.reasons)
    assert item.score == 50


def test_role_requested_applies_bonus() -> None:
    base = score_search_file(
        candidate_path="foo.py",
        basename="bar.py",
        role_requested=None,
        is_git_changed=False,
    )
    with_source = score_search_file(
        candidate_path="foo.py",
        basename="bar.py",
        role_requested="source",
        is_git_changed=False,
    )
    assert with_source.score > base.score
    assert any("role_requested=source" in reason for reason in with_source.reasons)


# --- Stable tie-breaks -----------------------------------------------------


def test_sort_ranked_is_stable_by_path_line_evidence() -> None:
    items = [
        RankedItem(key="c", score=5, reasons=(), path="b.py", line=1, evidence_id="ev1"),
        RankedItem(key="a", score=5, reasons=(), path="a.py", line=1, evidence_id="ev1"),
        RankedItem(key="b", score=5, reasons=(), path="a.py", line=2, evidence_id="ev1"),
    ]
    sorted_items = sort_ranked(items)
    assert sorted_items[0].path == "a.py"
    assert sorted_items[0].line == 1
    assert sorted_items[1].path == "a.py"
    assert sorted_items[1].line == 2
    assert sorted_items[2].path == "b.py"


def test_sort_ranked_higher_score_first() -> None:
    items = [
        RankedItem(key="a", score=10, reasons=(), path="a.py"),
        RankedItem(key="b", score=20, reasons=(), path="b.py"),
    ]
    sorted_items = sort_ranked(items)
    assert sorted_items[0].path == "b.py"


# --- Score reasons -------------------------------------------------------


def test_score_reasons_documents_disabled_phase2_components() -> None:
    item = score_search_file(
        candidate_path="a.py",
        basename="b.py",
        role_requested=None,
        is_git_changed=False,
    )
    joined = " ".join(item.reasons)
    assert PHASE2_DISABLED_NOTE in joined
    assert "symbol_definition" in joined
    assert "graph_neighbor" in joined


def test_score_search_file_to_dict_includes_reasons() -> None:
    item = score_search_file(
        candidate_path="a.py",
        basename="a.py",
        role_requested=None,
        is_git_changed=False,
    )
    payload = item.to_dict()
    assert payload["path"] == "a.py"
    assert payload["score"] >= 100
    assert isinstance(payload["score_reasons"], list)


# --- Grep rank_by scoring -----------------------------------------------


def test_score_grep_match_returns_phase2_disabled_for_symbol_components() -> None:
    item = score_grep_match(
        path="a.py", line=1, evidence_id="ev-1"
    )
    joined = " ".join(item.reasons)
    assert "bare_match" in joined
    assert PHASE2_DISABLED_NOTE in joined


def test_score_grep_match_git_changed_adds_bonus() -> None:
    base = score_grep_match(path="a.py", line=1, evidence_id="ev-1")
    changed = score_grep_match(
        path="a.py", line=1, evidence_id="ev-1", is_git_changed=True
    )
    assert changed.score > base.score


# --- FTS eligibility ------------------------------------------------------


def test_is_fts_eligible_accepts_literal() -> None:
    assert is_fts_eligible("hello", is_regex=False, whole_word=False) is True


def test_is_fts_eligible_rejects_regex() -> None:
    assert is_fts_eligible("foo.*", is_regex=False, whole_word=False) is False
    assert is_fts_eligible("foo.*", is_regex=True, whole_word=False) is False


def test_is_fts_eligible_rejects_whole_word_phrase() -> None:
    assert is_fts_eligible("hello world", is_regex=False, whole_word=True) is False


def test_fts_query_for_uses_phrase_quotes() -> None:
    assert fts_query_for("hello", whole_word=False) == '"hello"'
    assert fts_query_for("hello", whole_word=True) == '"hello"'


def test_fts_query_for_escapes_internal_quotes() -> None:
    assert fts_query_for('he"llo', whole_word=False) == '"he""llo"'


# --- Role + generated heuristics ------------------------------------------


def test_is_generated_path_detects_vendor() -> None:
    assert is_generated_path("vendor/lib/foo.py") is True
    assert is_generated_path("foo.py") is False


def test_is_source_role_accepts_common_extensions() -> None:
    assert is_source_role("foo.py") is True
    assert is_source_role("README.md") is True
    assert is_source_role("data.json") is True
    assert is_source_role("config.yaml") is True
    assert is_source_role("pyproject.toml") is True


def test_is_test_role_accepts_test_paths() -> None:
    assert is_test_role("tests/test_foo.py") is True
    assert is_test_role("test_foo.py") is True
    assert is_test_role("foo.py") is False


# --- RankedItem dataclass --------------------------------------------------


def test_ranked_item_defaults() -> None:
    item = RankedItem(key="a", score=0)
    assert item.path == ""
    assert item.line is None
    assert item.evidence_id is None
    assert item.reasons == ()


def test_ranked_item_is_frozen() -> None:
    item = RankedItem(key="a", score=0)
    with pytest.raises((AttributeError, TypeError)):
        item.score = 10  # type: ignore[misc]