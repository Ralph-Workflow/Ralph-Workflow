from __future__ import annotations

from ralph.pipeline import (
    auto_integrate,
    auto_integrate_agent,
    auto_integrate_rebase_merge,
    auto_integrate_refresh,
)


def _doc(module: object) -> str:
    text = getattr(module, "__doc__", None)
    assert isinstance(text, str) and text.strip(), (
        f"{module!r} must carry a non-empty module docstring"
    )
    return " ".join(text.split())


def test_rebase_merge_module_documents_resolution_before_the_fallback() -> None:
    doc = _doc(auto_integrate_rebase_merge)
    assert "in place" in doc.lower(), (
        "the rebase/merge engine docstring must say a conflicted rebase is "
        f"resolved in place before the fallback, got: {doc!r}"
    )
    resolve_at = doc.lower().find("in place")
    fallback_at = doc.lower().find("fall back")
    assert fallback_at != -1, f"the docstring must still describe the fallback: {doc!r}"
    assert resolve_at < fallback_at, (
        f"in-place resolution must be described BEFORE the endpoint-merge fallback, got: {doc!r}"
    )


def test_run_rebase_or_merge_summary_line_is_not_superseded() -> None:
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
    doc = _doc(auto_integrate).lower()
    resolve_at = doc.find("resolve the rebase in place")
    merge_at = doc.find("merge on unresolved conflict")
    assert resolve_at != -1, "auto_integrate must document resolve-in-place"
    assert merge_at != -1, "auto_integrate must document the unresolved-conflict merge"
    assert resolve_at < merge_at, (
        "resolve-in-place must precede the endpoint-merge fallback in the numbered workflow"
    )


def test_resolver_docstring_attributes_merge_commit_to_endpoint_merge_only() -> None:
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
            f"any merge-commit claim must be attributed to the endpoint-merge mode, got: {doc!r}"
        )


def test_refresh_module_documents_local_observation_without_remote_io() -> None:
    doc = " ".join((auto_integrate_refresh.refresh_target.__doc__ or "").split())
    assert doc, "refresh_target must carry a docstring"
    assert "without contacting a remote" in doc
    assert "no-op" not in doc.lower(), (
        f"refresh_target is no longer a no-op when fetching is disabled, got: {doc!r}"
    )
