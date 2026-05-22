"""Canonical baseline capability definition with tier splits."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BaselineCapability:
    """A Ralph Workflow baseline capability with its tier classification."""

    name: str
    description: str
    tier: str
    # tier values: 'static_builtin' | 'mandatory' | 'conditional' | 'non_default'


# Always-on built-in capabilities: no external dependencies, cannot degrade.
STATIC_BUILTIN_CAPABILITIES: tuple[BaselineCapability, ...] = (
    BaselineCapability(
        "workspace_ops",
        "Workspace and local file operations",
        "static_builtin",
    ),
    BaselineCapability(
        "git_read_ops",
        "Git read, status, diff, and log operations",
        "static_builtin",
    ),
    BaselineCapability(
        "artifact_ops",
        "Artifact submission support",
        "static_builtin",
    ),
    BaselineCapability(
        "plan_read",
        "Plan-reading support",
        "static_builtin",
    ),
    BaselineCapability(
        "media_read",
        "Image and media file read support",
        "static_builtin",
    ),
)

# Dependency-backed helpers that require health tracking.
MANDATORY_DEFAULTS: tuple[BaselineCapability, ...] = (
    BaselineCapability(
        "web_search",
        "DuckDuckGo-backed web search, no API key required",
        "mandatory",
    ),
    BaselineCapability(
        "visit_url",
        "Single-page HTTP/HTTPS retrieval with readable text extraction",
        "mandatory",
    ),
    BaselineCapability(
        "skills_bundle",
        "Ralph Workflow first-party skill bundle (17 skills: Superpowers + quality subset)",
        "mandatory",
    ),
)

# Supported when configured and reachable.
CONDITIONAL_DEFAULTS: tuple[BaselineCapability, ...] = (
    BaselineCapability(
        "docs_mcp",
        (
            "arabold/docs-mcp-server documentation lookup on localhost:6280 "
            "when configured and reachable"
        ),
        "conditional",
    ),
)

# Not part of the first-run baseline.
NON_DEFAULTS: tuple[BaselineCapability, ...] = (
    BaselineCapability(
        "github",
        "github/github-mcp-server - requires credentials",
        "non_default",
    ),
    BaselineCapability(
        "playwright",
        "Playwright-style browser automation MCP servers",
        "non_default",
    ),
    BaselineCapability(
        "crawl4ai",
        "Crawl4AI MCP server for advanced multi-page crawling",
        "non_default",
    ),
    BaselineCapability(
        "exa_search",
        "Exa and other credentialed search providers",
        "non_default",
    ),
)


__all__ = [
    "CONDITIONAL_DEFAULTS",
    "MANDATORY_DEFAULTS",
    "NON_DEFAULTS",
    "STATIC_BUILTIN_CAPABILITIES",
    "BaselineCapability",
]
