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
SCHEMA_VERSION: Final[str] = "v1"
POLICY_SCHEMA_MARKER: Final[str] = "<!-- ralph-policy-schema: v1 -->"

# Opt-out: byte-exact. The opt-out only fires on this exact substring of
# AGENTS.md; near-miss prose, additional words, or any whitespace/case change
# is treated as a non-match so accidental prose cannot disable the capability.
OPT_OUT_MARKER: Final[str] = "<!-- ralph-workflow-policy: skip -->"

# Significance heuristic for a pre-existing, marker-free AGENTS.md. The run
# preflight offers the interactive "skip inline policy" choice only when the
# file is significant: it contains at least one markdown heading line (a line
# starting with the prefix below) OR at least the threshold count of
# non-empty lines. Deterministic line checks only — no NLP, no fuzzy match.
SIGNIFICANT_HEADING_PREFIX: Final[str] = "#"
SIGNIFICANT_NONEMPTY_LINE_THRESHOLD: Final[int] = 10

# Managed instruction block in AGENTS.md. The block is bracketed by these two
# markers so repeated preflights can detect a pre-existing block without
# re-appending it (idempotent bootstrap).
AGENTS_BLOCK_BEGIN: Final[str] = "<!-- ralph-workflow-policy:begin v1 -->"
AGENTS_BLOCK_END: Final[str] = "<!-- ralph-workflow-policy:end -->"

# Canonical policy directory (relative to the workspace root).
CANONICAL_DIR: Final[str] = "docs/ralph-workflow-policy/"

# Required agent instruction files in the project.
AGENTS_MD: Final[str] = "AGENTS.md"
CLAUDE_MD: Final[str] = "CLAUDE.md"

# Core policy files (the nine always-required for any software project).
CORE_POLICY_FILES: Final[tuple[str, ...]] = (
    "testing-policy.md",
    "typechecking-policy.md",
    "linting-policy.md",
    "dependency-policy.md",
    "verification-policy.md",
    "agent-policy.md",
    "clean-code-policy.md",
    "documentation-policy.md",
    "security-policy.md",
)

# Conditional policy files keyed by their domain. Each conditional domain is
# required only when its deterministic signal set (UI framework / CSS family /
# app framework / router dep / perf signal / memory signal) matches the project.
CONDITIONAL_POLICY_FILES: Final[dict[str, str]] = {
    "design-system": "design-system-policy.md",
    "ux": "ux-policy.md",
    "performance": "performance-policy.md",
    "memory-usage": "memory-usage-policy.md",
}

# Required headings per policy file. Every starter AND every project-customized
# policy must include the listed headings (case-insensitive match against H1/H2
# lines). The verification policy additionally requires a "Bypass detection"
# heading because lint/typecheck bypass detection is mandatory when the
# selected tools permit such checks. The security policy additionally requires
# a "Threat surfaces" heading because security requirements are app-type
# specific (memory safety vs CSRF vs subprocess hygiene): the section that
# enumerates the project's own surfaces must survive every amendment.
REQUIRED_HEADINGS: Final[dict[str, tuple[str, ...]]] = {
    "testing-policy.md": (
        "Purpose and scope",
        "Default requirements",
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
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "verification-policy.md": (
        "Purpose and scope",
        "Default requirements",
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Bypass detection",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
    "agent-policy.md": (
        "Purpose and scope",
        "Default requirements",
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
        "Project facts to resolve",
        "AI execution instructions",
        "Verification",
        "Exceptions",
        "Maintenance triggers",
        "Research basis",
        "Ralph markers",
    ),
}

# Per-policy identifier prefix and per-file completion marker. Each canonical
# file must contain a `<!-- ralph-policy-id: <filename> -->` line and the
# completion marker. Both are exact-substring matches; the validator rejects
# any file that omits them or whose identifier does not match its filename.
POLICY_ID_PREFIX: Final[str] = "<!-- ralph-policy-id:"
COMPLETION_MARKER: Final[str] = "<!-- ralph-policy-complete -->"

# Machine-checkable field markers. The validator parses these as line-prefixed
# facts so policy content can be checked deterministically (no prose NLP).
#
#   RALPH-FACT: <key>: <value>          A project-fact field that must be
#                                       resolved with a real value (not a
#                                       placeholder token).
#   RALPH-COMMAND: <command>            A runnable verification command.
#                                       Must be non-empty and contain no
#                                       placeholder token.
#   RALPH-INAPPLICABLE: <reason>        Explicit declaration that a check
#                                       does not apply to this project; an
#                                       alternative to RALPH-COMMAND.
#   RALPH-LANG: <Language>              Marks a per-language declaration in
#                                       typecheck/lint starters; the line
#                                       that follows must include a
#                                       RALPH-COMMAND or RALPH-INAPPLICABLE.
FACT_MARKER: Final[str] = "RALPH-FACT:"
COMMAND_MARKER: Final[str] = "RALPH-COMMAND:"
INAPPLICABLE_MARKER: Final[str] = "RALPH-INAPPLICABLE:"
LANG_MARKER: Final[str] = "RALPH-LANG:"

# Tokens that mark a policy line or fact value as unresolved. The validator
# rejects any policy containing any of these substrings. They are also embedded
# in starter content so a freshly-seeded starter fails its own validation until
# the remediation agent replaces them with verified project facts. Adding to
# this tuple is the only way to forbid a new placeholder.
PLACEHOLDER_TOKENS: Final[tuple[str, ...]] = (
    "TODO",
    "TBD",
    "FIXME",
    "<REPLACE>",
    "{{",
    "REPLACE-ME",
    "PROJECT-FACT-UNRESOLVED",
)

# Every starter opens with a RALPH-STARTER-TEMPLATE banner comment that marks
# the file as an unfilled template. The token is deliberately NOT in
# PLACEHOLDER_TOKENS: the whole-file placeholder scan reports only the FIRST
# token it finds, so the banner would be shadowed by PROJECT-FACT-UNRESOLVED
# in a fresh starter. The dedicated validator check gives the banner its own
# stable finding id and an actionable required_outcome, so the remediation
# agent always sees "delete the banner" as an explicit step and a finished
# policy file carries no template scaffolding.
STARTER_TEMPLATE_TOKEN: Final[str] = "RALPH-STARTER-TEMPLATE"

# Approved gate tool executables. The validator checks every RALPH-COMMAND
# value's first whitespace-separated token against this fixed allowlist. A
# command whose first token is NOT in this set is rejected with a stable
# RWP-CMD:* finding so the project cannot satisfy the "executable gate"
# contract with arbitrary prose. The allowlist is the deterministic
# machine-checkable command contract required by the analysis feedback:
# it captures "verified project gate entry form" without consulting an AI.
#
# Rationale: the project already exposes well-known gate executables
# (pytest, mypy, ruff, make, cargo, go, npm, etc.) — these are the only
# ones whose presence proves the gate is runnable. Adding an entry here
# expands the allowlist; removing one tightens it. The validator never
# relies on path I/O to "verify the binary exists" — the executable name
# being on the allowlist IS the form contract, mirroring how the policy
# file's wording is checked structurally.
#
# Special cases:
# * ``make`` is accepted with any target (including no target). The make
#   program itself reports an unknown target at runtime; declaring
#   ``make <target>`` is the documented gate form, not a contract we can
#   verify without an AI.
# * ``echo``, ``cat``, ``ls``, ``find``, ``true``, ``bash``, ``sh`` are
#   shell utilities permitted in test fixtures and smoke checks; they
#   are not verification gates themselves but the unit-test commands
#   used in policy test fixtures rely on them.
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
        # Shell utilities permitted in test fixtures / smoke checks.
        "echo",
        "cat",
        "ls",
        "find",
        "bash",
        "sh",
        "true",
        # Bash strict-POSIX fallbacks (rare but legitimate in CI scripts).
        "/bin/sh",
        "/bin/bash",
    }
)

# Per-citation required fields. The validator parses the Research basis
# section into per-source citation blocks and checks each block contains tokens
# for every required field. "http" matches any URL scheme (the URL check is
# structural, not scheme-specific).
CITATION_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "publisher",
    "title",
    "http",
    "review date",
)

# Conditional-domain signal sets. The evidence module uses these to decide
# whether a domain-specific policy file is required for a project.
#
# UI framework names: matches the strings the language_detector emits for
# detected frameworks. Adding to this tuple teaches the validator about a new
# UI framework WITHOUT touching any prose rules.
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

# CSS-family languages: a project whose secondary languages include any of
# these triggers a design-system policy. CSS/SCSS/Sass/Less are detected by
# the language detector via file extensions.
CSS_LANGUAGE_SIGNALS: Final[frozenset[str]] = frozenset(
    {"CSS", "SCSS", "Sass", "Less"}
)

# Stricter signal set for UX: only when the project uses a SPA-style app
# framework does a UX policy become required. Design-system can be required
# without UX; UX always implies design-system.
UX_APP_FRAMEWORKS: Final[frozenset[str]] = frozenset(
    {"Angular", "Next.js", "Nuxt", "Gatsby"}
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

# Performance signal file paths (relative to workspace root). Existence of any
# of these is the deterministic trigger that a performance policy is required.
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

# Memory-usage dependency substrings.
MEMORY_DEP_SIGNALS: Final[tuple[str, ...]] = (
    "memlab",
    "clinic",
    "heaptrack",
)

# Migration: a finite candidate inventory the validator inspects for
# already-existing policy-like content. Every entry is a relative path scanned
# on the workspace seam (NOT raw Path I/O). Adding a path here means "scan
# this file for migration candidates on every preflight".
MIGRATION_CANDIDATE_PATHS: Final[tuple[str, ...]] = (
    # AGENTS.md itself: user-authored policy-like sections in it must be
    # INTEGRATED into the canonical dir (single source of truth), not left
    # as a parallel rulebook next to the managed block. The managed block
    # (placeholder or condensed) contains no headings, so bootstrap output
    # alone never makes AGENTS.md a candidate.
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

# Exact lowercased heading phrases that mark a doc as a migration candidate.
# The scanner normalizes H1/H2 lines (strip leading hashes, trim whitespace,
# lowercase) and looks for a recognizable phrase as a substring. Unrelated
# mixed-purpose docs (no recognized heading) are never candidates.
MIGRATION_HEADING_RECOGNIZERS: Final[tuple[str, ...]] = (
    "testing",
    "test policy",
    "linting",
    "type check",
    "typechecking",
    "dependencies",
    "dependency policy",
    "verification",
    "agent policy",
    "code style",
    "clean code",
    "documentation policy",
    "security",
    "threat model",
)

# Exact marker a migrated file embeds to declare itself resolved. The token
# ``{target}`` is replaced with the destination canonical file name; an
# RWP-MIGRATE-UNRECONCILED finding persists until the marker is present AND
# the target file exists.
MIGRATED_MARKER_TEMPLATE: Final[str] = (
    "<!-- ralph-workflow-policy:migrated -> docs/ralph-workflow-policy/{target} -->"
)

# Cache file path for the change-aware READY cache. Stored under .agent/tmp
# so it travels with the workspace but stays out of source control.
CACHE_REL_PATH: Final[str] = ".agent/tmp/policy_readiness_cache.json"

# Stable finding-id prefixes. The validator emits exactly one PolicyFinding
# per (requirement, target); the requirement_id is one of these literal
# prefixes concatenated with a stable suffix so the SAME id appears in the
# deterministic validator, the remediation prompt, and the BLOCKED report.
# External code (e.g. future migrations) can match on these prefixes safely.
ID_CORE_MISSING: Final[str] = "RWP-CORE"
ID_CMD_UNUSABLE: Final[str] = "RWP-CMD"
ID_LANG_COVERAGE: Final[str] = "RWP-LANG"
ID_CITATION_MISSING: Final[str] = "RWP-CITATION"
ID_HEADING_MISSING: Final[str] = "RWP-HEADING"
ID_PLACEHOLDER: Final[str] = "RWP-PLACEHOLDER"
ID_COMPLETION_MISSING: Final[str] = "RWP-COMPLETION"
ID_MARKER_MISSING: Final[str] = "RWP-MARKER"
ID_AGENTS_MD_MISSING: Final[str] = "RWP-AGENTS-MD"
ID_CLAUDE_MD_MISSING: Final[str] = "RWP-CLAUDE-MD"
ID_DOMAIN: Final[str] = "RWP-DOMAIN"
ID_MIGRATE: Final[str] = "RWP-MIGRATE-UNRECONCILED"


__all__ = [
    "AGENTS_BLOCK_BEGIN",
    "AGENTS_BLOCK_END",
    "AGENTS_MD",
    "APPROVED_GATE_TOOLS",
    "CACHE_REL_PATH",
    "CANONICAL_DIR",
    "CITATION_REQUIRED_FIELDS",
    "CLAUDE_MD",
    "COMMAND_MARKER",
    "COMPLETION_MARKER",
    "CONDITIONAL_POLICY_FILES",
    "CORE_POLICY_FILES",
    "CSS_LANGUAGE_SIGNALS",
    "FACT_MARKER",
    "ID_AGENTS_MD_MISSING",
    "ID_CITATION_MISSING",
    "ID_CLAUDE_MD_MISSING",
    "ID_CMD_UNUSABLE",
    "ID_COMPLETION_MISSING",
    "ID_CORE_MISSING",
    "ID_DOMAIN",
    "ID_HEADING_MISSING",
    "ID_LANG_COVERAGE",
    "ID_MARKER_MISSING",
    "ID_MIGRATE",
    "ID_PLACEHOLDER",
    "INAPPLICABLE_MARKER",
    "LANG_MARKER",
    "MEMORY_DEP_SIGNALS",
    "MEMORY_SIGNAL_PATHS",
    "MIGRATED_MARKER_TEMPLATE",
    "MIGRATION_CANDIDATE_PATHS",
    "MIGRATION_DOCS_GLOB_DIR",
    "MIGRATION_HEADING_RECOGNIZERS",
    "OPT_OUT_MARKER",
    "PERF_DEP_SIGNALS",
    "PERF_SIGNAL_PATHS",
    "PLACEHOLDER_TOKENS",
    "POLICY_ID_PREFIX",
    "POLICY_SCHEMA_MARKER",
    "REQUIRED_HEADINGS",
    "SCHEMA_VERSION",
    "SIGNIFICANT_HEADING_PREFIX",
    "SIGNIFICANT_NONEMPTY_LINE_THRESHOLD",
    "STARTER_TEMPLATE_TOKEN",
    "UI_FRAMEWORK_SIGNALS",
    "UX_APP_FRAMEWORKS",
    "UX_ROUTER_DEP_SIGNALS",
]
