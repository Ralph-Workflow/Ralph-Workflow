"""Self-documenting ``StepType`` enum for the per-step contract.

The four members match the previously free-form ``Literal["file_change",
"action", "research", "verify"]`` field on ``PlanStep``. The default is
``StepType.ACTION`` (``"action"``) to preserve the existing serialized
output; drift is fixed in this commit; ``R-PLAN-04`` is closed.

The two helpers ``requires_targets`` and ``requires_verify_handle`` are
the single source of truth for which step types bind to which
contract. ``PlanStep._validate_step_type_contract`` consults them
instead of pattern-matching literal strings.
"""

from __future__ import annotations

from enum import StrEnum


class StepType(StrEnum):
    """Closed set of valid step kinds.

    - ``FILE_CHANGE``: the step creates, modifies, or deletes one or more
      source files. The model validator requires at least one ``targets``
      entry.
    - ``ACTION``: a non-mutating executor action (a command or a tool
      call). No target/handle binding.
    - ``RESEARCH``: an exploratory step that may not produce a code
      change. No target/handle binding.
    - ``VERIFY``: a pure-verification step (e.g. ``run ruff``, ``run
      pytest``) with no file changes. The model validator requires
      either ``verify_command`` or ``location``.
    """

    FILE_CHANGE = "file_change"
    ACTION = "action"
    RESEARCH = "research"
    VERIFY = "verify"


def requires_targets(step_type: StepType) -> bool:
    """Return True when the step type binds to the ``targets`` contract."""
    return step_type == StepType.FILE_CHANGE


def requires_verify_handle(step_type: StepType) -> bool:
    """Return True when the step type binds to the verify handle contract."""
    return step_type == StepType.VERIFY


__all__ = [
    "StepType",
    "requires_targets",
    "requires_verify_handle",
]
