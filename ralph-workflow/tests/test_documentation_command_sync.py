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
