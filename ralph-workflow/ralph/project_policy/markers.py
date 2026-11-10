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

from dataclasses import dataclass
from typing import Final

from ralph.project_policy._required_headings import REQUIRED_HEADINGS

# Schema versioning
#
# v3 (2026-08-08) changed the policy contract in TWO ways, and both bind
# existing projects - ``_check_required_fact_keys`` derives its required set
# live from the bundled starter, so a starter's fact lines are contract, not
# content.
#
# 1. Two new REQUIRED headings carrying rules no other section can hold:
#    "Suite admission" in the testing policy (which criteria become automated
#    tests at all, and where the rest are verified instead) and "Gate lanes"
#    in the verification policy (the declared home for a check that cannot
#    meet the default gate's constraints).
# 2. A revised RALPH-FACT key set in those two starters: testing gained
#    parallel_execution_mechanism, slow_test_report_command,
#    suite_test_count_command, assertion_quality_check,
#    supported_platform_matrix, external_dependency_test_approach,
#    clean_clone_setup_command, quarantine_mechanism_expiry_and_max_size and
#    suite_review_cadence_and_owner; verification gained fast_path_command,
#    fast_path_selection_mechanism, gate_parallelism_mechanism,
#    gate_cache_mechanism_and_key and gate_duration_report_location.
#    The project's named profiles are recorded ONCE, in verification's
#    required_verification_profiles, so the two policies cannot drift.
#
# Consequence for an existing v2 project: it validates NOT READY until a
# remediation pass resolves the new keys and adds the two headings, or the
# pack is frozen at v2. That is the point of the marker bump - it forces the
# upgrade-or-freeze consent gate rather than changing files under the user.
#
# v4 (backfill) - minor hardening only (the 60 s test-budget fence, the
# fabrication guard).  No content-contract change for v3 projects; a
# frozen-at-v3 pack keeps validating under v4 because every required
# field and heading stayed the same.  The bump exists to carry the
# test-budget invariant into the marker record so the audit can prove
# the fence is policy, not convention.
#
# v5 (current) reframes the shipped policy as a small outcome-oriented
# execution kernel plus evidence-backed, selectively applicable controls.
# It is a major content change:
#
#   1. New mandatory core file ``execution-kernel.md`` carrying the five
#      kernel rules (truthful evidence, fail-closed verification, bounded
#      execution, no weakening of checks, explicit unverified-claim
#      reporting), the precedence over domain policies, the
#      selective-applicability rule, and the claim-scope rule.
#   2. Every applicable starter (core and conditional) MUST carry a
#      ``## Guard inventory`` section in the pinned markdown-table grammar
#      (header ``| Guard | Owner | Protected outcome | Detected condition |
#      Claim scope | Retirement trigger |``; one row per guard; six non-empty
#      cells).  The ``Claim scope`` column is a v5 requirement: every guard
#      row MUST name the narrow claim its detector actually supports, so a
#      row's ``Protected outcome`` cannot drift into a broad world-claim.
#      A starter without the table - or with a malformed table - fails
#      the validator with a stable RWP-INVENTORY:* finding.
#   3. Portfolio schema bumped v1 -> v2: every control adds the required
#      fields ``detects`` (the observable condition the detector actually
#      reports) and ``retirement_trigger`` (when to retire the control).
#      A control whose ``protected_outcome`` carries a broad-guarantee
#      phrase that does NOT also appear in ``detects`` is rejected
#      (claim-scope rule). Frozen v1 packs keep v1 validation so the
#      user keeps the work; the upgrade-or-freeze gate forces a choice.
#   4. Readiness reporting splits into three dimensions
#      (structure_complete, implementation_verified,
#      outcome_evidence_reviewed) on all four surfaces (CLI, status
#      bar, AGENTS.md block, remediation prompt). Structural READY
#      remains the cache gate; semantic correctness is now
#      reported separately.
#   5. Stale starter generations retired; the gate-script starter
#      switches from prose-freeze to meaning-freeze (C3).
#   6. Body ratchet declarations in a starter MUST be linked to a
#      Guard inventory row by an inline ``<!-- ralph-control: <id> -->``
#      token at the end of the declaring sentence, where ``<id>`` is the
#      backticked control id at the start of a Guard cell. Uncovered
#      declarations and orphan row ids are rejected with stable
#      ``RWP-INVENTORY:RATCHET-UNCOVERED`` /
#      ``RWP-INVENTORY:CONTROL-ORPHAN`` findings (B9).
#
# Consequence for an existing v4 project: it validates NOT READY until
# a remediation pass adopts v5, or the pack is frozen at v4 through the
# existing upgrade-or-freeze consent gate (``_schema_upgrade.py``).
# Frozen v4 packs are validated against the v4 required-file manifest
# (the original eleven core files) - only files introduced AFTER v4
# are exempt, never the existing core files, so a frozen v4 pack
# missing ``security-policy.md`` stays NOT READY.
#
# v6 (current) adds an explicit Enforcement column to the Guard
# inventory table (closed value set: deterministic: <id> or
# advisory: <reason>; review: <trigger>), with exact-string resolution
# against declared RALPH-COMMAND values and verify.py step labels.
# Starters ship with deterministic: PROJECT-FACT-UNRESOLVED.
# Portfolio schema bumped v2 -> v3 with required per-control enforcement.
SCHEMA_VERSION: Final[str] = "v6"
POLICY_SCHEMA_MARKER: Final[str] = "<!-- ralph-policy-schema: v6 -->"
POLICY_CONTRACT_VERSION: Final[str] = "2026-11-09.1"

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
# re-appending it (idempotent bootstrap). The managed block text is unchanged
# in schema v6, so the marker version remains v1.
AGENTS_BLOCK_BEGIN: Final[str] = "<!-- ralph-workflow-policy:begin v1 -->"
AGENTS_BLOCK_END: Final[str] = "<!-- ralph-workflow-policy:end -->"

# Canonical policy directory (relative to the workspace root).
CANONICAL_DIR: Final[str] = "docs/ralph-workflow-policy/"
APPLICABILITY_OVERRIDES_PATH: Final[str] = "docs/ralph-workflow-policy/applicability.toml"
PORTFOLIO_PATH: Final[str] = "docs/ralph-workflow-policy/policy-portfolio.toml"
PORTFOLIO_SCHEMA_VERSION: Final[str] = "v3"

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
    "<!-- ralph-workflow-policy:migrated -> docs/ralph-workflow-policy/{target} -->"
)

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
CLAIM_SCOPE_MISSING_FINDING_ID: Final[str] = "RWP-INVENTORY:CLAIM-SCOPE-MISSING"
CLAIM_SCOPE_MULTI_SENTENCE_FINDING_ID: Final[str] = "RWP-INVENTORY:CLAIM-SCOPE-MULTI-SENTENCE"
RATCHET_UNCOVERED_FINDING_ID: Final[str] = "RWP-INVENTORY:RATCHET-UNCOVERED"
CONTROL_ORPHAN_FINDING_ID: Final[str] = "RWP-INVENTORY:CONTROL-ORPHAN"

# Stable v6 finding ids for the guard-inventory enforcement validator.
ENFORCEMENT_MISSING_FINDING_ID: Final[str] = "RWP-INVENTORY:ENFORCEMENT-MISSING"
ENFORCEMENT_UNKNOWN_ID_FINDING_ID: Final[str] = "RWP-INVENTORY:ENFORCEMENT-UNKNOWN-ID"
ENFORCEMENT_ADVISORY_NO_TRIGGER_FINDING_ID: Final[str] = (
    "RWP-INVENTORY:ENFORCEMENT-ADVISORY-NO-TRIGGER"
)

# Stable v6 finding ids for instruction-surface checks.
ID_INSTRUCTION: Final[str] = "RWP-INSTRUCTION"
MANAGED_BLOCK_LINE_BUDGET_FINDING_ID: Final[str] = "RWP-AGENTS-MD:MANAGED-BLOCK-LINE-BUDGET"
PROJECT_AGENTS_MD_LINE_BUDGET_FINDING_ID: Final[str] = "RWP-AGENTS-MD:PROJECT-LINE-BUDGET"
AGENTS_MD_CITED_PATH_FINDING_ID: Final[str] = "RWP-AGENTS-MD:CITED-PATH-MISSING"
HIDDEN_UNICODE_FINDING_ID: Final[str] = "RWP-INSTRUCTION:HIDDEN-UNICODE"
RESEARCH_FRESHNESS_FINDING_ID: Final[str] = "RWP-CITATION:RESEARCH-FRESHNESS"

# Decided-value ceilings for instruction surfaces (judgment-based, tag none, advisory).
# Managed block line ceiling (carries non-inferable pointers and readiness state; ~33 lines today).
MANAGED_BLOCK_LINE_BUDGET: Final[int] = 40
# Project AGENTS.md line ceiling (Anthropic's 200-line ceiling to keep load-bearing rules salient).
PROJECT_AGENTS_MD_LINE_BUDGET: Final[int] = 200
# Research freshness threshold (instructions written for older models rot; warn at >365 days).
RESEARCH_FRESHNESS_MAX_DAYS: Final[int] = 365

# Guard inventory grammar. Every applicable starter (core and conditional)
# MUST carry a ``## Guard inventory`` section whose body is a markdown
# table with the exact header below (case-insensitive match). The body is
# a row-per-guard table with seven non-empty cells (six in v5). The validator
# parses the table via :func:`ralph.project_policy._guard_inventory._parse_guard_inventory`
# and emits a stable RWP-INVENTORY:* finding on a missing or malformed table.
GUARD_INVENTORY_HEADING: Final[str] = "## Guard inventory"
_V5_GUARD_INVENTORY_HEADER: Final[tuple[str, ...]] = (
    "Guard",
    "Owner",
    "Protected outcome",
    "Detected condition",
    "Claim scope",
    "Retirement trigger",
)
GUARD_INVENTORY_HEADER: Final[tuple[str, ...]] = (
    "Guard",
    "Owner",
    "Protected outcome",
    "Detected condition",
    "Claim scope",
    "Enforcement",
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


@dataclass(frozen=True, slots=True)
class PolicyVersionSpec:
    """Specification of a policy schema version.

    Captures the required core-file manifest, the expected guard-inventory
    header grammar (or ``None`` for versions prior to the table's
    introduction in v5), the matching portfolio schema version(s), and
    whether the version is permitted as a freeze target.
    """

    version: str
    core_manifest: tuple[str, ...]
    guard_inventory_header: tuple[str, ...] | None
    portfolio_schema_version: tuple[str, ...]
    allowed_frozen: bool = True

    @property
    def guard_inventory_columns(self) -> int:
        """Return the number of columns in the guard inventory table, or 0 if exempt."""
        return len(self.guard_inventory_header) if self.guard_inventory_header is not None else 0


#: Version-keyed specifications for supported active and frozen policy schemas.
VERSION_SPECS: Final[dict[str, PolicyVersionSpec]] = {
    "v4": PolicyVersionSpec(
        version="v4",
        core_manifest=_V4_CORE_FILES,
        guard_inventory_header=None,
        portfolio_schema_version=("v1", "v2"),
        allowed_frozen=True,
    ),
    "v5": PolicyVersionSpec(
        version="v5",
        core_manifest=CORE_POLICY_FILES,
        guard_inventory_header=_V5_GUARD_INVENTORY_HEADER,
        portfolio_schema_version=("v2",),
        allowed_frozen=True,
    ),
    "v6": PolicyVersionSpec(
        version="v6",
        core_manifest=CORE_POLICY_FILES,
        guard_inventory_header=GUARD_INVENTORY_HEADER,
        portfolio_schema_version=("v3",),
        allowed_frozen=True,
    ),
}


def core_files_for_version(v: str | None) -> tuple[str, ...]:
    """Return the core-file manifest for schema version ``v``.

    Args:
        v: The schema version string (``"v4"``, ``"v5"``; unknown values
            fall back to the current ``CORE_POLICY_FILES`` tuple).

    Returns:
        The required core-file tuple for that version. Used by the
        validators and the upgrade seed path to install the right pack
        and reject frozen packs missing a preserved core file.
    """
    if v is not None and v in VERSION_SPECS:
        return VERSION_SPECS[v].core_manifest
    return CORE_POLICY_FILES


def spec_for_version(v: str | None) -> PolicyVersionSpec:
    """Return the PolicyVersionSpec for schema version ``v``, defaulting to current."""
    if v is not None and v in VERSION_SPECS:
        return VERSION_SPECS[v]
    return VERSION_SPECS[SCHEMA_VERSION]


def is_allowed_frozen_version(v: str) -> bool:
    """Return True if ``v`` is a recognized schema version permitted to be frozen."""
    spec = VERSION_SPECS.get(v)
    return spec is not None and spec.allowed_frozen


def guard_inventory_header_for_version(v: str | None) -> tuple[str, ...] | None:
    """Return the guard inventory header for schema version ``v``, or None if exempt."""
    if v is not None and v in VERSION_SPECS:
        return VERSION_SPECS[v].guard_inventory_header
    return VERSION_SPECS[SCHEMA_VERSION].guard_inventory_header


def portfolio_schema_version_for_version(v: str | None) -> str:
    """Return expected portfolio schema version for policy version ``v``."""
    if v is not None and v in VERSION_SPECS:
        return VERSION_SPECS[v].portfolio_schema_version[0]
    return VERSION_SPECS[SCHEMA_VERSION].portfolio_schema_version[0]


def supported_portfolio_schema_versions() -> frozenset[str]:
    """Return all supported portfolio schema versions across recognized policy versions."""
    return frozenset(
        ver for spec in VERSION_SPECS.values() for ver in spec.portfolio_schema_version
    )


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
    "AGENTS_MD_CITED_PATH_FINDING_ID",
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
    "ENFORCEMENT_ADVISORY_NO_TRIGGER_FINDING_ID",
    "ENFORCEMENT_MISSING_FINDING_ID",
    "ENFORCEMENT_UNKNOWN_ID_FINDING_ID",
    "FACT_MARKER",
    "GUARD_INVENTORY_HEADER",
    "GUARD_INVENTORY_HEADING",
    "HIDDEN_UNICODE_FINDING_ID",
    "ID_AGENTS_MD_MISSING",
    "ID_CITATION_MISSING",
    "ID_CLAUDE_MD_MISSING",
    "ID_CMD_UNUSABLE",
    "ID_CORE_MISSING",
    "ID_DOMAIN",
    "ID_HEADING_MISSING",
    "ID_INSTRUCTION",
    "ID_INVENTORY",
    "ID_LANG_COVERAGE",
    "ID_MARKER_MISSING",
    "ID_MIGRATE",
    "ID_PENDING",
    "ID_PLACEHOLDER",
    "ID_PORTFOLIO",
    "INAPPLICABLE_MARKER",
    "LANG_MARKER",
    "MANAGED_BLOCK_LINE_BUDGET",
    "MANAGED_BLOCK_LINE_BUDGET_FINDING_ID",
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
    "PROJECT_AGENTS_MD_LINE_BUDGET",
    "PROJECT_AGENTS_MD_LINE_BUDGET_FINDING_ID",
    "RATCHET_DECLARATION_PHRASES",
    "RATCHET_UNCOVERED_FINDING_ID",
    "REQUIRED_HEADINGS",
    "RESEARCH_FRESHNESS_FINDING_ID",
    "RESEARCH_FRESHNESS_MAX_DAYS",
    "REVIEW_MARKER",
    "SCHEMA_VERSION",
    "SIGNIFICANT_HEADING_PREFIX",
    "SIGNIFICANT_NONEMPTY_LINE_THRESHOLD",
    "STARTER_TEMPLATE_TOKEN",
    "UI_FRAMEWORK_SIGNALS",
    "UX_APP_FRAMEWORKS",
    "UX_ROUTER_DEP_SIGNALS",
    "VERSION_SPECS",
    "PolicyVersionSpec",
    "core_files_for_version",
    "guard_inventory_header_for_version",
    "is_allowed_frozen_version",
    "portfolio_schema_version_for_version",
    "spec_for_version",
    "supported_portfolio_schema_versions",
]
