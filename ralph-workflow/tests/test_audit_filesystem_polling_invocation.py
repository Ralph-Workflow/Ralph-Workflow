"""Regression tests for filesystem polling and invocation ownership audit."""

from __future__ import annotations

from pathlib import Path

from ralph.testing import audit_filesystem_polling_invocation as audit


def _write_fake_package(tmp_path: Path, module_rel: str, body: str) -> Path:
    package_root = tmp_path / "ralph"
    module_path = package_root / module_rel
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(body, encoding="utf-8")
    return package_root


def test_regression_unowned_sleep_polling_fails_closed(tmp_path: Path) -> None:
    """S-6: a new timer-driven filesystem poll is rejected with P3 guidance."""
    module_rel = "feature/poller.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import time\ndef poll() -> None:\n    time.sleep(1)\n",
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_sleep_poll"
    assert "P3" in violations[0].message
    assert "injected clock" in violations[0].message


def test_regression_aliased_watchdog_module_construction_fails_closed(tmp_path: Path) -> None:
    """S-6: a module-qualified Observer alias cannot evade the watch owner."""
    module_rel = "feature/watch.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import watchdog.observers as observers\n"
        "def start() -> object:\n"
        "    return observers.Observer()\n",
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_observer_construction"


def test_regression_unowned_observer_construction_fails_closed(tmp_path: Path) -> None:
    """S-6: a second watch owner is rejected with P1 lifecycle guidance."""
    module_rel = "feature/watch.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from watchdog.observers import Observer\ndef start() -> object:\n    return Observer()\n",
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_observer_construction"
    assert "P1/P4" in violations[0].message
    assert "WorkspaceMonitor" in violations[0].message


def test_regression_unowned_subprocess_choice_fails_closed(tmp_path: Path) -> None:
    """S-6: product code cannot choose a direct subprocess outside typed owners."""
    module_rel = "feature/invoke.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import subprocess\ndef invoke() -> None:\n    subprocess.run(['tool'], check=True)\n",
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_subprocess_invocation"
    assert "typed process" in violations[0].message


def test_local_reasoned_marker_remains_an_explicit_exception(tmp_path: Path) -> None:
    """D3: a local non-empty lifecycle reason is the only exception path."""
    module_rel = "feature/poller.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import time\n"
        "def poll() -> None:\n"
        "    time.sleep(1)  # filesystem-poll-ok: external protocol backoff bounded by caller lifecycle\n",
    )

    assert (
        audit.audit_filesystem_polling_invocation(
            package_root,
            module_paths=(module_rel,),
        )
        == []
    )
