import sys
from typing import Any

import pytest

from ralph.process.manager import (
    ManagedProcess,
    ProcessManager,
    ProcessTerminationIssuer,
    SpawnOptions,
    install_process_manager,
)
from ralph.process.teardown import teardown_subtree


def _sleeping_child(manager: ProcessManager, *, label: str = "invoke:process") -> ManagedProcess:
    return manager.spawn(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        SpawnOptions(label=label),
    )


def test_managed_terminate_requires_issuer() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    untyped_terminate: Any = child.terminate
    try:
        with pytest.raises(TypeError):
            untyped_terminate()
    finally:
        child.kill(issuer=ProcessTerminationIssuer.OPERATOR)


def test_bad_reason_does_not_signal_child() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    try:
        with pytest.raises(ValueError):
            child.terminate(issuer=ProcessTerminationIssuer.OPERATOR, reason="unknown")
        assert child.poll() is None
    finally:
        child.kill(issuer=ProcessTerminationIssuer.OPERATOR)


@pytest.mark.parametrize(
    ("issuer", "reason"),
    [
        (ProcessTerminationIssuer.QUOTA_DETECTOR, "quota_exhausted"),
        (ProcessTerminationIssuer.POST_EXIT_WATCHDOG, "operator_cancellation"),
        (ProcessTerminationIssuer.OPERATOR, "operator_cancellation"),
    ],
)
def test_managed_terminate_records_typed_issuer(
    issuer: ProcessTerminationIssuer, reason: str
) -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    child.terminate(issuer=issuer, reason=reason)
    assert manager.termination_issuer(child.pid) == issuer.value


def test_process_manager_terminate_records_typed_issuer() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    manager.terminate(child, issuer=ProcessTerminationIssuer.OPERATOR)
    assert manager.termination_issuer(child.pid) == "operator"


def test_teardown_records_signalled_without_changing_terminal_reason() -> None:
    manager = install_process_manager(ProcessManager())
    child = _sleeping_child(manager)
    teardown_subtree(child.pid, issuer="invoke:process")
    assert {
        "stage": "teardown_subtree",
        "outcome": "signalled",
        "issuer": "invoke_teardown",
    } in manager.list_termination_outcomes()[child.pid]
    assert manager.original_terminal_reason(child.pid) is None
    assert manager.termination_issuer(child.pid) is None


def test_record_teardown_rejects_unknown_outcome() -> None:
    manager = ProcessManager()
    with pytest.raises(ValueError):
        manager.record_teardown(
            1,
            issuer=ProcessTerminationIssuer.OPERATOR,
            outcome="unknown",
        )


def test_context_exit_records_context_exit() -> None:
    manager = ProcessManager()
    with _sleeping_child(manager) as child:
        pid = child.pid
    assert manager.termination_issuer(pid) == "context_exit"
