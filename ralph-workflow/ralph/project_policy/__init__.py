from __future__ import annotations

from ralph.project_policy import (
    _content_checks,
    agents_md,
    evidence,
    instruction_surface,
    models,
    remediation,
    validators,
)
from ralph.project_policy._prompt_ui import PromptChoice, SelectFn
from ralph.project_policy._schema_upgrade import maybe_resolve_schema_upgrade
from ralph.project_policy.models import PolicyFinding, PortfolioError
from ralph.project_policy.portfolio import PolicyPortfolio, parse_portfolio_toml
from ralph.project_policy.preflight import (
    run_policy_readiness_preflight,
    seed_project_policy_starters,
)
from ralph.project_policy.validators import (
    check_policy_citations,
    get_research_freshness_clock,
    set_research_freshness_clock,
    validate_readiness,
)

__all__ = [
    "PolicyFinding",
    "PolicyPortfolio",
    "PortfolioError",
    "PromptChoice",
    "SelectFn",
    "agents_md",
    "check_capture_command_fact",
    "check_policy_citations",
    "evidence",
    "get_research_freshness_clock",
    "instruction_surface",
    "maybe_resolve_schema_upgrade",
    "models",
    "parse_portfolio_toml",
    "remediation",
    "run_policy_readiness_preflight",
    "seed_project_policy_starters",
    "set_research_freshness_clock",
    "validate_readiness",
    "validators",
]


def check_capture_command_fact(content: str, path: str, filename: str) -> list[PolicyFinding]:
    return _content_checks._check_capture_command_fact(content, path, filename)


def __getattr__(name: str) -> object:
    msg = f"module 'ralph.project_policy' has no attribute {name!r}"
    raise AttributeError(msg)
