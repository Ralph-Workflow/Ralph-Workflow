from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

import pytest

from ralph import install

pytestmark = [pytest.mark.timeout_seconds(5)]


def _run_launcher_entry(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    seen: dict[str, str] = {}

    def recording_app() -> None:
        seen.update(os.environ)
        seen["argv0"] = sys.argv[0]
        raise SystemExit(0)

    monkeypatch.setattr("ralph.cli.main.app", recording_app)
    monkeypatch.setattr(sys, "argv", ["-c", "--version"])
    launcher = install.render_dev_launcher(Path("/snapshot"), "/tools/uv")
    argv = shlex.split(launcher[launcher.index("exec ") :])
    entry = argv[argv.index("-c") + 1]
    with pytest.raises(SystemExit) as exit_info:
        exec(compile(entry, "<launcher-entry>", "exec"), dict[str, object](__name__="__main__"))
    assert exit_info.value.code == 0
    return seen


def test_launcher_entry_hides_the_runtime_virtualenv_from_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_bin = Path(sys.prefix) / "bin"
    monkeypatch.setenv("VIRTUAL_ENV", sys.prefix)
    monkeypatch.setenv("PATH", os.pathsep.join((str(runtime_bin), "/usr/local/bin", "/usr/bin")))

    seen = _run_launcher_entry(monkeypatch)

    assert "VIRTUAL_ENV" not in seen
    assert seen["PATH"] == os.pathsep.join(("/usr/local/bin", "/usr/bin"))
    assert seen["argv0"] == "ralph"


def test_launcher_entry_keeps_a_virtualenv_that_is_not_the_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_env = tmp_path / "project" / ".venv"
    project_bin = project_env / "bin"
    project_bin.mkdir(parents=True)
    path = os.pathsep.join((str(project_bin), "/usr/bin"))
    monkeypatch.setenv("VIRTUAL_ENV", str(project_env))
    monkeypatch.setenv("PATH", path)

    seen = _run_launcher_entry(monkeypatch)

    assert seen["VIRTUAL_ENV"] == str(project_env)
    assert seen["PATH"] == path
