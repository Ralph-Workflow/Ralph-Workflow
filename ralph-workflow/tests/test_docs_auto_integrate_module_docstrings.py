"""Contract tests for the auto-integration module docstrings Sphinx publishes.

``docs/sphinx/modules.rst`` carries an ``.. automodule::`` directive for
every module under test here, so these docstrings are not internal
commentary -- they render into the same operator-facing site as
``configuration.md``. That makes a stale one exactly as misleading as a
stale table row, and it is the failure mode this file exists to prevent:
a correction applied to ``configuration.md`` while a sibling module
docstring kept asserting the superseded behavior.

Two superseded claims are pinned as forbidden:

- *Every conflicted rebase is aborted before the endpoint merge.* The
  rebase is now resolved IN PLACE first
  (:mod:`ralph.pipeline.conflict_resolution.rebase_loop`); the abort is
  only the fallback for a resolution that does not land.
- *Conflict resolution creates the merge commit.* True only of the
  endpoint-merge mode. The rebase mode stages the proved-resolved paths
  and runs ``git rebase --continue``, leaving linear history.

The assertions read ``__doc__`` off imported modules rather than the
files, so no real-file IO and no ``_IO_ALLOWLIST`` entry is needed.
"""

from __future__ import annotations

from ralph.pipeline import (
    auto_integrate,
    auto_integrate_agent,
    auto_integrate_rebase_merge,
    auto_integrate_refresh,
)


def _doc(module: object) -> str:
    """Return a module's docstring with whitespace collapsed.

    Collapsing lets an assertion match a phrase without depending on
    where the source docstring happens to wrap.
    """
    text = getattr(module, "__doc__", None)
    assert isinstance(text, str) and text.strip(), (
        f"{module!r} must carry a non-empty module docstring"
    )
    return " ".join(text.split())


def test_rebase_merge_module_documents_resolution_before_the_fallback() -> None:
    """The engine-driver module must not claim conflicts are aborted first.

    Regression: this docstring read "when it conflicts or fails, abort it
    cleanly and fall back to a single endpoint three-way merge", which
    contradicted both the code below it
    (``run_rebase_or_merge`` calls ``_resolve_conflicted_rebase`` before
    ``_fallback_to_endpoint_merge``) and its own function docstring.
    """
    doc = _doc(auto_integrate_rebase_merge)
    assert "in place" in doc.lower(), (
        "the rebase/merge engine docstring must say a conflicted rebase is "
        f"resolved in place before the fallback, got: {doc!r}"
    )
    resolve_at = doc.lower().find("in place")
    fallback_at = doc.lower().find("fall back")
    assert fallback_at != -1, f"the docstring must still describe the fallback: {doc!r}"
    assert resolve_at < fallback_at, (
        "in-place resolution must be described BEFORE the endpoint-merge "
        f"fallback, got: {doc!r}"
    )


def test_run_rebase_or_merge_summary_line_is_not_superseded() -> None:
    """The rendered summary line must not assert the abandoned behavior.

    Regression: this docstring's FIRST line -- the one Sphinx renders in
    the function index, and the only one many readers see -- said "fall
    back to endpoint merge on conflict or failure", and its second
    paragraph said "Both a conflicted AND a failed rebase fall back to
    the endpoint merge". A later paragraph then correctly described
    in-place resolution, so the docstring contradicted itself and led
    with the superseded half.
    """
    doc = auto_integrate_rebase_merge.run_rebase_or_merge.__doc__ or ""
    summary = doc.strip().splitlines()[0] if doc.strip() else ""
    assert summary, "run_rebase_or_merge must carry a docstring summary line"
    assert "on conflict or failure" not in summary.lower(), (
        "the summary line still says the fallback fires on any conflict, "
        f"which skips in-place resolution, got: {summary!r}"
    )
    assert "in place" in summary.lower(), (
        f"the summary line must lead with in-place resolution, got: {summary!r}"
    )
    collapsed = " ".join(doc.split()).lower()
    assert "both a conflicted and a failed rebase fall back" not in collapsed, (
        "the body still claims every conflicted rebase falls back to the "
        f"endpoint merge, got: {collapsed!r}"
    )


def test_auto_integrate_module_documents_resolve_in_place_before_merge() -> None:
    """The top-level workflow docstring must order the steps as the code runs."""
    doc = _doc(auto_integrate).lower()
    resolve_at = doc.find("resolve the rebase in place")
    merge_at = doc.find("merge on unresolved conflict")
    assert resolve_at != -1, "auto_integrate must document resolve-in-place"
    assert merge_at != -1, "auto_integrate must document the unresolved-conflict merge"
    assert resolve_at < merge_at, (
        "resolve-in-place must precede the endpoint-merge fallback in the "
        "numbered workflow"
    )


def test_resolver_docstring_attributes_merge_commit_to_endpoint_merge_only() -> None:
    """Only the endpoint-merge mode may be described as creating a merge commit."""
    doc = _doc(auto_integrate_agent)
    lowered = doc.lower()
    assert "git rebase --continue" in lowered, (
        "the resolver docstring must say a rebase stop is completed with "
        f"`git rebase --continue`, got: {doc!r}"
    )
    if "merge commit" in lowered:
        claim_at = lowered.find("merge commit")
        qualifier_at = lowered.rfind("endpoint-merge", 0, claim_at)
        assert qualifier_at != -1, (
            "any merge-commit claim must be attributed to the endpoint-merge "
            f"mode, got: {doc!r}"
        )


def test_refresh_module_documents_local_observation_when_fetch_disabled() -> None:
    """Fetch-disabled refresh is not a no-op; it re-observes the local ref.

    Regression: ``refresh_target`` was documented as "a no-op returning
    ``REFRESH_DISABLED`` when fetching is turned off", which stopped being
    true when the local-fleet observation landed. Operators running a
    worktree fleet with no remote read that as "freshness is off".
    """
    doc = " ".join((auto_integrate_refresh.refresh_target.__doc__ or "").split())
    assert doc, "refresh_target must carry a docstring"
    assert "REFRESH_LOCAL_FLEET" in doc, (
        "refresh_target must document the local-fleet outcome for a "
        f"fetch-disabled run, got: {doc!r}"
    )
    assert "no-op" not in doc.lower(), (
        "refresh_target is no longer a no-op when fetching is disabled, "
        f"got: {doc!r}"
    )
