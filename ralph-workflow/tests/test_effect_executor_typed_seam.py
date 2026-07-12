"""Contract tests for the ``execute_agent_effect`` dependency seam.

The display/state/policy dependencies used to travel through ``**opts:
object``, which made a missing ``display_context`` invisible to mypy and
produced a runtime ``TypeError`` deep inside display resolution (the
project-policy remediation launch crash). These tests pin the seam as
explicit typed parameters, with ``display_context`` required, so an
omission is a type-check and signature error rather than a runtime flood.
"""

from __future__ import annotations

import inspect

from ralph.pipeline.effect_executor import execute_agent_effect


def _param(name: str) -> inspect.Parameter:
    return inspect.signature(execute_agent_effect).parameters[name]


def test_display_context_is_required_keyword_parameter() -> None:
    param = _param("display_context")
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


def test_display_dependencies_are_explicit_parameters_not_opts() -> None:
    parameters = inspect.signature(execute_agent_effect).parameters
    for name in ("display", "verbosity", "state", "policy_bundle"):
        assert name in parameters, f"{name} must be an explicit parameter"
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
