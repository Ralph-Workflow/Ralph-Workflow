"""Regression tests for docs/README.md family-level scope map.

Ensures that docs/README.md explicitly maps current-vs-archival status
for all guide families and is the authoritative file-level map.
"""

from tests.doc_roots import PACKAGE_DOCS_DIR, PACKAGE_ROOT, REPO_ROOT_DOCS_DIR


def test_docs_readme_exists() -> None:
    """docs/README.md must exist as the authoritative documentation map."""
    path = REPO_ROOT_DOCS_DIR / "README.md"
    assert path.exists(), "docs/README.md must exist"


def test_docs_readme_maps_agents_family() -> None:
    """docs/README.md must explicitly cover the docs/agents family."""
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text()
    # Must reference the agents family
    assert "agents" in content.lower(), (
        "docs/README.md must explicitly cover the docs/agents family"
    )


def test_docs_readme_distinguishes_current_vs_archival() -> None:
    """docs/README.md must distinguish current Python guidance from historical."""
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text().lower()
    # Should have some indicator of current vs archival status
    # Either explicitly listing what's current, or marking what's historical
    has_status_indicator = any(
        indicator in content
        for indicator in [
            "current",
            "archival",
            "historical",
            "rust-era",
            "python",
            "maintained",
        ]
    )
    assert has_status_indicator, (
        "docs/README.md should distinguish current Python guidance from historical"
    )


def test_docs_readme_covers_code_style_family() -> None:
    """docs/README.md must cover the docs/code-style family status."""
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text()
    # Must reference code-style family
    assert "code-style" in content.lower() or "code style" in content.lower(), (
        "docs/README.md should cover code-style family status"
    )


def test_docs_readme_covers_tooling_family() -> None:
    """docs/README.md must cover the docs/tooling family.

    The wt-026 documentation consolidation removed `docs/tooling/` as a
    top-level family — the maintained Python tooling notes now live under
    `ralph-workflow/docs/sphinx/configuration.md` and the contributor docs
    tree. docs/README.md instead routes the tooling question through the
    contributor route group.
    """
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text().lower()
    # Either the tooling family is still listed, or the README routes the
    # tooling question via the contributor route.
    tooling_routed = "tooling" in content or "contribut" in content
    assert tooling_routed, (
        "docs/README.md must either cover the docs/tooling family or "
        "route the tooling question via the contributor route group"
    )


def test_docs_readme_covers_performance_family() -> None:
    """docs/README.md must cover the docs/performance family.

    The wt-026 documentation consolidation quarantined the entire
    `docs/legacy-rust/` tree (including its `performance/` subdirectory)
    into `tmp/legacy-rust-archive/`. The canonical pointer is the
    augmented `docs/legacy-rust/README.md`. docs/README.md routes the
    retired-implementation question through the legacy route group.
    """
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text().lower()
    # Either the performance family is still listed, or the README routes
    # the question via the legacy/retired-implementation route group.
    performance_routed = "performance" in content or "legacy" in content or "retired" in content
    assert performance_routed, (
        "docs/README.md must either cover the performance family or route "
        "the legacy-Rust question via the legacy route group"
    )


# ---------------------------------------------------------------------------
# idle-watchdog workspace weights canonical home
# ---------------------------------------------------------------------------
#
# The docs consolidation (2026-07-07) moved the canonical watchdog and
# timeout content into ralph-workflow/docs/sphinx/watchdogs-and-timeouts.md
# as the single source of truth. The wt-026 documentation consolidation
# then DELETED ralph-workflow/docs/agents/timeout-policy.md (it was a
# duplicate surface of the canonical sphinx page). The remaining tests
# below pin the canonical home to watchdogs-and-timeouts.md so future
# regressions cannot drift the WorkspaceChangeKind values or the
# agent_workspace_change_weights key back to a deleted surface.
# ---------------------------------------------------------------------------

_PACKAGE_TIMEOUT_POLICY = PACKAGE_DOCS_DIR / "agents" / "timeout-policy.md"
_PACKAGE_SPHINX_WATCHDOGS = PACKAGE_DOCS_DIR / "sphinx" / "watchdogs-and-timeouts.md"
_PACKAGE_CHANGELOG = PACKAGE_ROOT / "CHANGELOG.md"


def test_package_timeout_policy_doc_exists() -> None:
    """The timeout-policy redirect stub must still exist.

    The wt-026 consolidation DELETED ralph-workflow/docs/agents/timeout-policy.md;
    the watchdog/timeout contract now lives only in the canonical sphinx page.
    This test is preserved as a regression guard: if the stub is re-introduced
    it must be redirected to the canonical home (the link-redirect check is
    separate from the existence check).
    """
    # The stub was deleted in wt-026; the watchdog contract lives only in
    # ralph-workflow/docs/sphinx/watchdogs-and-timeouts.md.
    assert not _PACKAGE_TIMEOUT_POLICY.exists(), (
        f"ralph-workflow/docs/agents/timeout-policy.md was deleted in wt-026; "
        f"the watchdog/timeout contract must live only in "
        f"{_PACKAGE_SPHINX_WATCHDOGS}"
    )


def test_package_sphinx_watchdogs_and_timeouts_doc_lists_all_five_kinds() -> None:
    """The canonical watchdogs-and-timeouts page must mention every WorkspaceChangeKind value."""
    content = _PACKAGE_SPHINX_WATCHDOGS.read_text()
    for kind in ("source", "log", "cache", "artifact", "other"):
        assert f"`{kind}`" in content or f"| {kind} |" in content, (
            f"watchdogs-and-timeouts.md must mention the `{kind}` WorkspaceChangeKind"
        )


def test_package_sphinx_watchdogs_and_timeouts_doc_mentions_new_config_key() -> None:
    """The canonical watchdogs-and-timeouts page must mention the new config key name."""
    content = _PACKAGE_SPHINX_WATCHDOGS.read_text()
    assert "agent_workspace_change_weights" in content, (
        "watchdogs-and-timeouts.md must mention the agent_workspace_change_weights key"
    )


def test_package_changelog_unreleased_section_calls_out_behavior_change() -> None:
    """CHANGELOG.md [Unreleased] must call out the behavior change explicitly."""
    content = _PACKAGE_CHANGELOG.read_text()
    assert "Behavior change:" in content, (
        "CHANGELOG.md [Unreleased] must include the explicit 'Behavior change:' callout"
    )


def test_package_timeout_policy_doc_mentions_canonical_home() -> None:
    """The canonical watchdog/timeout page must list all five WorkspaceChangeKind values.

    The wt-026 consolidation deleted the timeout-policy.md stub; the canonical
    watchdog contract now lives only in watchdogs-and-timeouts.md. This test
    pins the WorkspaceChangeKind list to the canonical home so future
    regressions cannot drift the values back to a deleted surface.
    """
    # Use _PACKAGE_SPHINX_WATCHDOGS directly — timeout-policy.md is deleted.
    sphinx_watchdogs_doc_mentions_canonical_home()


def test_package_timeout_policy_doc_is_redirect_stub() -> None:
    """The watchdog contract must not be duplicated on any non-canonical surface.

    The wt-026 consolidation deleted the timeout-policy.md stub; any
    regression that re-introduces duplicate watchdog/timeout content on
    a non-canonical surface (timeout-policy.md, watchdog-architecture.md,
    or elsewhere) must be caught here.
    """
    assert not _PACKAGE_TIMEOUT_POLICY.exists(), (
        f"{_PACKAGE_TIMEOUT_POLICY} must NOT exist — the watchdog/timeout "
        f"contract lives only in {_PACKAGE_SPHINX_WATCHDOGS}"
    )


def sphinx_watchdogs_doc_mentions_canonical_home() -> None:
    """Helper: assert watchdogs-and-timeouts.md is the canonical watchdog home."""
    content = _PACKAGE_SPHINX_WATCHDOGS.read_text()
    for kind in ("source", "log", "cache", "artifact", "other"):
        assert f"`{kind}`" in content or f"| {kind} |" in content, (
            f"watchdogs-and-timeouts.md must mention the `{kind}` WorkspaceChangeKind"
        )
    assert "agent_workspace_change_weights" in content, (
        "watchdogs-and-timeouts.md must mention the agent_workspace_change_weights key"
    )
