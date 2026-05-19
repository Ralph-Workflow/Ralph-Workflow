"""Explanation of a single phase."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commit_policy_explanation import CommitPolicyExplanation
    from .loop_policy_explanation import LoopPolicyExplanation
    from .verification_explanation import VerificationExplanation


@dataclass
class PhaseExplanation:
    """Explanation of a single phase."""

    name: str
    role: str | None
    drain: str
    chain: str | None
    agents: list[str]
    max_retries: int
    skip_invocation: bool
    on_success: str | None
    on_failure: str | None
    on_loopback: str | None
    bypass_routes: dict[str, str]
    decisions: dict[str, str]
    loop_policy: LoopPolicyExplanation | None
    commit_policy: CommitPolicyExplanation | None
    terminal_outcome: str | None
    clean_outcome: str | None = None
    issues_outcome: str | None = None
    is_entry: bool = False
    is_terminal: bool = False
    verification: VerificationExplanation | None = None
    has_parallelization: bool = False
    post_commit_routes_info: list[tuple[str, str]] = field(default_factory=list)
    workflow_fallback: tuple[str, str | None] | None = None
