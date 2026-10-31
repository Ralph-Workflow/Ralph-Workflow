"""The empty-stop classifier never reads a commit SUBJECT as an empty stop.

``git rebase --skip`` discards the commit being replayed. The classifier
that decides whether a paused rebase is an "empty commit" stop used to
search the whole output for ``is empty``; git echoes the replayed
commit's subject in ``could not apply <sha>... <subject>``, so a genuine
conflict on "Fix crash when the list is empty" was skipped and the commit
silently vanished while the rebase reported success.

The output below is what git 2.50 prints for exactly that conflict.
"""

from __future__ import annotations

import pytest

from ralph.git.rebase.rebase import is_empty_rebase_stop

_CONFLICT_ON_IS_EMPTY_SUBJECT_STDERR = (
    "error: could not apply 3cda1b9... Fix crash when the list is empty\n"
    "hint: Resolve all conflicts manually, mark them as resolved with\n"
    'hint: "git add/rm <conflicted_files>", then run "git rebase --continue".\n'
    'hint: You can instead skip this commit: run "git rebase --skip".\n'
    'hint: To abort and get back to the state before "git rebase", run "git rebase --abort".\n'
    "Could not apply 3cda1b9... # Fix crash when the list is empty\n"
)
_CONFLICT_ON_IS_EMPTY_SUBJECT_STDOUT = (
    "Rebasing (1/2)Auto-merging a.py\nCONFLICT (content): Merge conflict in a.py\n"
)


def test_a_conflict_whose_subject_says_is_empty_is_not_an_empty_stop() -> None:
    """The subject git echoes back is never evidence of an empty commit."""
    assert not is_empty_rebase_stop(
        _CONFLICT_ON_IS_EMPTY_SUBJECT_STDERR, _CONFLICT_ON_IS_EMPTY_SUBJECT_STDOUT
    )


@pytest.mark.parametrize(
    "subject",
    [
        "nothing to commit",
        "The previous cherry-pick is now empty",
        "No changes - did you forget to use 'git add'?",
    ],
)
def test_a_subject_that_quotes_an_empty_stop_message_is_not_an_empty_stop(subject: str) -> None:
    """Even a subject that IS git's message text only appears mid-line."""
    stderr = (
        f"error: could not apply abc1234... {subject}\nCould not apply abc1234... # {subject}\n"
    )
    hint = f"hint: Could not execute the todo command\nhint:\nhint:     pick abc1234 {subject}\n"
    assert not is_empty_rebase_stop(stderr + hint, "")


@pytest.mark.parametrize(
    "stderr",
    [
        "The previous cherry-pick is now empty, possibly due to conflict resolution.\n"
        "If you wish to commit it anyway, use:\n\n    git commit --allow-empty\n",
        "On branch feature\nnothing to commit, working tree clean\n",
        "No changes - did you forget to use 'git add'?\n"
        "If there is nothing left to stage, chances are that something else\n",
        "error: No changes -- Patch already applied.\n",
    ],
)
def test_gits_own_empty_stop_messages_are_recognised(stderr: str) -> None:
    """Every empty-stop line git itself authors is still recognised."""
    assert is_empty_rebase_stop(stderr, "")
