from __future__ import annotations

import inspect

from ralph.agents.invoke.attempt_supervision.attempt_supervisor import AttemptSupervisor


def test_teardown_subtree_accessor_exposes_the_teardown_function() -> None:
    supervisor = AttemptSupervisor(object())
    teardown = supervisor.teardown_subtree
    parameters = inspect.signature(teardown).parameters
    assert "issuer" in parameters

