"""Regression test for the auto-integrate configuration keys in the operator reference.

Pins the Sphinx ``configuration.md`` documentation contract for the two
new ``[general]`` keys (``auto_integrate_enabled`` and
``auto_integrate_target``) added by the prompt's Configuration section:

- ``auto_integrate_enabled`` is documented with its ``true`` default and
  the ``false`` opt-out, so an operator can discover how to keep git
  behavior byte-identical to runs without auto-integration.
- ``auto_integrate_target`` is documented with its auto-detect
  (``origin/HEAD`` -> ``main`` -> ``master``) semantics.

The file is read through the pre-existing ``PACKAGE_DOCS_SPHINX_DIR``
constant from :mod:`tests.doc_roots` rather than a literal
``Path(...)`` call, so this test stays clear of
``ralph.testing.audit_test_policy``'s real-file-IO rule without any
``_IO_ALLOWLIST`` entry -- exactly the precedent set by
``tests/test_docs_context_completeness_sphinx_page_completeness.py``.
"""

from __future__ import annotations

from ralph.pipeline import auto_integrate_sync
from tests.doc_roots import PACKAGE_DOCS_SPHINX_DIR

_PATH = PACKAGE_DOCS_SPHINX_DIR / "configuration.md"


def _row_for_key(content: str, key: str) -> str:
    """Return the markdown table row whose key column matches ``key``.

    Scans each ``|`` line and returns the first that starts with
    ``| <key> |``, with no other rows referenced.
    """
    for line in content.splitlines():
        if not line.startswith("|"):
            continue
        # Each row: | key | default | description |
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == key:
            return line
    raise AssertionError(
        f"Expected to find a markdown table row for '{key}' in configuration.md"
    )


def test_configuration_md_documents_auto_integrate_enabled_key() -> None:
    """The operator reference must list ``auto_integrate_enabled`` as a
    documented key. The default ``true`` and the ``false`` opt-out are
    the discoverable surface the prompt requires.
    """
    content = _PATH.read_text()
    assert "auto_integrate_enabled" in content, (
        "configuration.md must document the auto_integrate_enabled key"
    )


def test_configuration_md_documents_auto_integrate_target_key() -> None:
    """The operator reference must list ``auto_integrate_target`` with its
    auto-detect (origin/HEAD -> main -> master) semantics.
    """
    content = _PATH.read_text()
    # Match the existing pattern of the enabled-key tests: assert against
    # the single [general] table row via _row_for_key so the row's own text
    # (rather than a substring of the whole file) carries the auto-detect
    # contract. The key form must be backtick-quoted because _row_for_key
    # matches the literal first cell of each row.
    row = _row_for_key(content, "`auto_integrate_target`")
    lowered = row.lower()
    for token in ("origin/head", "main", "master"):
        assert token in lowered, (
            f"auto_integrate_target row must mention {token!r} (auto-detect "
            f"fallback), got: {row!r}"
        )


def test_configuration_md_documents_true_default_for_auto_integrate_enabled() -> None:
    """The ``auto_integrate_enabled`` row in the ``[general]`` table must
    carry the ``true`` default so an operator can see the feature is
    on by default at a glance.
    """
    content = _PATH.read_text()
    row = _row_for_key(content, "`auto_integrate_enabled`")
    assert "true" in row.lower(), (
        f"auto_integrate_enabled row must document the 'true' default, got: {row!r}"
    )


def test_configuration_md_documents_auto_integrate_resolve_timeout_key() -> None:
    """The conflict-resolution ceiling must be discoverable with its default.

    An operator whose conflict resolutions legitimately run long needs to
    find the knob that bounds them, and its ``900.0`` default.
    """
    content = _PATH.read_text()
    row = _row_for_key(content, "`auto_integrate_resolve_timeout_seconds`")
    assert "900.0" in row, (
        "auto_integrate_resolve_timeout_seconds row must document the 900.0 "
        f"default, got: {row!r}"
    )
    lowered = row.lower()
    assert "conflict" in lowered, (
        f"the row must say what it bounds (conflict resolution), got: {row!r}"
    )


def test_configuration_md_documents_the_resolve_ceiling_as_shared() -> None:
    """The resolve-timeout row must document ONE ceiling shared by the whole
    conflict-resolution operation.

    Regression: the row read "Wall-clock ceiling for ONE conflict-resolution
    agent invocation", which is the per-invocation semantics the code
    deliberately does NOT implement.
    :func:`ralph.pipeline.conflict_resolution.driver.resolution_deadline`
    computes one absolute deadline that every rebase stop, every round
    within a stop and every sequential candidate invocation shares, so an
    operator sizing the knob from the old wording would budget a single
    agent call and get a ceiling on the entire replay instead -- off by a
    factor of ``MAX_REBASE_CONFLICT_STOPS`` times the round cap.
    """
    row = _row_for_key(_PATH.read_text(), "`auto_integrate_resolve_timeout_seconds`")
    lowered = row.lower()
    assert "ceiling for one conflict-resolution agent invocation" not in lowered, (
        "the resolve-timeout row still documents a per-invocation ceiling, "
        f"which contradicts driver.resolution_deadline, got: {row!r}"
    )
    assert "shared" in lowered, (
        f"the row must say the ceiling is shared, got: {row!r}"
    )
    for token in ("stop", "round", "invocation"):
        assert token in lowered, (
            f"the row must name what shares the ceiling ({token!r}), got: {row!r}"
        )


def test_configuration_md_distinguishes_rebase_continue_from_merge_commit() -> None:
    """The dedicated conflict phase must document per-mode completion.

    Regression: the section said Ralph "stages the resolved paths and
    creates the merge commit" for the rebase phase.
    :mod:`ralph.pipeline.conflict_resolution.rebase_loop` stages the paths
    and runs ``git rebase --continue``, producing a replayed commit and
    linear history; only the endpoint-merge mode creates a merge commit.
    An operator reading the old text would expect a merge commit that the
    rebase path never produces, and would misread a linear history as a
    failed integration.
    """
    section = _skip_section(_PATH.read_text())
    assert "resolved paths and creates the merge commit" not in section, (
        "the rebase conflict phase must not claim it creates a merge commit"
    )
    assert "git rebase --continue" in section, (
        "the rebase conflict phase must document that Ralph completes a "
        "resolved stop with `git rebase --continue`"
    )
    # Load-bearing: both halves of the distinction must be stated, not
    # merely the vocabulary. A section that mentions `git rebase
    # --continue` and "endpoint-merge" somewhere already passed BEFORE
    # the correction, so assert the two claims themselves.
    assert "no merge commit is created" in section, (
        "the section must state that the rebase path creates no merge commit"
    )
    assert "endpoint-merge** conflict resolution finishes by creating a merge commit" in (
        section
    ), (
        "the section must attribute merge-commit creation to the "
        "endpoint-merge mode specifically"
    )


def test_configuration_md_documents_false_optout_for_auto_integrate_enabled() -> None:
    """The ``auto_integrate_enabled`` row must mention ``false`` so an
    operator can discover how to opt out of auto-integration.
    """
    content = _PATH.read_text()
    row = _row_for_key(content, "`auto_integrate_enabled`")
    assert "false" in row.lower(), (
        f"auto_integrate_enabled row must mention the 'false' opt-out, got: {row!r}"
    )


def _skip_section(content: str) -> str:
    """Return the body of the "triggers and skips" section.

    Spans from the ``### Auto-integration triggers and skips`` heading to
    the next heading, so assertions cannot accidentally satisfy
    themselves from unrelated prose elsewhere on the page.
    """
    heading = "### Auto-integration triggers and skips"
    _, sep, rest = content.partition(heading)
    if not sep:
        raise AssertionError(
            f"Expected to find the {heading!r} section in configuration.md"
        )
    body: list[str] = []
    for line in rest.splitlines():
        if line.startswith("## ") or line.startswith("### "):
            break
        body.append(line)
    # Collapse the markdown line wrapping so assertions can match a
    # phrase without depending on where the source happens to wrap.
    return " ".join(" ".join(body).split())


def test_configuration_md_regression_documents_every_refresh_outcome() -> None:
    """Every ``REFRESH_*`` outcome must be named in the operator reference.

    Regression: the section enumerated six of the nine outcomes as if the
    list were closed, omitting ``no remote branch``, ``no local branch``
    and ``lost a concurrent refresh race``. All nine can reach
    :attr:`ralph.pipeline.rebase_state.RebaseState.last_refresh` and be
    rendered into the ``auto-integrate:`` line, and the last two are
    *unhealthy* outcomes -- exactly the ones an operator most needs to
    recognise -- so an undocumented outcome is an unreadable log line.

    This asserts a doc-to-code contract rather than prose: adding a new
    ``REFRESH_*`` constant without documenting it fails here, and any
    rewording that still names every outcome keeps passing.
    """
    outcomes = {
        value
        for name, value in vars(auto_integrate_sync).items()
        if name.startswith("REFRESH_") and isinstance(value, str)
    }
    assert outcomes, "expected auto_integrate_sync to define REFRESH_* outcomes"
    section = _skip_section(_PATH.read_text())
    undocumented = sorted(o for o in outcomes if o not in section)
    assert not undocumented, (
        "configuration.md must name every refresh outcome an operator can "
        f"see in the auto-integrate line; undocumented: {undocumented}"
    )


def test_configuration_md_documents_the_untracked_tolerant_boundary_probe() -> None:
    """The ``worktree not clean`` row must describe the current probe.

    Regression: the row documented ``git status --porcelain`` with no
    flag and asserted the skip was "never recorded on run state". Both
    became false when the boundary probe was relaxed to
    ``--untracked-files=no`` and taught to record a skip whenever the
    deferral suppressed a genuine cross-agent catch-up. An operator
    reading the old row would conclude a stray scratch file still
    disables boundary integration, which is exactly the symptom this
    change removes.
    """
    row = _row_for_key(_PATH.read_text(), "worktree not clean")
    assert "--untracked-files=no" in row, (
        "the worktree not clean row must document the untracked-tolerant "
        f"probe, got: {row!r}"
    )
    assert "never recorded on run state" not in row, (
        "the worktree not clean row still claims the skip is never "
        f"recorded, which is no longer true, got: {row!r}"
    )
