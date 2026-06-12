"""Tests for ``ralph.testing.audit_parallelization_dormant``.

The audit enforces six non-vacuous literal-string invariants that keep
the prompt, format doc, effect-router WARNING, bundled pipeline.toml,
and planning_analysis.jinja rubric in lockstep with the bundled
default (``dispatch_mode = 'agent_subagents'``, fan-out dormant).
"""

from __future__ import annotations

import ralph.testing.audit_parallelization_dormant as audit_module
from ralph.testing.audit_parallelization_dormant import main as audit_main


def test_audit_returns_zero_when_all_invariants_satisfied() -> None:
    assert audit_main([]) == 0


def test_audit_main_returns_zero_on_clean_tree() -> None:
    """The same ``main()`` entry point used by ``make verify`` must return 0
    against the in-tree literals. Catches the case where the invariants
    regress but the imports still load.
    """
    assert audit_main([]) == 0


def test_audit_module_path() -> None:
    """Audit must be importable as ``ralph.testing.audit_parallelization_dormant``."""
    assert hasattr(audit_module, "main")
