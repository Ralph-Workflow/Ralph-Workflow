from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from ralph.project_policy._required_headings import REQUIRED_HEADINGS

SCHEMA_VERSION: Final[str] = "v6"
POLICY_SCHEMA_MARKER: Final[str] = "<!-- ralph-policy-schema: v6 -->"
POLICY_CONTRACT_VERSION: Final[str] = "2026-11-09.1"

OPT_OUT_MARKER: Final[str] = "<!-- ralph-workflow-policy: skip -->"

SIGNIFICANT_HEADING_PREFIX: Final[str] = "#"
SIGNIFICANT_NONEMPTY_LINE_THRESHOLD: Final[int] = 10

AGENTS_BLOCK_BEGIN_V1: Final[str] = "<!-- ralph-workflow-policy:begin v1 -->"
AGENTS_BLOCK_BEGIN: Final[str] = "<!-- ralph-workflow-policy:begin v2 -->"
AGENTS_BLOCK_END: Final[str] = "<!-- ralph-workflow-policy:end -->"

AGENTS_BLOCK_BEGIN_RE: Final[re.Pattern[str]] = re.compile(
    r"<!-- ralph-workflow-policy:begin v\d+ -->"
)

CANONICAL_DIR: Final[str] = "docs/ralph-workflow-policy/"
APPLICABILITY_OVERRIDES_PATH: Final[str] = "docs/ralph-workflow-policy/applicability.toml"
PORTFOLIO_PATH: Final[str] = "docs/ralph-workflow-policy/policy-portfolio.toml"
PORTFOLIO_SCHEMA_VERSION: Final[str] = "v3"

AGENTS_MD: Final[str] = "AGENTS.md"
CLAUDE_MD: Final[str] = "CLAUDE.md"

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

POLICY_ID_PREFIX: Final[str] = "<!-- ralph-policy-id:"

FACT_MARKER: Final[str] = "RALPH-FACT:"
COMMAND_MARKER: Final[str] = "RALPH-COMMAND:"
INAPPLICABLE_MARKER: Final[str] = "RALPH-INAPPLICABLE:"
REVIEW_MARKER: Final[str] = "RALPH-REVIEW:"
LANG_MARKER: Final[str] = "RALPH-LANG:"
PENDING_MARKER: Final[str] = "RALPH-PENDING:"
PENDING_SENTINEL: Final[str] = "RALPH-PENDING"

PLACEHOLDER_TOKENS: Final[tuple[str, ...]] = (
    "TODO",
    "TBD",
    "FIXME",
    "<REPLACE>",
    "{{",
    "REPLACE-ME",
    "PROJECT-FACT-UNRESOLVED",
    "<date>",
    "<trigger>",
)

STARTER_TEMPLATE_TOKEN: Final[str] = "RALPH-STARTER-TEMPLATE"

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

CITATION_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "publisher",
    "title",
    "http",
    "review date",
)

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

CSS_LANGUAGE_SIGNALS: Final[frozenset[str]] = frozenset({"CSS", "SCSS", "Sass", "Less"})

UX_APP_FRAMEWORKS: Final[frozenset[str]] = frozenset(
    {"React", "Vue", "Angular", "Svelte", "Next.js", "Nuxt", "Gatsby"}
)

UX_ROUTER_DEP_SIGNALS: Final[tuple[str, ...]] = (
    "react-router",
    "@angular/router",
    "vue-router",
    "@remix-run",
    "react-navigation",
    "@tanstack/router",
)

PERF_SIGNAL_PATHS: Final[tuple[str, ...]] = (
    "performance-budget.json",
    "lighthouse-budget.json",
    ".lighthouserc.js",
    ".lighthouserc.json",
    "benches/",
    "criterion.toml",
)

PERF_DEP_SIGNALS: Final[tuple[str, ...]] = (
    "k6",
    "artillery",
    "lighthouse",
)

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

MIGRATION_CANDIDATE_PATHS: Final[tuple[str, ...]] = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "CONTRIBUTING.rst",
    "TESTING.md",
    "DEVELOPMENT.md",
    "SECURITY.md",
    ".github/SECURITY.md",
    "docs/testing.md",
    "docs/development.md",
    "docs/contributing.md",
    "docs/security.md",
    ".github/CONTRIBUTING.md",
)

MIGRATION_DOCS_GLOB_DIR: Final[str] = "docs"

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

MIGRATED_MARKER_TEMPLATE: Final[str] = (
    "<!-- ralph-workflow-policy:migrated -> docs/ralph-workflow-policy/{target} -->"
)

MIGRATED_MARKER_PREFIX: Final[str] = "<!-- ralph-workflow-policy:migrated ->"

CACHE_REL_PATH: Final[str] = ".agent/tmp/policy_readiness_cache.json"

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

CLAIM_SCOPE_MISSING_FINDING_ID: Final[str] = "RWP-INVENTORY:CLAIM-SCOPE-MISSING"
CLAIM_SCOPE_MULTI_SENTENCE_FINDING_ID: Final[str] = "RWP-INVENTORY:CLAIM-SCOPE-MULTI-SENTENCE"
RATCHET_UNCOVERED_FINDING_ID: Final[str] = "RWP-INVENTORY:RATCHET-UNCOVERED"
CONTROL_ORPHAN_FINDING_ID: Final[str] = "RWP-INVENTORY:CONTROL-ORPHAN"

ENFORCEMENT_MISSING_FINDING_ID: Final[str] = "RWP-INVENTORY:ENFORCEMENT-MISSING"
ENFORCEMENT_UNKNOWN_ID_FINDING_ID: Final[str] = "RWP-INVENTORY:ENFORCEMENT-UNKNOWN-ID"
ENFORCEMENT_ADVISORY_NO_TRIGGER_FINDING_ID: Final[str] = (
    "RWP-INVENTORY:ENFORCEMENT-ADVISORY-NO-TRIGGER"
)

ID_INSTRUCTION: Final[str] = "RWP-INSTRUCTION"
MANAGED_BLOCK_LINE_BUDGET_FINDING_ID: Final[str] = "RWP-AGENTS-MD:MANAGED-BLOCK-LINE-BUDGET"
PROJECT_AGENTS_MD_LINE_BUDGET_FINDING_ID: Final[str] = "RWP-AGENTS-MD:PROJECT-LINE-BUDGET"
AGENTS_MD_CITED_PATH_FINDING_ID: Final[str] = "RWP-AGENTS-MD:CITED-PATH-MISSING"
HIDDEN_UNICODE_FINDING_ID: Final[str] = "RWP-INSTRUCTION:HIDDEN-UNICODE"
RESEARCH_FRESHNESS_FINDING_ID: Final[str] = "RWP-CITATION:RESEARCH-FRESHNESS"

MANAGED_BLOCK_LINE_BUDGET: Final[int] = 40
PROJECT_AGENTS_MD_LINE_BUDGET: Final[int] = 200
RESEARCH_FRESHNESS_MAX_DAYS: Final[int] = 365

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

_GUARD_INVENTORY_CLAIM_SCOPE_MAX_SENTENCES: Final[int] = 1


@dataclass(frozen=True, slots=True)
class PolicyVersionSpec:
    version: str
    core_manifest: tuple[str, ...]
    guard_inventory_header: tuple[str, ...] | None
    portfolio_schema_version: tuple[str, ...]
    allowed_frozen: bool = True

    @property
    def guard_inventory_columns(self) -> int:
        return len(self.guard_inventory_header) if self.guard_inventory_header is not None else 0


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
    if v is not None and v in VERSION_SPECS:
        return VERSION_SPECS[v].core_manifest
    return CORE_POLICY_FILES


def spec_for_version(v: str | None) -> PolicyVersionSpec:
    if v is not None and v in VERSION_SPECS:
        return VERSION_SPECS[v]
    return VERSION_SPECS[SCHEMA_VERSION]


def is_allowed_frozen_version(v: str) -> bool:
    spec = VERSION_SPECS.get(v)
    return spec is not None and spec.allowed_frozen


def guard_inventory_header_for_version(v: str | None) -> tuple[str, ...] | None:
    if v is not None and v in VERSION_SPECS:
        return VERSION_SPECS[v].guard_inventory_header
    return VERSION_SPECS[SCHEMA_VERSION].guard_inventory_header


def portfolio_schema_version_for_version(v: str | None) -> str:
    if v is not None and v in VERSION_SPECS:
        return VERSION_SPECS[v].portfolio_schema_version[0]
    return VERSION_SPECS[SCHEMA_VERSION].portfolio_schema_version[0]


def supported_portfolio_schema_versions() -> frozenset[str]:
    return frozenset(
        ver for spec in VERSION_SPECS.values() for ver in spec.portfolio_schema_version
    )


RATCHET_DECLARATION_PHRASES: Final[tuple[str, ...]] = (
    "ratcheted",
    "ratchet",
    "MUST NOT grow",
    "MUST NOT increase",
)

CONTROL_TOKEN_RE: Final[str] = r"<!--\s*ralph-control:\s*([a-z0-9-]+)\s*-->"
CONTROL_ID_RE: Final[str] = r"`([a-z0-9-]+)`"


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
    "AGENTS_BLOCK_BEGIN_RE",
    "AGENTS_BLOCK_BEGIN_V1",
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
