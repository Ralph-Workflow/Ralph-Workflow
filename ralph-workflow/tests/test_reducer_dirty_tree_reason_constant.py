"""The dirty-tree commit-recovery signal is pinned to one named encoding.

The reducer's commit-failure recovery (:func:`_is_repairable_git_failure`)
has no typed verdict available at its seam: the commit executor records only
display prose via ``set_last_commit_error``, and the ``COMMIT_FAILURE`` event
carries no failure category. The dirty-tree branch therefore matches a single
named constant, ``DIRTY_WORKTREE_FAILURE_ENCODING``. These tests pin the
contract: the constant is the sole acceptable encoding of the dirty-worktree
signal, and rewording the prose requires changing the constant and this test
together.
"""

from __future__ import annotations

import re

from ralph.pipeline.reducer import DIRTY_WORKTREE_FAILURE_ENCODING, _is_repairable_git_failure


class TestDirtyWorktreeFailureEncoding:
    """The dirty-tree routing input is one named constant, nothing else."""

    def test_constant_value_is_pinned(self) -> None:
        """The constant encodes exactly the canonical dirty-tree phrase."""
        assert DIRTY_WORKTREE_FAILURE_ENCODING == "worktree not clean"

    def test_constant_is_lowercase_words_only(self) -> None:
        """The encoding is plain lowercase prose (a substring, not a regex)."""
        assert re.fullmatch(r"[a-z][a-z ]*[a-z]", DIRTY_WORKTREE_FAILURE_ENCODING)

    def test_canonical_dirty_tree_reason_is_repairable(self) -> None:
        """A reason built from the constant routes as repairable."""
        reason = f"{DIRTY_WORKTREE_FAILURE_ENCODING}: uncommitted changes exist"
        assert _is_repairable_git_failure(reason) is True

    def test_reason_without_the_encoding_is_not_dirty_tree_repairable(self) -> None:
        """A reason lacking the encoding does not carry the dirty-tree signal."""
        reason = "merge failed; see operator log for details"
        assert _is_repairable_git_failure(reason) is False

    def test_encoding_matches_as_substring_of_canonical_deferral_reason(self) -> None:
        """The canonical deferral prose embeds the pinned encoding verbatim."""
        canonical = "worktree not clean; uncommitted tracked changes deferred catch-up integration"
        assert DIRTY_WORKTREE_FAILURE_ENCODING in canonical
