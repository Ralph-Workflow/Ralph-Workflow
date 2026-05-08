"""Regression guard: README.md and CONTRIBUTING.md must not hard-code stale test-cov flags.

Asserts invariants, not exact recipe strings, so the test survives valid internal
changes to worker counts or coverage thresholds without needing to be updated.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
CONTRIBUTING_PATH = REPO_ROOT / "CONTRIBUTING.md"

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
    """README.md Verification section must point to the canonical `make verify` workflow."""
    readme = README_PATH.read_text(encoding="utf-8")
    assert "make verify" in readme, (
        "README.md must reference the canonical `make verify` command in its Verification section."
    )


def test_contributing_required_verification_references_make_verify() -> None:
    """CONTRIBUTING.md Required verification must point to the canonical `make verify` workflow."""
    contributing = CONTRIBUTING_PATH.read_text(encoding="utf-8")
    assert "make verify" in contributing, (
        "CONTRIBUTING.md must reference the canonical `make verify` command "
        "in its Required verification section."
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
_ARCHITECTURE_PATH = REPO_ROOT / "ralph" / "mcp" / "ARCHITECTURE.md"
_SPHINX_MCP_TOOLS_PATH = REPO_ROOT / "docs" / "sphinx" / "mcp-tools.md"
_SPHINX_MCP_ARCH_PATH = REPO_ROOT / "docs" / "sphinx" / "mcp-architecture.md"
_SPHINX_AGENTS_PATH = REPO_ROOT / "docs" / "sphinx" / "agents.md"


def test_readme_describes_read_media_as_primary_multimodal_tool() -> None:
    """README.md must describe read_media as the primary multimodal tool."""
    content = _README_PATH.read_text(encoding="utf-8")
    assert "read_media" in content
    assert "compatibility" in content
    # Old image-only wording must be gone or augmented
    assert "read_image" in content  # kept as compatibility alias reference
    # Old reject-all wording must be gone
    assert "Upstream multimodal rejection" not in content
    assert "rejects it with a clear error" not in content


def test_readme_describes_broad_modality_support() -> None:
    """README.md must describe broad modality support (not image-only)."""
    content = _README_PATH.read_text(encoding="utf-8")
    assert "resource_reference" in content or "resource-reference" in content
    # Must mention normalization of upstream content
    assert (
        "normalizes it to a" in content
        or "normalized to a" in content
        or "Upstream normalization" in content
    )


def test_contributing_describes_read_media_as_primary_multimodal_tool() -> None:
    """CONTRIBUTING.md must describe read_media as primary tool, not image-only."""
    content = _CONTRIBUTING_PATH.read_text(encoding="utf-8")
    assert "read_media" in content
    assert "compatibility" in content
    # Old upstream-rejection wording must be replaced with normalization
    assert "rejects it with a clear error" not in content
    assert "resource_reference" in content


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


def test_readme_provider_matrix_claude_typed_blocks() -> None:
    """README.md must document Claude PDF/document delivery as typed blocks."""
    content = _README_PATH.read_text(encoding="utf-8")
    # Must say Claude PDFs/documents are typed blocks
    assert "typed block" in content, (
        "README.md must describe typed block delivery for Claude PDF/document modalities"
    )
    # Must not claim Claude delivers PDFs/documents as resource references
    assert "PDFs and documents as resource references" not in content, (
        "README.md must not claim Claude delivers PDFs/documents as resource_reference; "
        "Claude uses typed blocks for PDF/document modalities"
    )


def test_readme_provider_matrix_gemini_typed_blocks() -> None:
    """README.md must document Gemini image/PDF/document/audio/video correctly."""
    content = _README_PATH.read_text(encoding="utf-8")
    # Must say Gemini uses typed blocks for PDFs/documents/audio/video
    assert "typed block" in content, (
        "README.md must describe typed block delivery for Gemini modalities"
    )
    # Must not claim Gemini delivers all media as resource references
    assert (
        "PDFs, documents, audio, and video are all delivered as resource references"
        not in content
    ), (
        "README.md must not claim Gemini delivers all non-image media as resource_reference; "
        "Gemini uses typed blocks for PDF/document/audio/video"
    )


def test_readme_provider_matrix_openai_explicitly_unsupported() -> None:
    """README.md must document OpenAI/Codex non-image modalities as explicitly unsupported."""
    content = _README_PATH.read_text(encoding="utf-8")
    # Must say PDFs/docs/audio/video are unsupported for OpenAI
    assert (
        "explicitly unsupported" in content
        or "unsupported via the chat completion API" in content
    ), (
        "README.md must describe PDF/document/audio/video as explicitly unsupported"
        " for OpenAI/Codex"
    )
    # Must not say OpenAI non-image media falls back to resource_reference
    assert "other models fall back to resource reference" not in content, (
        "README.md must not claim OpenAI non-image media falls back to resource_reference; "
        "those modalities are explicitly unsupported"
    )


def test_readme_provider_matrix_unknown_providers_replayable() -> None:
    """README.md must document unknown providers using replayable resource references."""
    content = _README_PATH.read_text(encoding="utf-8")
    assert "replayable resource references" in content or "resource_reference_replay" in content, (
        "README.md must describe unknown providers as using replayable resource references"
    )


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


def test_readme_describes_screenshot_and_browser_visual_workflows() -> None:
    """README.md must describe screenshot and browser-captured visual QA workflows."""
    content = _README_PATH.read_text(encoding="utf-8")
    assert "screenshot" in content.lower(), (
        "README.md must describe screenshot/browser-captured visual QA workflows"
    )
    assert "browser" in content.lower(), (
        "README.md must mention browser-captured visuals as a multimodal workflow"
    )


def test_readme_describes_replayable_resource_handles_via_resources_read() -> None:
    """README.md must describe replayable ralph://media/... handles via resources/read."""
    content = _README_PATH.read_text(encoding="utf-8")
    assert "ralph://media/" in content, (
        "README.md must describe replayable ralph://media/... handles"
    )
    assert "resources/read" in content, (
        "README.md must describe how artifacts are retrievable via resources/read"
    )


def test_readme_describes_mixed_modality_workflows() -> None:
    """README.md must describe mixed-modality execution combining multiple modality types."""
    content = _README_PATH.read_text(encoding="utf-8")
    content_lower = content.lower()
    assert "mixed" in content_lower or "mixed-modality" in content_lower, (
        "README.md must describe mixed-modality workflows combining multiple modalities"
    )


def test_readme_text_only_safety_is_explicit() -> None:
    """README.md must explicitly state that text-only workflows remain safe and unchanged."""
    content = _README_PATH.read_text(encoding="utf-8")
    assert "text-only" in content, (
        "README.md must explicitly describe text-only safety/compatibility"
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
