from __future__ import annotations

import itertools
import sys
import textwrap
from collections.abc import Generator

import pytest

import ralph.test_suites as test_suites_module
import tests.conftest as conftest_module
from ralph.process import ProcessManager, ProcessManagerPolicy, ProcessStatus, get_process_manager
from ralph.testing.fake_process import (
    FakePsutil,
    make_async_process_factory,
    make_sync_process_factory,
)

pytest_plugins = ("pytester",)


def _session_process_manager() -> ProcessManager:
    return ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
        sync_process_factory=make_sync_process_factory(itertools.count(10_000)),
        async_process_factory=make_async_process_factory(itertools.count(20_000)),
        psutil=FakePsutil(),
    )


def test_per_test_cleanup_runs_before_monkeypatch_restores_fake_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = conftest_module._isolate_default_process_manager.__wrapped__(monkeypatch)
    assert isinstance(lifecycle, Generator)
    next(lifecycle)

    session_manager = get_process_manager()
    fake_manager = _session_process_manager()
    monkeypatch.setattr(session_manager, "_sync_process_factory", fake_manager._sync_process_factory)
    monkeypatch.setattr(session_manager, "_psutil", fake_manager._psutil)
    child = session_manager.spawn([sys.executable, "-c", "pass"])

    try:
        next(lifecycle)
    except StopIteration:
        pass
    else:
        raise AssertionError("process-manager lifecycle fixture yielded more than once")

    assert child.record.status == ProcessStatus.KILLED
    assert get_process_manager() is not session_manager


@pytest.mark.subprocess_e2e
def test_per_test_cleanup_regression_runs_inside_real_pytest_lifecycle(
    pytester: pytest.Pytester,
) -> None:
    sentinel = pytester.path / "teardown-proof.txt"
    pytester.makeconftest(
        "from tests.conftest import _isolate_default_process_manager, pytest_unconfigure\n"
    )
    pytester.makepyfile(
        test_lifecycle=textwrap.dedent(
            f"""
            from __future__ import annotations

            import itertools
            import sys
            from pathlib import Path

            from ralph.process import ProcessStatus, get_process_manager
            from ralph.testing.fake_process import FakePsutil, make_sync_process_factory

            SENTINEL = Path({str(sentinel)!r})


            def test_fake_child_is_left_for_autouse_teardown(monkeypatch) -> None:
                manager = get_process_manager()
                pids = itertools.count(30_000)

                fake_factory = make_sync_process_factory(pids)

                def persist_terminal_event(event) -> None:
                    if event.new_status == ProcessStatus.KILLED:
                        patch_active = manager._sync_process_factory is fake_factory
                        SENTINEL.write_text(
                            f"patch_active={{patch_active}}\\nterminal=True\\n",
                            encoding="utf-8",
                        )

                monkeypatch.setattr(manager, "_sync_process_factory", fake_factory)
                monkeypatch.setattr(manager, "_psutil", FakePsutil())
                manager.register_listener(persist_terminal_event)
                child = manager.spawn([sys.executable, "-c", "pass"])

                assert child.record.status == ProcessStatus.RUNNING
                assert not SENTINEL.exists()
            """
        )
    )

    result = pytester.runpytest_inprocess("-q")

    result.assert_outcomes(passed=1)
    assert sentinel.read_text(encoding="utf-8") == "patch_active=True\nterminal=True\n"


def test_pytest_unconfigure_regression_releases_default_manager_children(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_manager = get_process_manager()
    fake_manager = _session_process_manager()
    monkeypatch.setattr(session_manager, "_sync_process_factory", fake_manager._sync_process_factory)
    monkeypatch.setattr(session_manager, "_psutil", fake_manager._psutil)
    child = session_manager.spawn([sys.executable, "-c", "pass"])

    conftest_module.pytest_unconfigure(request.config)

    assert child.record.status == ProcessStatus.KILLED
    assert get_process_manager() is not session_manager


def test_pytest_unconfigure_regression_preserves_outer_shard_manager(
    request: pytest.FixtureRequest,
) -> None:
    outer_manager = test_suites_module._PYTEST_SHARD_PROCESS_MANAGER
    get_process_manager()

    conftest_module.pytest_unconfigure(request.config)

    assert test_suites_module._PYTEST_SHARD_PROCESS_MANAGER is outer_manager
