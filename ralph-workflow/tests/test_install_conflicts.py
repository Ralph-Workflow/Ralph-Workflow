"""Regression tests for the installation conflict preflight (S-2)."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from ralph._install_conflicts import (
    ConflictResolution,
    ExistingInstall,
    detect_existing_ralph,
    prompt_for_conflict,
)
from ralph.update_check._install_kind import InstallKind

conflicts = import_module("ralph._install_conflicts")


def test_install_conflict_regression_resolves_console_script_to_pipx_package() -> None:
    existing = detect_existing_ralph(
        which_fn=lambda _name: "/home/u/.local/bin/ralph",
        environ={},
        resolve_package_file=lambda _exe: Path(
            "/home/u/.local/pipx/venvs/ralph-workflow/lib/ralph/__init__.py"
        ),
    )

    assert existing is not None
    assert existing.kind is InstallKind.PIPX
    assert existing.remove_command == ("pipx", "uninstall", "ralph-workflow")


def test_install_conflict_detects_uv_tool_from_resolved_package_path() -> None:
    existing = detect_existing_ralph(
        which_fn=lambda _name: "/home/u/.local/bin/ralph",
        environ={},
        resolve_package_file=lambda _exe: Path(
            "/home/u/.local/share/uv/tools/ralph-workflow/lib/ralph/__init__.py"
        ),
    )

    assert existing is not None
    assert existing.kind is InstallKind.UV_TOOL
    assert existing.remove_command == ("uv", "tool", "uninstall", "ralph-workflow")


def test_install_conflict_source_checkout_only_aborts() -> None:
    existing = ExistingInstall(
        executable=Path("/home/u/bin/ralph"),
        package_file=Path("/work/ralph/ralph/__init__.py"),
        kind=InstallKind.SOURCE,
    )

    assert (
        prompt_for_conflict(existing, input_fn=lambda _prompt: "c", is_tty=True)
        is ConflictResolution.ABORT
    )


def test_install_conflict_prompt_dispatches_continue_remove_and_abort() -> None:
    existing = ExistingInstall(
        executable=Path("/home/u/bin/ralph"),
        package_file=Path("/venv/lib/ralph/__init__.py"),
        kind=InstallKind.PIPX,
    )

    assert (
        prompt_for_conflict(existing, input_fn=lambda _prompt: "c", is_tty=True)
        is ConflictResolution.CONTINUE
    )
    assert (
        prompt_for_conflict(existing, input_fn=lambda _prompt: "r", is_tty=True)
        is ConflictResolution.REMOVE
    )
    assert (
        prompt_for_conflict(existing, input_fn=lambda _prompt: "anything", is_tty=True)
        is ConflictResolution.ABORT
    )


def test_install_conflict_non_tty_aborts_without_prompt() -> None:
    existing = ExistingInstall(
        executable=Path("/home/u/bin/ralph"),
        package_file=Path("/venv/lib/ralph/__init__.py"),
        kind=InstallKind.PIP,
    )

    with pytest.raises(RuntimeError, match="non-interactive"):
        prompt_for_conflict(
            existing, input_fn=lambda _prompt: pytest.fail("must not prompt"), is_tty=False
        )


def test_install_conflict_regression_resolves_env_shebang_interpreter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = tmp_path / "ralph"
    script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    def fake_run(executable: str, arguments: tuple[str, ...], *, options: object) -> object:
        assert executable == "python3"
        assert arguments == ("-c", conflicts._PACKAGE_FILE_SCRIPT)
        assert options is not None
        return SimpleNamespace(succeeded=True, stdout="/venv/site-packages/ralph/__init__.py\n")

    monkeypatch.setattr(conflicts, "run_process", fake_run)

    assert conflicts.resolve_package_file(str(script)) == Path(
        "/venv/site-packages/ralph/__init__.py"
    )


def test_install_conflict_regression_resolves_env_split_string_interpreter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = tmp_path / "ralph"
    script.write_text("#!/usr/bin/env -S python3 -I\n", encoding="utf-8")

    def fake_run(executable: str, arguments: tuple[str, ...], *, options: object) -> object:
        assert executable == "python3"
        assert arguments == ("-c", conflicts._PACKAGE_FILE_SCRIPT)
        assert options is not None
        return SimpleNamespace(succeeded=True, stdout="/venv/site-packages/ralph/__init__.py\n")

    monkeypatch.setattr(conflicts, "run_process", fake_run)

    assert conflicts.resolve_package_file(str(script)) == Path(
        "/venv/site-packages/ralph/__init__.py"
    )


def test_install_conflict_missing_executable_returns_none() -> None:
    assert (
        detect_existing_ralph(
            which_fn=lambda _name: None,
            environ={},
            resolve_package_file=lambda _exe: pytest.fail("must not resolve"),
        )
        is None
    )
