from tests.doc_roots import PACKAGE_DOCS_DIR, PACKAGE_ROOT, REPO_ROOT_DOCS_DIR


def test_docs_readme_exists() -> None:
    path = REPO_ROOT_DOCS_DIR / "README.md"
    assert path.exists(), "docs/README.md must exist"


def test_docs_readme_maps_agents_family() -> None:
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text()
    assert "agents" in content.lower(), (
        "docs/README.md must explicitly cover the docs/agents family"
    )


def test_docs_readme_distinguishes_current_vs_archival() -> None:
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text().lower()
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
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text()
    assert "code-style" in content.lower() or "code style" in content.lower(), (
        "docs/README.md should cover code-style family status"
    )


def test_docs_readme_covers_tooling_family() -> None:
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text().lower()
    tooling_routed = "tooling" in content or "contribut" in content
    assert tooling_routed, (
        "docs/README.md must either cover the docs/tooling family or "
        "route the tooling question via the contributor route group"
    )


def test_docs_readme_covers_performance_family() -> None:
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text().lower()
    performance_routed = "performance" in content or "legacy" in content or "retired" in content
    assert performance_routed, (
        "docs/README.md must either cover the performance family or route "
        "the legacy-Rust question via the legacy route group"
    )


_PACKAGE_TIMEOUT_POLICY = PACKAGE_DOCS_DIR / "agents" / "timeout-policy.md"
_PACKAGE_SPHINX_WATCHDOGS = PACKAGE_DOCS_DIR / "sphinx" / "concepts.md"
_PACKAGE_CHANGELOG = PACKAGE_ROOT / "CHANGELOG.md"


def test_package_timeout_policy_doc_exists() -> None:
    assert not _PACKAGE_TIMEOUT_POLICY.exists(), (
        f"ralph-workflow/docs/agents/timeout-policy.md was deleted in wt-026; "
        f"the watchdog/timeout contract must live only in "
        f"{_PACKAGE_SPHINX_WATCHDOGS}"
    )


def test_package_sphinx_watchdogs_and_timeouts_doc_lists_all_five_kinds() -> None:
    content = _PACKAGE_SPHINX_WATCHDOGS.read_text()
    for kind in ("source", "log", "cache", "artifact", "other"):
        assert f"`{kind}`" in content or f"| {kind} |" in content, (
            f"concepts.md#watchdogs must mention the `{kind}` WorkspaceChangeKind"
        )


def test_package_sphinx_watchdogs_and_timeouts_doc_mentions_new_config_key() -> None:
    content = _PACKAGE_SPHINX_WATCHDOGS.read_text()
    assert "agent_workspace_change_weights" in content, (
        "concepts.md#watchdogs must mention the agent_workspace_change_weights key"
    )


def test_package_changelog_unreleased_section_calls_out_behavior_change() -> None:
    content = _PACKAGE_CHANGELOG.read_text()
    assert "Behavior change:" in content, (
        "CHANGELOG.md [Unreleased] must include the explicit 'Behavior change:' callout"
    )


def test_package_timeout_policy_doc_mentions_canonical_home() -> None:
    sphinx_watchdogs_doc_mentions_canonical_home()


def test_package_timeout_policy_doc_is_redirect_stub() -> None:
    assert not _PACKAGE_TIMEOUT_POLICY.exists(), (
        f"{_PACKAGE_TIMEOUT_POLICY} must NOT exist — the watchdog/timeout "
        f"contract lives only in {_PACKAGE_SPHINX_WATCHDOGS}"
    )


def sphinx_watchdogs_doc_mentions_canonical_home() -> None:
    content = _PACKAGE_SPHINX_WATCHDOGS.read_text()
    for kind in ("source", "log", "cache", "artifact", "other"):
        assert f"`{kind}`" in content or f"| {kind} |" in content, (
            f"concepts.md#watchdogs must mention the `{kind}` WorkspaceChangeKind"
        )
    assert "agent_workspace_change_weights" in content, (
        "concepts.md#watchdogs must mention the agent_workspace_change_weights key"
    )
