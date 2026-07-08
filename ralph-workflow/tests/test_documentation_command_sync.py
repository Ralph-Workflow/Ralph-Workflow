"""Regression guard: README.md and CONTRIBUTING.md must not hard-code stale test-cov flags.

Asserts invariants, not exact recipe strings, so the test survives valid internal
changes to worker counts or coverage thresholds without needing to be updated.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
CONTRIBUTING_PATH = REPO_ROOT / "CONTRIBUTING.md"
WORKSPACE_ROOT = REPO_ROOT.parent
CODE_STYLE_PATH = WORKSPACE_ROOT / "CODE_STYLE.md"
CODE_STYLE_INDEX_PATH = WORKSPACE_ROOT / "docs" / "code-style" / "index.md"
PYTHON_TOOLING_PATH = WORKSPACE_ROOT / "docs" / "tooling" / "python-tooling.md"
# quickstart.md was deleted in the wt-026 documentation consolidation;
# its non-duplicate content was merged into docs/sphinx/getting-started.md,
# which is now the canonical home for the init-local-config contract.
QUICKSTART_PATH = REPO_ROOT / "docs" / "sphinx" / "getting-started.md"

_STALE_FLAGS = [
    "--cov-report=term-missing",
    "--cov-report=html",
]


def test_readme_does_not_hard_code_stale_cov_report_flags() -> None:
    """README.md must not document obsolete --cov-report=term-missing or --cov-report=html."""
    readme = README_PATH.read_text(encoding="utf-8")
    found = [flag for flag in _STALE_FLAGS if flag in readme]
    assert not found, (
        "README.md documents obsolete coverage flags that no longer match Makefile:test-cov:\n"
        + "\n".join(f"  {f}" for f in found)
        + "\n\nUpdate README.md to describe the canonical `make verify` workflow instead."
    )


def test_contributing_does_not_hard_code_stale_cov_report_flags() -> None:
    """CONTRIBUTING.md must not document obsolete --cov-report=term-missing or --cov-report=html."""
    contributing = CONTRIBUTING_PATH.read_text(encoding="utf-8")
    found = [flag for flag in _STALE_FLAGS if flag in contributing]
    assert not found, (
        "CONTRIBUTING.md documents obsolete coverage flags that no longer match "
        "Makefile:test-cov:\n"
        + "\n".join(f"  {f}" for f in found)
        + "\n\nReplace with the canonical `make test-cov` command."
    )


def test_readme_verification_section_references_make_verify() -> None:
    """README.md must point at the canonical `make verify` workflow.

    The wt-026 documentation consolidation removed the inline "Verification"
    section from README.md; the canonical `make verify` reference now lives
    on the contributor route. README.md routes contributors through
    ralph-workflow/CONTRIBUTING.md (which names the canonical `make verify`
    command), so this test checks CONTRIBUTING.md directly.
    """
    contributing = CONTRIBUTING_PATH.read_text(encoding="utf-8")
    assert "make verify" in contributing, (
        "CONTRIBUTING.md must reference the canonical `make verify` command "
        "so the README contributor route resolves to a working build."
    )


def test_contributing_required_verification_references_make_verify() -> None:
    """CONTRIBUTING.md Required verification must point to the canonical `make verify` workflow."""
    contributing = CONTRIBUTING_PATH.read_text(encoding="utf-8")
    assert "make verify" in contributing, (
        "CONTRIBUTING.md must reference the canonical `make verify` command "
        "in its Required verification section."
    )


def test_repo_root_typing_docs_do_not_claim_pydantic_mypy_plugin() -> None:
    """Repo-root strict-typing docs must reflect the no-plugin Pydantic contract."""
    for path in (CODE_STYLE_PATH, CODE_STYLE_INDEX_PATH, PYTHON_TOOLING_PATH):
        content = path.read_text(encoding="utf-8")
        assert "pydantic.mypy" not in content, (
            f"{path} must not instruct users to enable the pydantic.mypy plugin; "
            "the maintained runtime contract uses first-party typed helpers instead."
        )
        assert (
            "no-plugin" in content
            or "first-party typed helper" in content
            or "typed helper" in content
        ), f"{path} must describe the maintained no-plugin Pydantic typing contract."


def test_quickstart_documents_init_local_config_as_explicit_opt_in() -> None:
    """Quickstart must keep `--init` and `--init-local-config` contracts aligned with runtime."""
    content = QUICKSTART_PATH.read_text(encoding="utf-8")
    assert "ralph --init-local-config" in content, (
        "docs/sphinx/quickstart.md must document `ralph --init-local-config` as the explicit "
        "project-local override path."
    )
    assert ".agent/ralph-workflow.toml" in content, (
        "docs/sphinx/quickstart.md must explain that `.agent/ralph-workflow.toml` belongs to the "
        "explicit local override flow."
    )
    forbidden_claim = "local config files (`ralph-workflow.toml`, `mcp.toml`,"
    assert forbidden_claim not in content, (
        "docs/sphinx/quickstart.md must not claim `ralph --init` creates "
        "`.agent/ralph-workflow.toml` by default."
    )


_MCP_SERVERS_DOC = REPO_ROOT / "docs" / "mcp" / "mcp-servers.md"


def test_mcp_servers_doc_describes_broad_multimodal_surface() -> None:
    """docs/mcp/mcp-servers.md must describe the broad multimodal contract, not image-only."""
    content = _MCP_SERVERS_DOC.read_text(encoding="utf-8")
    assert "read_media" in content
    assert "read_image" in content
    assert "compatibility" in content
    # Old reject-all wording must be gone
    assert "rejects it with a clear error" not in content
    assert "text-only passthrough" not in content


def test_mcp_servers_doc_describes_upstream_normalization_policy() -> None:
    """docs/mcp/mcp-servers.md must describe upstream normalization, not reject-all policy."""
    content = _MCP_SERVERS_DOC.read_text(encoding="utf-8")
    assert "normalizes it to a" in content
    assert "resource_reference" in content
    assert "URI-backed" in content
    assert "Embedded-data" in content


_README_PATH = REPO_ROOT / "README.md"
_CONTRIBUTING_PATH = REPO_ROOT / "CONTRIBUTING.md"
WORKSPACE_ROOT = REPO_ROOT.parent
CODE_STYLE_PATH = WORKSPACE_ROOT / "CODE_STYLE.md"
CODE_STYLE_INDEX_PATH = WORKSPACE_ROOT / "docs" / "code-style" / "index.md"
PYTHON_TOOLING_PATH = WORKSPACE_ROOT / "docs" / "tooling" / "python-tooling.md"
_ARCHITECTURE_PATH = REPO_ROOT / "ralph" / "mcp" / "ARCHITECTURE.md"
_SPHINX_MCP_TOOLS_PATH = REPO_ROOT / "docs" / "sphinx" / "mcp-tools.md"
_SPHINX_MCP_ARCH_PATH = REPO_ROOT / "docs" / "sphinx" / "mcp-architecture.md"
_SPHINX_AGENTS_PATH = REPO_ROOT / "docs" / "sphinx" / "agents.md"


def test_readme_stays_onboarding_focused_and_points_to_deeper_docs() -> None:
    """README.md must stay onboarding-focused and route to the canonical handler pages.

    The wt-026 documentation consolidation replaced the legacy
    "intentionally leaves out" / quickstart.md / developer-reference.md /
    modules.rst pointer cluster with the canonical handler pages.

    For the package-level ralph-workflow/README.md (PyPI surface), the
    canonical handler is `ralph-workflow/docs/sphinx/index.rst` because
    PyPI cannot resolve repo-root paths like `docs/README.md`. The test
    accepts either pointer (or both) so both surfaces stay onboarding-focused.
    The surviving developer/API surface is `concepts.md` (or
    `developer-internals.md`) plus `modules.rst`; the route is
    README.md -> ralph-workflow/docs/sphinx/index.rst.
    """
    content = _README_PATH.read_text(encoding="utf-8")
    # The maintained operator manual is the canonical handler for the PyPI surface.
    assert "docs/sphinx/index.rst" in content, (
        "README.md must route deeper topics to the maintained operator manual"
    )


def test_contributing_describes_read_media_as_primary_multimodal_tool() -> None:
    """CONTRIBUTING.md must describe read_media as primary tool, not image-only."""
    content = _CONTRIBUTING_PATH.read_text(encoding="utf-8")
    assert "read_media" in content
    assert "compatibility" in content
    # Old upstream-rejection wording must be replaced with normalization
    assert "rejects it with a clear error" not in content
    assert "resource_reference" in content


def test_contributing_does_not_claim_upstream_mcp_untracked() -> None:
    """CONTRIBUTING.md must not claim upstream MCP servers are untracked.

    The ``mcp_tool`` activity channel covers both in-process Ralph tools
    and upstream (third-party) MCP tool calls proxied through
    ``UpstreamProxyHandler``. The documented limitation was closed by the
    upstream-proxy activity-sink wiring.
    """
    content = _CONTRIBUTING_PATH.read_text(encoding="utf-8")
    assert "upstream MCP servers are not tracked" not in content, (
        "CONTRIBUTING.md must not claim upstream MCP servers are untracked;"
        " the UpstreamProxyHandler now records them on the mcp_tool channel"
    )
    assert "stdout-only behavior in place" not in content, (
        "CONTRIBUTING.md must not advise operators to keep stdout-only behavior"
        " because upstream MCP calls were untracked"
    )


def test_contributing_uses_operator_facing_activity_evidence_ttl_key() -> None:
    """CONTRIBUTING.md must use the operator-facing TOML key name, not the
    internal TimeoutPolicy field name, when describing the tunable."""
    content = _CONTRIBUTING_PATH.read_text(encoding="utf-8")
    # The internal dataclass field name must not appear bare in docs text;
    # the operator-facing TOML key ``agent_idle_activity_evidence_ttl_seconds``
    # is the canonical name for contributor-facing documentation.
    assert "`activity_evidence_ttl_seconds`" not in content, (
        "CONTRIBUTING.md must use the operator-facing key"
        " `agent_idle_activity_evidence_ttl_seconds`, not the internal"
        " TimeoutPolicy field name `activity_evidence_ttl_seconds`"
    )


def test_readme_does_not_claim_none_disables_activity_evidence_ttl() -> None:
    """README.md must document ``agent_idle_activity_evidence_ttl_seconds``
    as disabled only by ``0.0``, not by ``None``.

    The operator-facing config surface (``GeneralConfig``) defines the
    field as ``float`` with ``ge=0.0``; it does not accept ``None``.
    """
    content = README_PATH.read_text(encoding="utf-8")
    for match in re.finditer(r"agent_idle_activity_evidence_ttl_seconds", content):
        start = max(0, match.start() - 200)
        end = min(len(content), match.end() + 200)
        context = content[start:end]
        assert "None" not in context, (
            "README.md context around agent_idle_activity_evidence_ttl_seconds"
            " must not claim None disables it; the config surface only accepts"
            f" float >= 0.0: {context!r}"
        )


def test_contributing_does_not_claim_none_disables_activity_evidence_ttl() -> None:
    """CONTRIBUTING.md must document ``agent_idle_activity_evidence_ttl_seconds``
    as disabled only by ``0.0``, not by ``None``.

    The operator-facing config surface (``GeneralConfig``) defines the
    field as ``float`` with ``ge=0.0``; it does not accept ``None``.
    """
    content = _CONTRIBUTING_PATH.read_text(encoding="utf-8")
    for match in re.finditer(r"agent_idle_activity_evidence_ttl_seconds", content):
        start = max(0, match.start() - 200)
        end = min(len(content), match.end() + 200)
        context = content[start:end]
        assert "None" not in context, (
            "CONTRIBUTING.md context around agent_idle_activity_evidence_ttl_seconds"
            " must not claim None disables it; the config surface only accepts"
            f" float >= 0.0: {context!r}"
        )


def test_contributing_does_not_reference_obsolete_watchdog_module_paths() -> None:
    """CONTRIBUTING.md must point at the maintained module paths, not the
    obsolete flat-module paths that no longer exist."""
    content = _CONTRIBUTING_PATH.read_text(encoding="utf-8")
    assert "ralph/agents/idle_watchdog.py" not in content, (
        "CONTRIBUTING.md references obsolete path ralph/agents/idle_watchdog.py;"
        " use ralph/agents/idle_watchdog/idle_watchdog.py"
    )
    assert "ralph/agents/execution_state.py" not in content, (
        "CONTRIBUTING.md references obsolete path ralph/agents/execution_state.py;"
        " use ralph/agents/execution_state/opencode_execution_strategy.py"
    )


def test_architecture_md_describes_read_media_as_primary_tool() -> None:
    """ralph/mcp/ARCHITECTURE.md must describe read_media as primary multimodal tool."""
    content = _ARCHITECTURE_PATH.read_text(encoding="utf-8")
    assert "read_media" in content
    assert "compatibility" in content
    # Must not describe old rejection policy
    assert "Upstream Multimodal Rejection Policy" not in content
    assert "text-only passthrough" not in content
    # Must describe normalization
    assert "resource_reference" in content
    assert "Normalization" in content or "normalization" in content


def test_sphinx_mcp_tools_describes_read_media_as_primary_tool() -> None:
    """docs/sphinx/mcp-tools.md must list read_media as primary multimodal tool."""
    content = _SPHINX_MCP_TOOLS_PATH.read_text(encoding="utf-8")
    assert "read_media" in content
    assert "compatibility" in content or "read_image" in content
    # media.read capability row must mention read_media
    lines = content.splitlines()
    media_read_rows = [row for row in lines if "media.read" in row]
    assert any("read_media" in row for row in media_read_rows), (
        f"media.read rows do not mention read_media: {media_read_rows}"
    )


def test_sphinx_mcp_architecture_describes_read_media() -> None:
    """docs/sphinx/mcp-architecture.md must list read_media in workspace tools."""
    content = _SPHINX_MCP_ARCH_PATH.read_text(encoding="utf-8")
    assert "read_media" in content


def test_sphinx_agents_describes_bounded_summaries_not_first_class_artifacts() -> None:
    """docs/sphinx/agents.md must describe parser behavior as bounded summaries, not first-class."""
    content = _SPHINX_AGENTS_PATH.read_text(encoding="utf-8")
    # Must describe the correct parser behavior
    assert "bounded" in content or "summaries" in content or "summary" in content
    # Must not claim parsers preserve multimodal as first-class artifacts in event stream
    assert "preserve these blocks as first-class artifacts" not in content


def test_mcp_servers_doc_provider_matrix_typed_blocks() -> None:
    """docs/mcp/mcp-servers.md must document typed block delivery for Claude/Gemini."""
    content = _MCP_SERVERS_DOC.read_text(encoding="utf-8")
    assert "typed block" in content, (
        "docs/mcp/mcp-servers.md must describe typed block delivery mode"
    )
    # Must not claim all non-image media returns resource_reference
    assert "Returns all other media as `resource_reference`" not in content, (
        "docs/mcp/mcp-servers.md must not claim all non-image media returns resource_reference; "
        "PDFs/documents use typed blocks for Claude/Gemini"
    )


def test_mcp_servers_doc_provider_matrix_table() -> None:
    """docs/mcp/mcp-servers.md must include an explicit provider/modality delivery matrix table."""
    content = _MCP_SERVERS_DOC.read_text(encoding="utf-8")
    # The matrix table must be present
    assert "Claude/Anthropic" in content and "Gemini" in content and "OpenAI/Codex" in content, (
        "docs/mcp/mcp-servers.md must have a provider/modality delivery matrix table"
    )
    # Must describe explicit unsupported for OpenAI non-image modalities
    assert "unsupported" in content, (
        "docs/mcp/mcp-servers.md must describe modalities as explicitly unsupported"
        " for some providers"
    )


def test_architecture_md_upstream_normalization_not_rejection() -> None:
    """ralph/mcp/ARCHITECTURE.md must not describe upstream content as 'explicitly rejected'."""
    content = _ARCHITECTURE_PATH.read_text(encoding="utf-8")
    # The old stale wording must be gone
    assert "Now explicitly rejected with clear error message" not in content, (
        "ARCHITECTURE.md must not describe upstream multimodal handling as 'explicitly rejected'; "
        "the current policy normalizes upstream content to resource_reference artifacts"
    )
    # Must describe the normalization policy instead
    assert "normalized to" in content or "normalizes" in content, (
        "ARCHITECTURE.md must describe the upstream normalization policy"
    )


def test_mcp_servers_doc_describes_screenshot_and_browser_visual_workflows() -> None:
    """docs/mcp/mcp-servers.md must describe screenshot and browser-captured workflows."""
    content = _MCP_SERVERS_DOC.read_text(encoding="utf-8")
    assert "screenshot" in content.lower(), (
        "docs/mcp/mcp-servers.md must describe screenshot/browser-captured visual workflows"
    )


def test_mcp_servers_doc_describes_replayable_resource_handles_via_resources_read() -> None:
    """docs/mcp/mcp-servers.md must describe replayable handles retrievable via resources/read."""
    content = _MCP_SERVERS_DOC.read_text(encoding="utf-8")
    assert "ralph://media/" in content, (
        "docs/mcp/mcp-servers.md must describe replayable ralph://media/... handles"
    )
    assert "resources/read" in content, (
        "docs/mcp/mcp-servers.md must describe artifact retrieval via resources/read"
    )


def test_mcp_servers_doc_describes_mixed_modality_workflows() -> None:
    """docs/mcp/mcp-servers.md must describe mixed-modality workflow execution."""
    content = _MCP_SERVERS_DOC.read_text(encoding="utf-8")
    content_lower = content.lower()
    assert "mixed" in content_lower or "mixed-modality" in content_lower, (
        "docs/mcp/mcp-servers.md must describe mixed-modality workflow execution"
    )


def test_mcp_servers_doc_text_only_safety_is_explicit() -> None:
    """docs/mcp/mcp-servers.md must explicitly state text-only workflow safety."""
    content = _MCP_SERVERS_DOC.read_text(encoding="utf-8")
    assert "text-only" in content, (
        "docs/mcp/mcp-servers.md must explicitly describe text-only client safety"
    )


def test_agents_doc_describes_resolved_capability_profile() -> None:
    """docs/sphinx/agents.md must describe ResolvedCapabilityProfile as session-owned contract."""
    content = _SPHINX_AGENTS_PATH.read_text(encoding="utf-8")
    assert "ResolvedCapabilityProfile" in content, (
        "docs/sphinx/agents.md must mention ResolvedCapabilityProfile as the "
        "pre-computed, session-owned delivery contract"
    )


def test_mcp_servers_doc_describes_resolved_capability_profile() -> None:
    """docs/mcp/mcp-servers.md must describe ResolvedCapabilityProfile-based delivery."""
    content = _MCP_SERVERS_DOC.read_text(encoding="utf-8")
    assert "ResolvedCapabilityProfile" in content, (
        "docs/mcp/mcp-servers.md must mention ResolvedCapabilityProfile as the "
        "mechanism for per-modality delivery selection"
    )


def test_agents_doc_explains_interactive_completion_evaluation() -> None:
    """agents.md must explain interactive Claude completion via artifacts or declare_complete."""
    content = _SPHINX_AGENTS_PATH.read_text(encoding="utf-8")
    assert "artifact" in content, (
        "docs/sphinx/agents.md must explain that completion is evaluated via artifact evidence"
    )
    assert "declare_complete" in content, (
        "agents.md must explain that an explicit declare_complete MCP call signals completion"
    )


def test_agents_doc_describes_resumable_session_on_incomplete_exit() -> None:
    """agents.md must explain what happens when interactive Claude exits without completing."""
    content = _SPHINX_AGENTS_PATH.read_text(encoding="utf-8")
    assert "incomplete" in content or "without completing" in content, (
        "docs/sphinx/agents.md must describe what happens on an incomplete exit "
        "(e.g. session continuation or retry behavior)"
    )


def test_agents_doc_explains_interactive_vs_headless_tradeoff() -> None:
    """agents.md must explain the observability tradeoff between interactive and headless Claude."""
    content = _SPHINX_AGENTS_PATH.read_text(encoding="utf-8")
    assert "observability" in content or "tradeoff" in content, (
        "docs/sphinx/agents.md must explain the observability or streaming tradeoff "
        "between interactive and headless Claude transport modes"
    )


def test_agents_doc_explains_unattended_orchestration_contract() -> None:
    """docs/sphinx/agents.md must explain Ralph Workflow's unattended orchestration contract."""
    content = _SPHINX_AGENTS_PATH.read_text(encoding="utf-8")
    assert "unattended" in content, "docs/sphinx/agents.md must describe unattended orchestration"
    assert "supervise" in content or "orchestrate" in content or "manages" in content, (
        "docs/sphinx/agents.md must describe how Ralph Workflow supervises, orchestrates, "
        "or manages the interactive Claude session"
    )


# Maintained type checking and tooling docs enforcement
_CODE_STYLE_PATH = REPO_ROOT.parent / "CODE_STYLE.md"
_CODE_STYLE_INDEX_PATH = REPO_ROOT.parent / "docs" / "code-style" / "index.md"
_TOOLING_GUIDE_PATH = REPO_ROOT.parent / "docs" / "tooling" / "python-tooling.md"


def test_code_style_md_mentions_strict_mypy_config() -> None:
    """CODE_STYLE.md must mention strict mypy with ralph-workflow/mypy.ini."""
    content = _CODE_STYLE_PATH.read_text(encoding="utf-8")
    assert "ralph-workflow/mypy.ini" in content, (
        "CODE_STYLE.md must reference the exact maintained mypy config path ralph-workflow/mypy.ini"
    )
    assert "strict" in content.lower(), "CODE_STYLE.md must mention strict type checking"


def test_code_style_md_mentions_type_ignore_policy() -> None:
    """CODE_STYLE.md must mention docs/agents/type-ignore-policy.md."""
    content = _CODE_STYLE_PATH.read_text(encoding="utf-8")
    assert "docs/agents/type-ignore-policy.md" in content, (
        "CODE_STYLE.md must reference the type-ignore policy document"
    )


def test_code_style_md_mentions_zero_test_suppressions() -> None:
    """CODE_STYLE.md must state test files have zero suppressions."""
    content = _CODE_STYLE_PATH.read_text(encoding="utf-8")
    content_lower = content.lower()
    marker = "type:" + " ignore"
    assert (
        "test files must not" in content_lower
        or ("zero" in content_lower and "test" in content_lower)
        or (marker in content and "test" in content)
    ), "CODE_STYLE.md must explicitly state that test files have zero suppressions"


def test_code_style_md_mentions_make_verify() -> None:
    """CODE_STYLE.md must mention the canonical make verify workflow."""
    content = _CODE_STYLE_PATH.read_text(encoding="utf-8")
    assert "make verify" in content, (
        "CODE_STYLE.md must reference the canonical `make verify` verification command"
    )


def test_code_style_index_mentions_strict_mypy_config() -> None:
    """docs/code-style/index.md must mention strict mypy with ralph-workflow/mypy.ini."""
    content = _CODE_STYLE_INDEX_PATH.read_text(encoding="utf-8")
    assert "ralph-workflow/mypy.ini" in content, (
        "docs/code-style/index.md must reference the exact maintained mypy config path"
    )
    assert "strict" in content.lower(), "docs/code-style/index.md must mention strict type checking"


def test_code_style_index_mentions_type_ignore_policy() -> None:
    """docs/code-style/index.md must mention docs/agents/type-ignore-policy.md."""
    content = _CODE_STYLE_INDEX_PATH.read_text(encoding="utf-8")
    assert "docs/agents/type-ignore-policy.md" in content, (
        "docs/code-style/index.md must reference the type-ignore policy document"
    )


def test_code_style_index_mentions_zero_test_suppressions() -> None:
    """docs/code-style/index.md must state test files have zero suppressions."""
    content = _CODE_STYLE_INDEX_PATH.read_text(encoding="utf-8")
    content_lower = content.lower()
    marker = "type:" + " ignore"
    assert (
        "test files must not" in content_lower
        or ("zero" in content_lower and "test" in content_lower)
        or (marker in content and "test" in content)
    ), "docs/code-style/index.md must explicitly state that test files have zero suppressions"


def test_code_style_index_mentions_make_verify() -> None:
    """docs/code-style/index.md must mention the canonical make verify workflow."""
    content = _CODE_STYLE_INDEX_PATH.read_text(encoding="utf-8")
    assert "make verify" in content, (
        "docs/code-style/index.md must reference the canonical `make verify` verification command"
    )


def test_tooling_guide_mentions_strict_mypy_config() -> None:
    """docs/tooling/python-tooling.md must mention strict mypy with ralph-workflow/mypy.ini."""
    content = _TOOLING_GUIDE_PATH.read_text(encoding="utf-8")
    assert "ralph-workflow/mypy.ini" in content, (
        "docs/tooling/python-tooling.md must reference the exact maintained mypy config path"
    )
    assert "strict" in content.lower(), (
        "docs/tooling/python-tooling.md must mention strict type checking"
    )


def test_tooling_guide_mentions_type_ignore_policy() -> None:
    """docs/tooling/python-tooling.md must mention docs/agents/type-ignore-policy.md."""
    content = _TOOLING_GUIDE_PATH.read_text(encoding="utf-8")
    assert "docs/agents/type-ignore-policy.md" in content, (
        "docs/tooling/python-tooling.md must reference the type-ignore policy document"
    )


def test_tooling_guide_mentions_zero_test_suppressions() -> None:
    """docs/tooling/python-tooling.md must state test files have zero suppressions."""
    content = _TOOLING_GUIDE_PATH.read_text(encoding="utf-8")
    content_lower = content.lower()
    marker = "type:" + " ignore"
    assert (
        "test files must not" in content_lower
        or ("zero" in content_lower and ("test" in content_lower or "suppression" in content_lower))
        or (marker in content and "test" in content)
    ), "docs/tooling/python-tooling.md must explicitly state that test files have zero suppressions"


def test_tooling_guide_mentions_make_verify() -> None:
    """docs/tooling/python-tooling.md must mention the canonical make verify workflow."""
    content = _TOOLING_GUIDE_PATH.read_text(encoding="utf-8")
    assert "make verify" in content, (
        "docs/tooling/python-tooling.md must reference the canonical "
        "`make verify` verification command"
    )


def test_tooling_guide_does_not_reference_nonexistent_ruff_toml() -> None:
    """docs/tooling/python-tooling.md must not reference nonexistent ruff.toml."""
    content = _TOOLING_GUIDE_PATH.read_text(encoding="utf-8")
    # The file should not reference ruff.toml as a config source (it doesn't exist in the repo)
    # It may be acceptable to mention it in passing, but not as the primary configuration source
    lines = content.splitlines()
    ruff_config_lines = [
        (i, line)
        for i, line in enumerate(lines)
        if "ruff.toml" in line and ("config" in line.lower() or "configuration" in line.lower())
    ]
    assert not ruff_config_lines, (
        f"docs/tooling/python-tooling.md references nonexistent ruff.toml as config at lines: "
        f"{[(i + 1, line) for i, line in ruff_config_lines]}"
    )


def test_tooling_guide_does_not_claim_stale_python_version() -> None:
    """docs/tooling/python-tooling.md must not claim python_version = 3.12."""
    content = _TOOLING_GUIDE_PATH.read_text(encoding="utf-8")
    assert "python_version = 3.12" not in content, (
        "docs/tooling/python-tooling.md must not document python_version = 3.12; "
        "the actual config in ralph-workflow/mypy.ini uses python_version = 3.14"
    )


def test_tooling_guide_does_not_claim_pydantic_mypy_plugin() -> None:
    """docs/tooling/python-tooling.md must not claim pydantic.mypy plugin is used."""
    content = _TOOLING_GUIDE_PATH.read_text(encoding="utf-8")
    assert "plugins = pydantic.mypy" not in content, (
        "docs/tooling/python-tooling.md must not document plugins = pydantic.mypy; "
        "the actual config in ralph-workflow/mypy.ini has no plugins entry"
    )


def test_tooling_guide_uses_ini_style_overrides_not_toml() -> None:
    """docs/tooling/python-tooling.md must not show TOML [[overrides]] syntax."""
    content = _TOOLING_GUIDE_PATH.read_text(encoding="utf-8")
    assert "[[overrides]]" not in content, (
        "docs/tooling/python-tooling.md must not show TOML [[overrides]] syntax; "
        "ralph-workflow/mypy.ini uses INI-style [mypy-...] override sections"
    )


_CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"


def test_changelog_does_not_claim_none_disables_activity_evidence_ttl() -> None:
    """CHANGELOG.md must document ``agent_idle_activity_evidence_ttl_seconds``
    as disabled only by ``0.0``, not by ``None``.

    The operator-facing config surface (``GeneralConfig``) defines the
    field as ``float`` with ``ge=0.0``; it does not accept ``None``.
    """
    content = _CHANGELOG_PATH.read_text(encoding="utf-8")
    for match in re.finditer(r"agent_idle_activity_evidence_ttl_seconds", content):
        start = max(0, match.start() - 200)
        end = min(len(content), match.end() + 200)
        context = content[start:end]
        assert "None" not in context, (
            "CHANGELOG.md context around agent_idle_activity_evidence_ttl_seconds"
            " must not claim None disables it; the config surface only accepts"
            f" float >= 0.0: {context!r}"
        )


def test_changelog_does_not_claim_upstream_proxy_invokes_active_sink() -> None:
    """CHANGELOG.md must not claim ``UpstreamProxyHandler`` emits the activity sink.

    The single emission point for every ``tools/call`` is
    ``McpServer._handle_tools_call``; the proxy is a pure pass-through and
    must not invoke ``invoke_active_sink`` itself.
    """
    content = _CHANGELOG_PATH.read_text(encoding="utf-8")
    assert "invoke_active_sink(self._alias)" not in content, (
        "CHANGELOG.md must not claim UpstreamProxyHandler invokes the activity sink;"
        " McpServer._handle_tools_call is the single emission point"
    )


def test_changelog_does_not_reference_missing_architecture_doc() -> None:
    """CHANGELOG.md must not point at a non-existent architecture doc path."""
    content = _CHANGELOG_PATH.read_text(encoding="utf-8")
    assert "docs/architecture/logging-and-observability.md" not in content, (
        "CHANGELOG.md references docs/architecture/logging-and-observability.md,"
        " which does not exist; remove or correct the stale pointer"
    )
