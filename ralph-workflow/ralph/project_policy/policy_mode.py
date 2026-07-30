"""How a run was asked to treat project policy.

Four CLI flags select a non-default mode, in two pairs. The ``_ONLY`` suffix
means "exit after the policy pipeline instead of continuing into the development
run"; the ``REDO`` pair wipes the existing policy first, the ``RUN_AGENTS`` pair
reviews it in place.

============================  =======  ===========================  ============
Mode                          Wipes?   Enters at                    After
============================  =======  ===========================  ============
``NORMAL``                    no       remediation (if findings)    continue
``REDO``                      yes      remediation                  continue
``REDO_ONLY``                 yes      remediation                  exit
``RUN_AGENTS``                no       analysis                     continue
``RUN_AGENTS_ONLY``           no       analysis                     exit
============================  =======  ===========================  ============

Every explicit mode bypasses the READY cache (a mode that no-ops on a ready
project would be useless) and overrides the AGENTS.md opt-out marker (explicit
CLI intent beats a persisted marker). Only the ``REDO`` pair *strips* the opt-out
marker from the file; ``RUN_AGENTS`` merely declines to honor it for this run.
"""

from __future__ import annotations

from enum import StrEnum

from ralph.project_policy.pipeline_graph import PHASE_ANALYSIS, PHASE_REMEDIATION


class PolicyMode(StrEnum):
    """The policy behavior selected for this run."""

    NORMAL = "normal"
    REDO = "redo"
    REDO_ONLY = "redo_only"
    RUN_AGENTS = "run_agents"
    RUN_AGENTS_ONLY = "run_agents_only"

    def is_explicit(self) -> bool:
        """True when the user asked for policy work with a CLI flag.

        An explicit mode bypasses the READY cache and the opt-out marker.
        """
        return self is not PolicyMode.NORMAL

    def resets_policy(self) -> bool:
        """True when the existing policy is wiped before the pipeline runs."""
        return self in (PolicyMode.REDO, PolicyMode.REDO_ONLY)

    def exits_after(self) -> bool:
        """True when the run stops after the policy pipeline.

        This is the ONLY case in which a policy outcome can produce a non-zero
        exit code: an ``_ONLY`` invocation has no development run to proceed to,
        so its exit code is the only signal it can give. A normal run NEVER exits
        non-zero because of policy.
        """
        return self in (PolicyMode.REDO_ONLY, PolicyMode.RUN_AGENTS_ONLY)

    def entry_phase(self) -> str:
        """The phase the policy pipeline enters at.

        The ``RUN_AGENTS`` pair enters at ANALYSIS: it reviews the EXISTING policy
        in place (are the facts still true, do the gates still resolve, do the
        scripts still obey the gate-script policy) and only routes back to
        remediation if the review finds something wrong. Nothing is overwritten
        unless analysis proves it should be.
        """
        if self in (PolicyMode.RUN_AGENTS, PolicyMode.RUN_AGENTS_ONLY):
            return PHASE_ANALYSIS
        return PHASE_REMEDIATION


__all__ = ["PolicyMode"]
