from __future__ import annotations

import inspect

from ralph.agents.invoke.attempt_supervision.attempt_supervisor import AttemptSupervisor


def test_teardown_subtree_accessor_exposes_the_teardown_function() -> None:
    supervisor = AttemptSupervisor(object())
    teardown = supervisor.teardown_subtree
    parameters = inspect.signature(teardown).parameters
    assert "issuer" in parameters


def test_observation_teardown_is_reachable_under_its_own_name() -> None:
    supervisor = AttemptSupervisor(object())
    assert callable(supervisor.teardown_observed_subtree)
    assert supervisor.teardown_observed_subtree is not supervisor.teardown_subtree
