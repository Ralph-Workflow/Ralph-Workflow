"""Project policy readiness contract markers.

This module is the single source of truth for the deterministic, machine-checkable
schema used by Ralph Workflow's project-policy-readiness preflight. Every other
module in ``ralph.project_policy`` imports these constants; validators, the
shared evidence inventory, the AGENTS.md bootstrap, the cached READY signature,
and the bundled starter policies all reference these tokens instead of repeating
string literals.

Design notes:

* All values use ``Final`` typing with explicit ``str`` annotations so a typo
  at any consumer site fails mypy rather than silently passing a different
  string to the validator.
* Comments name the rationale so a future maintainer can confirm the marker is
  still load-bearing before changing it. Changing ANY marker is a hard break of
  every existing cached READY and every existing canonical policy file; the
  change must be coordinated with a schema bump and a migration of starter
  content.
"""

from __future__ import annotations

from typing import Final

# Schema versioning
#
#   ... (21 more lines)

#   3. Portfolio schema bumped v1 -> v2: every control adds ``detects`` and
#      ``retirement_trigger``; the claim-scope rule applies to the
#   ... (10 more lines)
SCHEMA_VERSION: Final[str] = "v5"
POLICY_SCHEMA_MARKER: Final[str] = "<!-- ralph-policy-schema: v5 -->"
POLICY_CONTRACT_VERSION: Final[str] = "2026-10-12.1"

# Opt-out: byte-exact. The opt-out only fires on this exact substring;
# near-miss prose, additional words, or any whitespace/case change
# is treated as a non-match so accidental prose cannot disable the capability.
OPT_OUT_MARKER: Final[str] = "<!-- ralph-workflow-policy: skip -->"

# Significance heuristic for a pre-existing, marker-free AGENTS.md. The run
# preflight offers the interactive "skip inline policy" choice only when the
#   ... (3 more lines)
SIGNIFICANT_HEADING_PREFIX: Final[str] = "#"
SIGNIFICANT_NONEMPTY_LINE_THRESHOLD: Final[int] = 10

# Managed instruction block in AGENTS.md, bracketed by these two markers
# so repeated preflights can detect a pre-existing block without
# re-appending it (idempotent bootstrap).
AGENTS_BLOCK_BEGIN: Final[str] = "<!-- ralph-workflow-policy:begin v1 -->"
AGENTS_BLOCK_END: Final[str] = "<!-- ralph-workflow-policy:end -->"

# Canonical policy directory (relative to the workspace root).
CANONICAL_DIR: Final[str] = "docs/ralph-workflow-policy/"
APPLICABILITY_OVERRIDES_PATH: Final[str] = "docs/ralph-workflow-policy/applicability.toml"
PORTFOLIO_PATH: Final[str] = "docs/ralph-workflow-policy/policy-portfolio.toml"
PORTFOLIO_SCHEMA_VERSION: Final[str] = "v2"

# Required agent instruction files in the project.
AGENTS_MD: Final[str] = "AGENTS.md"
CLAUDE_MD: Final[str] = "CLAUDE.md"

# Core policy files (the twelve always-required for any software
# project). ``gate-script-policy.md`` ships its requirement sections
#   ... (9 more lines)
CORE_POLICY_FILES: Final[tuple[str, ...]] = (
    "execution-kernel.md",
    "testing-policy.md",
    "typechecking-policy.md",
    "linting-policy.md",
    "dependency-policy.md",
    "verification-policy.md",
    "gate-script-policy.md",
    "agent-policy.md",
    "clean-code-policy.md",
    "documentation-policy.md",
    "security-policy.md",
    "architecture-policy.md",
)


# Version-specific core-file manifest. v4 requires the original
# eleven; v5 adds ``execution-kernel.md`` to reach twelve. Files
#   ... (3 more lines)
def core_files_for_version(v: str) -> tuple[str, ...]:
    """Return the core-file manifest for schema version ``v``.

    Args:
        v: The schema version string (``"v4"``, ``"v5"``; unknown values
            fall back to the current ``CORE_POLICY_FILES`` tuple).

    Returns:
        The required core-file tuple for that version. Used by the
        validators and the upgrade seed path to install the right pack
        and reject frozen packs missing a preserved core file.
    """
    if v == "v4":
        return _V4_CORE_FILES
    return CORE_POLICY_FILES


# v4 manifest: the original eleven required core files. Used by the
# frozen-pack validator so a frozen v4 pack missing a preserved core
# file (e.g. security-policy.md) stays NOT READY.
_V4_CORE_FILES: Final[tuple[str, ...]] = (
    "testing-policy.md",
    "typechecking-policy.md",
    "linting-policy.md",
    "dependency-policy.md",
    "verification-policy.md",
    "gate-script-policy.md",
    "agent-policy.md",
    "clean-code-policy.md",
    "documentation-policy.md",
    "security-policy.md",
    "architecture-policy.md",
)

# Conditional policy files keyed by their domain. Each conditional
# domain is required only when its deterministic signal set matches the
# project (see ``CONDITIONAL_SIGNAL_PATHS`` for the per-domain signal list).
CONDITIONAL_POLICY_FILES: Final[dict[str, str]] = {
    "design-system": "design-system-policy.md",
    "ux": "ux-policy.md",
    "performance": "performance-policy.md",
    "memory-usage": "memory-usage-policy.md",
    "accessibility": "accessibility-policy.md",
    "api-compatibility": "api-compatibility-policy.md",
    "data-storage": "data-storage-policy.md",
    "reliability-observability": "reliability-observability-policy.md",
    "privacy": "privacy-policy.md",
    "release-deployment": "release-deployment-policy.md",
}

CONDITIONAL_SIGNAL_PATHS: Final[dict[str, tuple[str, ...]]] = {
    "api-compatibility": (
        "openapi.json",
        "openapi.yaml",
        "openapi.yml",
        "docs/api.md",
        "docs/public-api.md",
    ),
    "data-storage": (
        "migrations/",
        "db/migrate/",
        "db/migrations/",
        "alembic/",
        "prisma/schema.prisma",
    ),
    "reliability-observability": (
        "k8s/",
        "deploy/",
        "docs/operations.md",
    ),
    "privacy": (
        "PRIVACY.md",
        "docs/privacy.md",
        "docs/data-classification.md",
    ),
    "release-deployment": (
        ".github/workflows/release.yml",
        ".github/workflows/release.yaml",
        "RELEASING.md",
        "docs/releasing.md",
    ),
}

# Required headings per policy file. Every starter AND every
# project-customized policy must include the listed headings
#   ... (6 more lines)
REQUIRED_HEADINGS: Final[dict[str, tuple[str, ...]]] = {
    # The execution kernel (v5+) carries the five generic rules once
    # (truthful evidence, fail-closed verification, bounded execution,
    # no weakening of checks, explicit unverified-claim reporting), the
    # precedence over domain policies, the selective-applicability rule,
    # the claim-scope rule, and the guard inventory for kernel rules.
    "execution-kernel.md": (
        "Purpose and scope",
        "Kernel rules",
        "Precedence and selective applicability",
        "Claim-scope rule",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    # The testing policy additionally requires "Suite admission": the section
    # that decides which acceptance criteria become automated tests at all.
    # Without it the policy is a one-way ratchet — every rule obliges MORE
    # coverage and none routes a perceptual, third-party, or unrepeatable
    # criterion to the lane that actually owns it.
    "testing-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Suite admission",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "typechecking-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "linting-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Dead code — zero tolerance",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "dependency-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    # The verification policy additionally requires "Gate lanes": the declared
    # home for a check that cannot meet the default gate's constraints (live
    # third-party service, device matrix, long soak). Without a lane that
    # demands an owner and a trigger, "wire it in or delete it" leaves such a
    # check nowhere legitimate to live, so it lands in the default gate.
    "verification-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Gate lanes",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Bypass detection",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    # The gate-script policy additionally requires "Security" and
    # "Cross-platform" headings: a gate script is an execution surface
    # (secrets, remote code, temp files) and a portability surface
    # (a bash-only gate on a Windows-supporting project is a broken
    # gate). Both sections must survive every amendment.
    "gate-script-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Failure output",
        "Security",
        "Cross-platform",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "agent-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "clean-code-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "documentation-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "security-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Threat surfaces",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "architecture-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "design-system-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "ux-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "performance-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "memory-usage-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "accessibility-policy.md": (
        "Purpose and scope",
        "Applicability",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "api-compatibility-policy.md": (
        "Purpose and scope",
        "Applicability",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "data-storage-policy.md": (
        "Purpose and scope",
        "Applicability",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "reliability-observability-policy.md": (
        "Purpose and scope",
        "Applicability",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "privacy-policy.md": (
        "Purpose and scope",
        "Applicability",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "release-deployment-policy.md": (
        "Purpose and scope",
        "Applicability",
        "Default requirements",
        "Guard inventory",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
}

# Per-policy identifier prefix. Each canonical file must contain a
# `<!-- ralph-policy-id: <filename> -->` line (exact-substring match).
#   ... (4 more lines)
POLICY_ID_PREFIX: Final[str] = "<!-- ralph-policy-id:"

# Machine-checkable field markers. The validator parses these as line-prefixed
# facts so policy content can be checked deterministically (no prose NLP).
#   ... (28 more lines)
FACT_MARKER: Final[str] = "RALPH-FACT:"
COMMAND_MARKER: Final[str] = "RALPH-COMMAND:"
INAPPLICABLE_MARKER: Final[str] = "RALPH-INAPPLICABLE:"
REVIEW_MARKER: Final[str] = "RALPH-REVIEW:"
LANG_MARKER: Final[str] = "RALPH-LANG:"
# Deferral marker. ``PENDING_MARKER`` (with colon) introduces a gate
# deferral; ``PENDING_SENTINEL`` (no colon) leads a deferred RALPH-FACT
#   ... (4 more lines)
PENDING_MARKER: Final[str] = "RALPH-PENDING:"
PENDING_SENTINEL: Final[str] = "RALPH-PENDING"

# Tokens that mark a policy line or fact value as unresolved. The validator
# rejects any policy containing any of these substrings. They are also embedded
#   ... (3 more lines)
PLACEHOLDER_TOKENS: Final[tuple[str, ...]] = (
    "TODO",
    "TBD",
    "FIXME",
    "<REPLACE>",
    "{{",
    "REPLACE-ME",
    "PROJECT-FACT-UNRESOLVED",
    # The REPLACE-ME guidance comments show the RALPH-PENDING deferral form
    # "(assumed <date>); review trigger: <trigger>"; agents that copy the
    # example literally must stay blocked until they substitute real values.
    "<date>",
    "<trigger>",
)

# Every starter opens with a RALPH-STARTER-TEMPLATE banner comment that marks
# the file as an unfilled template. The token is deliberately NOT in
#   ... (6 more lines)
STARTER_TEMPLATE_TOKEN: Final[str] = "RALPH-STARTER-TEMPLATE"

# Approved gate tool executables. The validator checks every
# RALPH-COMMAND value's first whitespace-separated token against
#   ... (11 more lines)
APPROVED_GATE_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "make",
        "pytest",
        "mypy",
        "ruff",
        "cargo",
        "go",
        "npm",
        "pnpm",
        "yarn",
        "npx",
        "uv",
        "python",
        "python3",
        "node",
        "tsc",
        "ts-node",
        "eslint",
        "prettier",
        "black",
        "isort",
        "flake8",
        "pylint",
        "bandit",
        "safety",
        # Security scanners the security-policy and linting-policy starters
        # recommend: the validator must accept the starters' own advice.
        "semgrep",
        "gitleaks",
        "detect-secrets",
        "gosec",
        "golangci-lint",
        "shellcheck",
        "hadolint",
        "docker",
        "docker-compose",
        "kubectl",
        "terraform",
        "ansible",
        "bundle",
        "bun",
        "composer",
        "dotnet",
        "gradle",
        "mvn",
        "php",
        "swift",
        "mix",
    }
)

# Per-citation required fields. The validator parses the Research
# basis section into per-source citation blocks and checks each block
#   ... (2 more lines)
CITATION_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "publisher",
    "title",
    "http",
    "review date",
)

# Conditional-domain signal sets. The evidence module uses these to
# decide whether a domain-specific policy file is required for a
#   ... (3 more lines)
UI_FRAMEWORK_SIGNALS: Final[frozenset[str]] = frozenset(
    {
        "React",
        "Vue",
        "Angular",
        "Svelte",
        "Next.js",
        "Nuxt",
        "Gatsby",
        "Tauri",
        "Leptos",
        "Yew",
    }
)

# CSS-family languages: a project whose secondary languages include
# any of these triggers a design-system policy.
CSS_LANGUAGE_SIGNALS: Final[frozenset[str]] = frozenset({"CSS", "SCSS", "Sass", "Less"})

# Stricter signal set for UX: only when the project uses a SPA-style app
# framework does a UX policy become required. Design-system can be required
# without UX; UX always implies design-system.
UX_APP_FRAMEWORKS: Final[frozenset[str]] = frozenset(
    {"React", "Vue", "Angular", "Svelte", "Next.js", "Nuxt", "Gatsby"}
)

# UX router dependency substrings: scanned inside package.json content. A
# project using react-router, vue-router, etc. also requires a UX policy.
UX_ROUTER_DEP_SIGNALS: Final[tuple[str, ...]] = (
    "react-router",
    "@angular/router",
    "vue-router",
    "@remix-run",
    "react-navigation",
    "@tanstack/router",
)

# Performance signal file paths (relative to workspace root). Existence
# of any of these triggers a performance policy.
PERF_SIGNAL_PATHS: Final[tuple[str, ...]] = (
    "performance-budget.json",
    "lighthouse-budget.json",
    ".lighthouserc.js",
    ".lighthouserc.json",
    "benches/",
    "criterion.toml",
)

# Performance dependency substrings: scanned inside package.json/pyproject.toml/
# Cargo.toml content. A project that depends on a known benchmarking tool
# requires a performance policy.
PERF_DEP_SIGNALS: Final[tuple[str, ...]] = (
    "k6",
    "artillery",
    "lighthouse",
)

# Memory-usage signal file paths.
MEMORY_SIGNAL_PATHS: Final[tuple[str, ...]] = (
    "docs/memory-budget.md",
    "memory-budget.json",
    ".soak-test.json",
    "soak-tests/",
)

MEMORY_DEP_SIGNALS: Final[tuple[str, ...]] = (
    "memlab",
    "clinic",
    "heaptrack",
)

# Migration: a finite candidate inventory the validator inspects for
# already-existing policy-like content. Every entry is a relative path
# scanned on the workspace seam (NOT raw Path I/O).
MIGRATION_CANDIDATE_PATHS: Final[tuple[str, ...]] = (
    # AGENTS.md: user-authored policy-like sections must be INTEGRATED
    # into the canonical dir (single source of truth). The managed block
    # contains no headings, so bootstrap output alone never makes it a
    # candidate.
    "AGENTS.md",
    "CONTRIBUTING.md",
    "CONTRIBUTING.rst",
    "TESTING.md",
    "DEVELOPMENT.md",
    # SECURITY.md is the standard GitHub security-policy location; its
    # content belongs in the canonical security-policy.md so the project
    # never keeps a parallel security rulebook outside the canonical dir.
    "SECURITY.md",
    ".github/SECURITY.md",
    "docs/testing.md",
    "docs/development.md",
    "docs/contributing.md",
    "docs/security.md",
    ".github/CONTRIBUTING.md",
)

# Migration docs scan root: a bounded one-level scan of *.md under this
# directory catches project-local policy-like docs that are not in the
# explicit candidate list. Files already under CANONICAL_DIR are skipped.
MIGRATION_DOCS_GLOB_DIR: Final[str] = "docs"

# Lowercased heading phrases that mark a doc as a migration candidate.
# The scanner normalizes H1/H2 lines (strip leading hashes, trim
#   ... (2 more lines)
MIGRATION_HEADING_RECOGNIZERS: Final[tuple[str, ...]] = (
    "test policy",
    "testing policy",
    "linting policy",
    "type checking policy",
    "dependency policy",
    "verification policy",
    "agent policy",
    "clean code policy",
    "documentation policy",
    "security policy",
    "threat model",
)

# Exact marker a migrated file embeds to declare itself resolved. The token
# ``{target}`` is replaced with the destination canonical file name; an
#   ... (2 more lines)
MIGRATED_MARKER_TEMPLATE: Final[str] = (
    "<!-- ralph-workflow-policy:migrated -> docs/ralph-workflow-policy/{target} -->")

# The target-independent prefix of the migrated marker. `--redo-policy` strips
# every line carrying it: the marker points at a canonical file the reset has
#   ... (2 more lines)
MIGRATED_MARKER_PREFIX: Final[str] = "<!-- ralph-workflow-policy:migrated ->"

# Cache file path for the change-aware READY cache. Stored under
# .agent/tmp so it travels with the workspace but stays out of source
# control.
CACHE_REL_PATH: Final[str] = ".agent/tmp/policy_readiness_cache.json"

# Stable finding-id prefixes. The validator emits exactly one
# PolicyFinding per (requirement, target); the requirement_id is one
#   ... (3 more lines)
ID_CORE_MISSING: Final[str] = "RWP-CORE"
ID_CMD_UNUSABLE: Final[str] = "RWP-CMD"
ID_LANG_COVERAGE: Final[str] = "RWP-LANG"
ID_CITATION_MISSING: Final[str] = "RWP-CITATION"
ID_HEADING_MISSING: Final[str] = "RWP-HEADING"
ID_PLACEHOLDER: Final[str] = "RWP-PLACEHOLDER"
ID_MARKER_MISSING: Final[str] = "RWP-MARKER"
ID_PENDING: Final[str] = "RWP-PENDING"
ID_AGENTS_MD_MISSING: Final[str] = "RWP-AGENTS-MD"
ID_CLAUDE_MD_MISSING: Final[str] = "RWP-CLAUDE-MD"
ID_DOMAIN: Final[str] = "RWP-DOMAIN"
ID_MIGRATE: Final[str] = "RWP-MIGRATE-UNRECONCILED"
ID_PORTFOLIO: Final[str] = "RWP-PORTFOLIO"
ID_INVENTORY: Final[str] = "RWP-INVENTORY"

# Stable v5 finding ids for the guard-inventory validator. The plan
# freezes these identifiers so black-box tests can pin the v5 contract
#   ... (4 more lines)
CLAIM_SCOPE_MISSING_FINDING_ID: Final[str] = "RWP-INVENTORY:CLAIM-SCOPE-MISSING"
CLAIM_SCOPE_MULTI_SENTENCE_FINDING_ID: Final[str] = "RWP-INVENTORY:CLAIM-SCOPE-MULTI-SENTENCE"
RATCHET_UNCOVERED_FINDING_ID: Final[str] = "RWP-INVENTORY:RATCHET-UNCOVERED"
CONTROL_ORPHAN_FINDING_ID: Final[str] = "RWP-INVENTORY:CONTROL-ORPHAN"

# Guard inventory grammar. Every applicable starter (core and conditional)
# MUST carry a ``## Guard inventory`` section whose body is a markdown
#   ... (12 more lines)
GUARD_INVENTORY_HEADING: Final[str] = "## Guard inventory"
GUARD_INVENTORY_HEADER: Final[tuple[str, ...]] = (
    "Guard",
    "Owner",
    "Protected outcome",
    "Detected condition",
    "Claim scope",
    "Retirement trigger",
)

#: Maximum number of sentences permitted in a guard row's ``Claim scope``
#: cell. The plan freezes this at one: the cell names the narrow claim
#: the detector actually supports, never a multi-clause narrative.
#: Validated as a regex sentence-boundary split (period, exclamation,
#: question mark, or line break). The constant is exported so the
#: validator and the project-policy test fixtures share one source of
#: truth.
_GUARD_INVENTORY_CLAIM_SCOPE_MAX_SENTENCES: Final[int] = 1

# Body ratchet declaration (B9): a sentence in a starter body that
# names a project constraint whose value MUST NOT grow / MUST NOT
#   ... (16 more lines)
RATCHET_DECLARATION_PHRASES: Final[tuple[str, ...]] = (
    "ratcheted",
    "ratchet",
    "MUST NOT grow",
    "MUST NOT increase",
)

# Grammar (frozen) for the inline control token. A single-line HTML
# comment placed at the end of the declaring sentence; the captured id
#   ... (2 more lines)
CONTROL_TOKEN_RE: Final[str] = r"<!--\s*ralph-control:\s*([a-z0-9-]+)\s*-->"
CONTROL_ID_RE: Final[str] = r"`([a-z0-9-]+)`"


# Claim-scope rule (deterministic substring predicate). A guard row is
# REJECTED when its ``Protected outcome`` (or new ``Claim scope``)
#   ... (6 more lines)
CLAIM_SCOPE_PHRASES: Final[tuple[str, ...]] = (
    "enforced",
    "guaranteed",
    "ensures",
    "always",
    "prevents all",
    "all ",
)


__all__ = [
    "AGENTS_BLOCK_BEGIN",
    "AGENTS_BLOCK_END",
    "AGENTS_MD",
    "APPLICABILITY_OVERRIDES_PATH",
    "APPROVED_GATE_TOOLS",
    "CACHE_REL_PATH",
    "CANONICAL_DIR",
    "CITATION_REQUIRED_FIELDS",
    "CLAIM_SCOPE_MISSING_FINDING_ID",
    "CLAIM_SCOPE_MULTI_SENTENCE_FINDING_ID",
    "CLAIM_SCOPE_PHRASES",
    "CLAUDE_MD",
    "COMMAND_MARKER",
    "CONDITIONAL_POLICY_FILES",
    "CONDITIONAL_SIGNAL_PATHS",
    "CONTROL_ID_RE",
    "CONTROL_ORPHAN_FINDING_ID",
    "CONTROL_TOKEN_RE",
    "CORE_POLICY_FILES",
    "CSS_LANGUAGE_SIGNALS",
    "FACT_MARKER",
    "GUARD_INVENTORY_HEADER",
    "GUARD_INVENTORY_HEADING",
    "ID_AGENTS_MD_MISSING",
    "ID_CITATION_MISSING",
    "ID_CLAUDE_MD_MISSING",
    "ID_CMD_UNUSABLE",
    "ID_CORE_MISSING",
    "ID_DOMAIN",
    "ID_HEADING_MISSING",
    "ID_INVENTORY",
    "ID_LANG_COVERAGE",
    "ID_MARKER_MISSING",
    "ID_MIGRATE",
    "ID_PENDING",
    "ID_PLACEHOLDER",
    "ID_PORTFOLIO",
    "INAPPLICABLE_MARKER",
    "LANG_MARKER",
    "MEMORY_DEP_SIGNALS",
    "MEMORY_SIGNAL_PATHS",
    "MIGRATED_MARKER_PREFIX",
    "MIGRATED_MARKER_TEMPLATE",
    "MIGRATION_CANDIDATE_PATHS",
    "MIGRATION_DOCS_GLOB_DIR",
    "MIGRATION_HEADING_RECOGNIZERS",
    "OPT_OUT_MARKER",
    "PENDING_MARKER",
    "PENDING_SENTINEL",
    "PERF_DEP_SIGNALS",
    "PERF_SIGNAL_PATHS",
    "PLACEHOLDER_TOKENS",
    "POLICY_CONTRACT_VERSION",
    "POLICY_ID_PREFIX",
    "POLICY_SCHEMA_MARKER",
    "PORTFOLIO_PATH",
    "PORTFOLIO_SCHEMA_VERSION",
    "RATCHET_DECLARATION_PHRASES",
    "RATCHET_UNCOVERED_FINDING_ID",
    "REQUIRED_HEADINGS",
    "REVIEW_MARKER",
    "SCHEMA_VERSION",
    "SIGNIFICANT_HEADING_PREFIX",
    "SIGNIFICANT_NONEMPTY_LINE_THRESHOLD",
    "STARTER_TEMPLATE_TOKEN",
    "UI_FRAMEWORK_SIGNALS",
    "UX_APP_FRAMEWORKS",
    "UX_ROUTER_DEP_SIGNALS",
    "core_files_for_version",
]
