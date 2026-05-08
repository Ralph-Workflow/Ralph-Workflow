"""Tests for the installation/update workflow."""

from __future__ import annotations

import builtins
import importlib.util
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from ralph import install as install_module

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


_LEGACY_GLOBAL_CONFIG = """# Ralph Unified Configuration File
[general]
verbosity = 2
interactive = true
isolation_mode = true
auto_detect_stack = true
checkpoint_enabled = true
developer_iters = 5
reviewer_reviews = 2
developer_context = 1
reviewer_context = 0
review_depth = \"standard\"
strict_validation = false

[ccs]
output_flag = \"--output-format=stream-json\"
verbose_flag = \"--verbose\"
print_flag = \"--print\"
session_flag = \"--resume {}\"
yolo_flag = \"--dangerously-skip-permissions\"
json_parser = \"claude\"
can_commit = true

[agent_chains]
developer = [\"claude\", \"codex\", \"opencode\"]
reviewer = [\"codex\", \"claude\"]

[agent_drains]
planning = \"developer\"
development = \"developer\"
analysis = \"developer\"
review = \"reviewer\"
fix = \"reviewer\"
commit = \"reviewer\"

[agent_chain]
max_retries = 3
retry_delay_ms = 1000
"""


def test_install_current_checkout_runs_pip_and_pipx(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[tuple[Sequence[str], Path]] = []

    def fake_run(command: Sequence[str], *, cwd: Path) -> None:
        commands.append((tuple(command), cwd))

    package_dir = Path("/tmp/ralph-workflow")

    install_module.install_current_checkout(
        package_dir=package_dir,
        run=fake_run,
        python_executable="/usr/bin/python3",
        pipx_executable="/usr/local/bin/pipx",
    )

    assert commands == [
        (("/usr/bin/python3", "-m", "pip", "install", "-e", ".[dev]"), package_dir),
        (
            (
                "/usr/local/bin/pipx",
                "install",
                "--force",
                "--editable",
                str(package_dir),
            ),
            package_dir,
        ),
    ]


def test_install_current_checkout_skips_pipx_when_not_available() -> None:
    commands: list[tuple[Sequence[str], Path]] = []

    def fake_run(command: Sequence[str], *, cwd: Path) -> None:
        commands.append((tuple(command), cwd))

    package_dir = Path("/tmp/ralph-workflow")

    install_module.install_current_checkout(
        package_dir=package_dir,
        run=fake_run,
        python_executable=sys.executable,
        pipx_executable=None,
    )

    assert commands == [
        ((sys.executable, "-m", "pip", "install", "-e", ".[dev]"), package_dir),
    ]


def test_install_module_imports_without_process_manager_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_path = Path(__file__).resolve().parents[1] / "ralph" / "install.py"
    original_import = builtins.__import__

    def fail_on_missing_psutil(
        name: str,
        globals_dict: dict[str, object] | None = None,
        locals_dict: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name == "psutil":
            raise ModuleNotFoundError(f"No module named {name}")
        return original_import(name, globals_dict, locals_dict, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_on_missing_psutil)

    spec = importlib.util.spec_from_file_location("bootstrap_safe_install_module", module_path)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    loaded_module = cast("Any", module)

    assert callable(loaded_module.install_current_checkout)
    assert callable(loaded_module.main)



def test_main_uses_repo_directory_and_path_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_install_current_checkout(
        *,
        package_dir: Path,
        run: object,
        python_executable: str,
        pipx_executable: str | None,
    ) -> None:
        captured["package_dir"] = package_dir
        captured["python_executable"] = python_executable
        captured["pipx_executable"] = pipx_executable

    monkeypatch.setattr(install_module, "install_current_checkout", fake_install_current_checkout)
    monkeypatch.setattr(install_module.shutil, "which", lambda name: f"/opt/bin/{name}")

    assert install_module.main() == 0
    assert captured == {
        "package_dir": Path(install_module.__file__).resolve().parents[1],
        "python_executable": sys.executable,
        "pipx_executable": "/opt/bin/pipx",
    }



def _run_subprocess(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=None if env is None else dict(env),
        text=True,
        capture_output=True,
        check=False,
    )



def _build_wheel(repo_root: Path) -> Path:
    build = _run_subprocess(("uv", "run", "hatch", "build", "--target", "wheel"), cwd=repo_root)
    assert build.returncode == 0, build.stderr or build.stdout
    wheels = sorted((repo_root / "dist").glob("ralph_workflow-*.whl"))
    assert wheels, "Expected hatch build to produce a wheel in dist/"
    return wheels[-1]



def test_built_wheel_includes_policy_default_tomls(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    wheel_path = _build_wheel(repo_root)

    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())

    expected = {
        "ralph/policy/defaults/agents.toml",
        "ralph/policy/defaults/artifacts.toml",
        "ralph/policy/defaults/mcp.toml",
        "ralph/policy/defaults/pipeline.toml",
        "ralph/policy/defaults/ralph-workflow-local.toml",
        "ralph/policy/defaults/ralph-workflow.toml",
    }
    missing = expected - names
    assert not missing, f"Built wheel is missing bundled defaults: {sorted(missing)}"



@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(30)
def test_installed_wheel_plain_ralph_bootstraps_without_unbound_drain_failure() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    wheel_path = _build_wheel(repo_root)

    with tempfile.TemporaryDirectory(prefix="ralph-installed-wheel-") as tmp_dir:
        root = Path(tmp_dir)
        venv = root / "venv"
        project = root / "project"
        xdg = root / "xdg"
        home = root / "home"
        project.mkdir()
        xdg.mkdir()
        home.mkdir()

        create_venv = _run_subprocess(
            (sys.executable, "-m", "venv", str(venv)),
            cwd=repo_root,
        )
        assert create_venv.returncode == 0, create_venv.stderr or create_venv.stdout

        python_bin = venv / "bin" / "python"
        install = _run_subprocess(
            (str(python_bin), "-m", "pip", "install", str(wheel_path)),
            cwd=repo_root,
        )
        assert install.returncode == 0, install.stderr or install.stdout

        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = str(xdg)
        env["HOME"] = str(home)

        plain = _run_subprocess((str(python_bin), "-m", "ralph"), cwd=project, env=env)
        assert plain.returncode == 2, plain.stderr or plain.stdout  # noqa: PLR2004
        assert "not initialized" in plain.stdout.lower(), plain.stdout
        assert "Preflight error:" not in plain.stdout, plain.stdout
        assert "unbound drains" not in plain.stdout, plain.stdout
        assert "unbound drains" not in plain.stderr, plain.stderr

        load = _run_subprocess(
            (
                str(python_bin),
                "-c",
                "from pathlib import Path; "
                "from ralph.config.loader import load_config; "
                "from ralph.policy.loader import load_policy; "
                "from ralph.workspace.scope import WorkspaceScope; "
                "scope = WorkspaceScope(Path.cwd()); "
                "cfg = load_config(workspace_scope=scope); "
                "bundle = load_policy(Path.cwd() / '.agent', config=cfg); "
                "print(sorted(bundle.agents.agent_drains))",
            ),
            cwd=project,
            env=env,
        )
        assert load.returncode == 0, load.stderr or load.stdout
        for drain in (
            "planning",
            "planning_analysis",
            "development",
            "development_analysis",
            "development_commit",
        ):
            assert drain in load.stdout, load.stdout


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(30)
def test_installed_wheel_migrates_legacy_global_config_before_plain_ralph() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    wheel_path = _build_wheel(repo_root)

    with tempfile.TemporaryDirectory(prefix="ralph-installed-wheel-legacy-") as tmp_dir:
        root = Path(tmp_dir)
        venv = root / "venv"
        project = root / "project"
        xdg = root / "xdg"
        home = root / "home"
        project.mkdir()
        xdg.mkdir()
        home.mkdir()

        create_venv = _run_subprocess(
            (sys.executable, "-m", "venv", str(venv)),
            cwd=repo_root,
        )
        assert create_venv.returncode == 0, create_venv.stderr or create_venv.stdout

        python_bin = venv / "bin" / "python"
        install = _run_subprocess(
            (str(python_bin), "-m", "pip", "install", str(wheel_path)),
            cwd=repo_root,
        )
        assert install.returncode == 0, install.stderr or install.stdout

        config_path = xdg / "ralph-workflow.toml"
        config_path.write_text(_LEGACY_GLOBAL_CONFIG, encoding="utf-8")

        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = str(xdg)
        env["HOME"] = str(home)

        plain = _run_subprocess((str(python_bin), "-m", "ralph"), cwd=project, env=env)
        assert plain.returncode == 2, plain.stderr or plain.stdout  # noqa: PLR2004
        assert "not initialized" in plain.stdout.lower(), plain.stdout
        assert "unbound drains" not in plain.stdout, plain.stdout
        assert "unbound drains" not in plain.stderr, plain.stderr

        migrated = config_path.read_text(encoding="utf-8")
        for line in (
            'planning_analysis = "developer"',
            'development_analysis = "developer"',
            'development_commit = "reviewer"',
        ):
            assert line in migrated, migrated
