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
    """docs/README.md must cover the docs/tooling family."""
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text()
    # Must reference tooling family
    assert "tooling" in content.lower(), "docs/README.md should cover docs/tooling family"


def test_docs_readme_covers_performance_family() -> None:
    """docs/README.md must cover the docs/performance family."""
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text()
    # Must reference performance family
    assert "performance" in content.lower(), "docs/README.md should cover docs/performance family"


# ---------------------------------------------------------------------------
# idle-watchdog workspace weights canonical home
# ---------------------------------------------------------------------------
#
# The docs consolidation (2026-07-07) moved the canonical watchdog and
# timeout content into ralph-workflow/docs/sphinx/watchdogs-and-timeouts.md
# as the single source of truth. ralph-workflow/docs/agents/timeout-policy.md
# is now a one-paragraph redirect stub. The tests below pin the canonical
# home to watchdogs-and-timeouts.md so future regressions cannot drift the
# WorkspaceChangeKind values or the agent_workspace_change_weights key back
# to a deleted surface.
# ---------------------------------------------------------------------------

_PACKAGE_TIMEOUT_POLICY = PACKAGE_DOCS_DIR / "agents" / "timeout-policy.md"
_PACKAGE_SPHINX_WATCHDOGS = PACKAGE_DOCS_DIR / "sphinx" / "watchdogs-and-timeouts.md"
_PACKAGE_CHANGELOG = PACKAGE_ROOT / "CHANGELOG.md"


def test_package_timeout_policy_doc_exists() -> None:
    """The timeout-policy redirect stub must still exist."""
    assert _PACKAGE_TIMEOUT_POLICY.exists(), (
        f"ralph-workflow/docs/agents/timeout-policy.md must exist ({_PACKAGE_TIMEOUT_POLICY})"
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
    """timeout-policy.md must point at the canonical watchdog/timeout page.

    The consolidation moved the WorkspaceChangeKind values and the
    agent_workspace_change_weights key into
    ralph-workflow/docs/sphinx/watchdogs-and-timeouts.md as the
    canonical home. This test pins the redirect-stub link so future
    regressions cannot silently re-introduce duplicate content on
    timeout-policy.md.
    """
    content = _PACKAGE_TIMEOUT_POLICY.read_text()
    assert "../sphinx/watchdogs-and-timeouts.md" in content, (
        "timeout-policy.md must link to the canonical watchdogs-and-timeouts.md page"
    )


def test_package_timeout_policy_doc_is_redirect_stub() -> None:
    """timeout-policy.md must be a short redirect stub (not substantive content)."""
    content = _PACKAGE_TIMEOUT_POLICY.read_text()
    line_count = len(content.splitlines())
    assert line_count <= 10, (
        f"timeout-policy.md must be a one-paragraph redirect stub (got {line_count} lines)"
    )
