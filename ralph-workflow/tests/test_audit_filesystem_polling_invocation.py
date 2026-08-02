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


def test_regression_missing_default_production_root_fails_closed(tmp_path: Path) -> None:
    """S-6: a missing default root cannot make lifecycle enforcement silently pass."""
    violations = audit.audit_filesystem_polling_invocation(tmp_path)

    assert len(violations) == 1
    assert violations[0].kind == "missing_production_root"
    assert violations[0].file_path == "ralph"
    assert "fail closed" in violations[0].message


def test_regression_explicit_module_path_cannot_escape_package_root(tmp_path: Path) -> None:
    """S-6: explicit candidates outside production root must fail closed."""
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


def test_regression_exempt_suffix_cannot_skip_unrelated_module(tmp_path: Path) -> None:
    """S-6: canonical exemptions cannot silently allow similarly named modules."""
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
    """S-6: D3 markers must be local comments, not text inside a payload."""
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
    """S-6: undecodable new source cannot bypass lifecycle ownership enforcement."""
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


def test_regression_asyncio_sleep_polling_fails_closed(tmp_path: Path) -> None:
    """S-6: async timer polls are governed by the same P3 lifecycle boundary."""
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


def test_regression_root_watchdog_package_observer_construction_fails_closed(tmp_path: Path) -> None:
    """S-1/S-6: root watchdog imports cannot hide a second recursive watch."""
    module_rel = "feature/watch.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import watchdog\n"
        "def start() -> object:\n"
        "    return watchdog.observers.Observer()\n",
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_observer_construction"]


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


def test_regression_direct_import_subprocess_alias_fails_closed(tmp_path: Path) -> None:
    """S-6: a directly imported subprocess launcher alias cannot evade D1."""
    module_rel = "feature/invoke.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "from subprocess import run as launch\n"
        "def invoke() -> None:\n"
        "    launch(['tool'])\n",
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


def test_regression_dynamic_subprocess_launcher_fails_closed(tmp_path: Path) -> None:
    """S-6: a statically named dynamic launcher cannot evade typed ownership."""
    module_rel = "feature/dynamic_invoke.py"
    package_root = _write_fake_package(
        tmp_path,
        module_rel,
        "import subprocess\n"
        "def invoke() -> None:\n"
        "    getattr(subprocess, 'run')(['tool'])\n",
    )

    violations = audit.audit_filesystem_polling_invocation(
        package_root,
        module_paths=(module_rel,),
    )

    assert [violation.kind for violation in violations] == ["raw_subprocess_invocation"]
