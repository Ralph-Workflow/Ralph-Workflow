from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import audit_filesystem_polling_invocation as audit

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "marker exemption": "the inline marker comment no longer waives a polling finding",
    "structural exemption": "the structural exemption registry now carries the waived call site",
}


def _write_fake_package(tmp_path: Path, module_rel: str, body: str) -> Path:
    package_root = tmp_path / "ralph"
    module_path = package_root / module_rel
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(body, encoding="utf-8")
    return package_root


def test_regression_missing_default_production_root_fails_closed(tmp_path: Path) -> None:
    violations = audit.audit_filesystem_polling_invocation(tmp_path)

    assert len(violations) == 1
    assert violations[0].kind == "missing_production_root"
    assert violations[0].file_path == "ralph"
    assert "fail closed" in violations[0].message


def test_regression_explicit_module_path_cannot_escape_package_root(tmp_path: Path) -> None:
    package_root = _write_fake_package(tmp_path, "feature/inert.py", "VALUE = 1\n")
    external_module = tmp_path / "outside.py"
    external_module.write_text("VALUE = 1\n", encoding="utf-8")

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=("../outside.py",),
    )

    assert len(violations) == 1
    assert violations[0].kind == "invalid_module_path"
    assert violations[0].file_path == "../outside.py"


def test_regression_workspace_monitor_is_not_broadly_exempt_from_polling_enforcement(
    tmp_path: Path,
) -> None:
    module_rel = "agents/invoke/_workspace.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import time\ndef poll() -> None:\n    time.sleep(1)\n",
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_sleep_poll"]


def test_regression_exempt_suffix_cannot_skip_unrelated_module(tmp_path: Path) -> None:
    module_rel = "unrelated/agents/invoke/_workspace.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import time\ndef poll() -> None:\n    time.sleep(1)\n",
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_sleep_poll"]


def test_regression_marker_in_string_literal_does_not_bypass_enforcement(tmp_path: Path) -> None:
    module_rel = "feature/poller.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import time\n"
        "def poll() -> None:\n"
        "    reason = 'filesystem-poll-ok: not a comment'\n"
        "    time.sleep(1)\n",
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_sleep_poll"]


def test_non_utf8_candidate_module_fails_closed(tmp_path: Path) -> None:
    module_rel = "feature/non_utf8.py"
    package_root = tmp_path / "ralph"
    module_path = package_root / module_rel
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_bytes(b"\xff")

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "unreadable_module"
    assert violations[0].file_path == module_rel


def test_regression_unowned_sleep_polling_fails_closed(tmp_path: Path) -> None:
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


def test_regression_asyncio_sleep_polling_fails_closed(tmp_path: Path) -> None:
    module_rel = "feature/async_poller.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import asyncio\nasync def poll() -> None:\n    await asyncio.sleep(1)\n",
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_sleep_poll"
    assert "P3" in violations[0].message


def test_regression_aliased_watchdog_module_construction_fails_closed(tmp_path: Path) -> None:
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


def test_regression_root_watchdog_package_observer_construction_fails_closed(
    tmp_path: Path,
) -> None:
    module_rel = "feature/watch.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import watchdog\ndef start() -> object:\n    return watchdog.observers.Observer()\n",
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_observer_construction"]


def test_regression_dynamic_watchdog_observer_construction_fails_closed(tmp_path: Path) -> None:
    module_rel = "feature/watch.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import watchdog.observers as observers\n"
        "def start() -> object:\n"
        "    return getattr(observers, 'Observer')()\n",
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_observer_construction"]


def test_regression_unowned_observer_construction_fails_closed(tmp_path: Path) -> None:
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


def test_regression_direct_import_subprocess_alias_fails_closed(tmp_path: Path) -> None:
    module_rel = "feature/invoke.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from subprocess import run as launch\ndef invoke() -> None:\n    launch(['tool'])\n",
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert len(violations) == 1
    assert violations[0].kind == "raw_subprocess_invocation"
    assert "typed process" in violations[0].message


def test_local_reasoned_marker_no_longer_silences_a_finding(tmp_path: Path) -> None:
    module_rel = "feature/poller.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import time\n"
        "def poll() -> None:\n"
        "    time.sleep(1)  # filesystem-poll-ok: external protocol backoff\n",
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_sleep_poll"], (
        "an inline marker comment MUST NOT silence the polling audit"
    )


def test_registered_call_site_is_exempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module_rel = "feature/poller.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import time\ndef poll() -> None:\n    time.sleep(1)\n",
    )
    monkeypatch.setattr(
        audit,
        "EXEMPT_CALL_SITES",
        frozenset({("feature/poller.py", "poll", "raw_sleep_poll")}),
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert violations == []


def test_registered_scope_does_not_exempt_another_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_rel = "feature/poller.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import time\ndef poll() -> None:\n    time.sleep(1)\n",
    )
    monkeypatch.setattr(
        audit,
        "EXEMPT_CALL_SITES",
        frozenset({("feature/poller.py", "other", "raw_sleep_poll")}),
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_sleep_poll"]


def test_registered_kind_does_not_exempt_another_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_rel = "feature/poller.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import time\ndef poll() -> None:\n    time.sleep(1)\n",
    )
    monkeypatch.setattr(
        audit,
        "EXEMPT_CALL_SITES",
        frozenset({("feature/poller.py", "poll", "raw_subprocess_invocation")}),
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_sleep_poll"]


def test_regression_dynamic_subprocess_launcher_fails_closed(tmp_path: Path) -> None:
    module_rel = "feature/dynamic_invoke.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import subprocess\ndef invoke() -> None:\n    getattr(subprocess, 'run')(['tool'])\n",
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_subprocess_invocation"]
