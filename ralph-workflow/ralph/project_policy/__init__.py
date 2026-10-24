"""Project-policy-readiness preflight for Ralph Workflow.

This package owns the deterministic, project-policy-readiness preflight that
runs at the start of every Ralph Workflow invocation (NOT gated on
``ralph --init``). It is intentionally physically separate from the
``ralph/policy/`` orchestration-policy package; the two packages have nothing
to do with each other at the code level.

* ``ralph/policy/``    — the orchestration policy (drain bindings, agent
                         chains, phase graphs, artifacts, MCP).
* ``ralph/project_policy/`` — the project quality-policy readiness capability
                              (canonical files under
                              ``docs/ralph-workflow-policy/``, AGENTS.md
                              bootstrap, deterministic validator, change-aware
                              cache, optional remediation driver).

The capability surfaced by this package:

1. Detects an opt-out via the byte-exact
   ``<!-- ralph-workflow-policy: skip -->`` marker in AGENTS.md.
2. Builds the shared readiness-evidence inventory the validator and the cache
   BOTH consume (so a stale ready cannot diverge from a fresh validation).
3. Idempotently bootstraps AGENTS.md and CLAUDE.md with a managed
   instruction block.
4. Runs a deterministic, versioned machine-checkable validator over every
   canonical policy file (presence, identifier, schema, headings, citations,
   field markers, placeholders, template banners, per-language coverage).
5. Caches READY under a content+signature key so a single ready run does not
   pay for validation on every subsequent preflight; edits and deletions to
   any input invalidates the cached READY.
6. When NOT ready, hands findings to a single bounded remediation agent
   outside the phase graph and ALWAYS re-runs the validator afterward.

Public API:

* :func:`run_policy_readiness_preflight` — orchestrator (opt-out → cache →
  bootstrap → validate → cache/return).
* :func:`validate_readiness` — deterministic validator (also exposed for
  tests).
* :func:`ralph.project_policy.pipeline_driver.run_policy_pipeline` — the
  two-phase remediation/analysis pipeline the preflight hands off to.
"""

from __future__ import annotations

from ralph.project_policy import (
    _content_checks,
    agents_md,
    evidence,
    models,
    remediation,
    validators,
)
from ralph.project_policy.models import PolicyFinding, PortfolioError
from ralph.project_policy.portfolio import PolicyPortfolio, parse_portfolio_toml
from ralph.project_policy.preflight import run_policy_readiness_preflight
from ralph.project_policy.validators import validate_readiness

__all__ = [
    "PolicyFinding",
    "PolicyPortfolio",
    "PortfolioError",
    "agents_md",
    "check_capture_command_fact",
    "evidence",
    "models",
    "parse_portfolio_toml",
    "remediation",
    "run_policy_readiness_preflight",
    "validate_readiness",
    "validators",
]


def check_capture_command_fact(content: str, path: str, filename: str) -> list[PolicyFinding]:
    """Return the findings for a policy file's ``design_capture_command`` fact.

    The visual pipeline parses this one fact from the design-system policy and
    silently skips visual review when the command is unusable (prose, shell
    metacharacters, a bare executable). The readiness validator reports the
    same defect as a remediable policy finding, and this accessor exposes that
    per-file check to callers outside the validator.
    """
    return _content_checks._check_capture_command_fact(content, path, filename)


def __getattr__(name: str) -> object:
    """Lazy attribute access for names not exported eagerly."""
    msg = f"module 'ralph.project_policy' has no attribute {name!r}"
    raise AttributeError(msg)
