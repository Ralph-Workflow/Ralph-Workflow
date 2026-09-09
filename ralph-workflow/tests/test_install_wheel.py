from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_LEGACY_GLOBAL_CONFIG = (
    "# Ralph Unified Configuration File\n[general]\nverbosity = 2\ninteractive = true\n"
    "isolation_mode = true\nauto_detect_stack = true\ncheckpoint_enabled = true\n"
    "developer_iters = 5\nreviewer_reviews = 2\ndeveloper_context = 1\n"
    'reviewer_context = 0\nreview_depth = "standard"\nstrict_validation = false\n\n'
    '[ccs]\noutput_flag = "--output-format=stream-json"\nverbose_flag = "--verbose"\n'
    'print_flag = "--print"\nsession_flag = "--resume {}"\n'
    'yolo_flag = "--dangerously-skip-permissions"\njson_parser = "claude"\n'
    '[agent_chains]\ndeveloper = ["claude", "codex", "opencode"]\n'
    'reviewer = ["codex", "claude"]\n\n[agent_drains]\nplanning = "developer"\n'
    'development = "developer"\nanalysis = "developer"\nreview = "reviewer"\n'
    'fix = "reviewer"\ncommit = "reviewer"\n\n[agent_chain]\nmax_retries = 3\n'
    "retry_delay_ms = 1000\n"
)

_PLAIN_RALPH_BOOTSTRAP_SCRIPT = (
    "import subprocess\nimport sys\nfrom pathlib import Path\n\n"
    "from ralph.config.loader import load_config\nfrom ralph.policy.loader import load_policy\n"
    "from ralph.workspace.scope import WorkspaceScope\n\n"
    'plain = subprocess.run([sys.executable, "-m", "ralph"], capture_output=True, text=True, check=False)\n'
    'print(f"PLAIN_RC={plain.returncode}")\nprint("---PLAIN_STDOUT---")\nprint(plain.stdout, end="")\n'
    'print("---PLAIN_STDERR---")\nprint(plain.stderr, end="")\n'
    "scope = WorkspaceScope(Path.cwd())\ncfg = load_config(workspace_scope=scope)\n"
    'bundle = load_policy(Path.cwd() / ".agent", config=cfg)\nprint("---DRAINS---")\n'
    "print(sorted(bundle.agents.agent_drains))\n"
)


def _run_subprocess(
    command: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=None if env is None else dict(env),
        text=True,
        capture_output=True,
        check=False,
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _build_wheel(repo_root: Path) -> Path:
    wheels = sorted((repo_root / "dist").glob("ralph_workflow-*.whl"))
    if wheels:
        return wheels[-1]
    build = _run_subprocess(
        ("uv", "run", "--with", "hatchling", "hatch", "build", "-t", "wheel"), cwd=repo_root
    )
    assert build.returncode == 0, build.stderr or build.stdout
    wheels = sorted((repo_root / "dist").glob("ralph_workflow-*.whl"))
    assert wheels
    return wheels[-1]


@pytest.fixture(scope="session")
def built_wheel_path() -> Path:
    return _build_wheel(_repo_root())


@pytest.fixture(scope="session")
def installed_wheel_python(
    tmp_path_factory: pytest.TempPathFactory, built_wheel_path: Path
) -> Path:
    del tmp_path_factory
    cache_root = _repo_root() / "tmp" / "installed-wheel-cache" / built_wheel_path.stem
    launcher = cache_root / "bin" / "python"
    wheel_hash = hashlib.sha256(built_wheel_path.read_bytes()).hexdigest()[:16]
    hash_marker = cache_root / ".wheel-content-hash"
    cached_hash = hash_marker.read_text(encoding="utf-8").strip() if hash_marker.exists() else ""
    if launcher.exists() and cached_hash == wheel_hash:
        return launcher
    if cache_root.exists():
        shutil.rmtree(cache_root)
    site_packages = cache_root / "site-packages"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    site_packages.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(built_wheel_path) as wheel:
        wheel.extractall(site_packages)
    launcher.write_text(
        "#!/usr/bin/env bash\n"
        f'export PYTHONPATH="{site_packages}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    hash_marker.write_text(wheel_hash, encoding="utf-8")
    return launcher


def _assert_plain_bootstrap(combined: subprocess.CompletedProcess[str]) -> None:
    assert combined.returncode == 0, combined.stderr or combined.stdout
    assert "PLAIN_RC=2" in combined.stdout
    assert "unbound drains" not in combined.stdout


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(30)
def test_built_wheel_includes_policy_default_tomls(built_wheel_path: Path) -> None:
    with zipfile.ZipFile(built_wheel_path) as wheel:
        names = set(wheel.namelist())
    assert {
        "ralph/policy/defaults/agents.toml",
        "ralph/policy/defaults/artifacts.toml",
        "ralph/policy/defaults/mcp.toml",
        "ralph/policy/defaults/pipeline.toml",
        "ralph/policy/defaults/ralph-workflow-local.toml",
        "ralph/policy/defaults/ralph-workflow.toml",
    } <= names


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(30)
def test_installed_wheel_plain_ralph_bootstraps_without_unbound_drain_failure(
    tmp_path: Path, installed_wheel_python: Path
) -> None:
    project, xdg, home = tmp_path / "project", tmp_path / "xdg", tmp_path / "home"
    project.mkdir()
    xdg.mkdir()
    home.mkdir()
    environment = {**os.environ, "XDG_CONFIG_HOME": str(xdg), "HOME": str(home)}
    _assert_plain_bootstrap(
        _run_subprocess(
            (str(installed_wheel_python), "-c", _PLAIN_RALPH_BOOTSTRAP_SCRIPT),
            cwd=project,
            env=environment,
        )
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(30)
def test_installed_wheel_migrates_legacy_global_config_before_plain_ralph(
    tmp_path: Path, installed_wheel_python: Path
) -> None:
    project, xdg, home = tmp_path / "project", tmp_path / "xdg", tmp_path / "home"
    project.mkdir()
    xdg.mkdir()
    home.mkdir()
    config_path = xdg / "ralph-workflow.toml"
    config_path.write_text(_LEGACY_GLOBAL_CONFIG, encoding="utf-8")
    environment = {**os.environ, "XDG_CONFIG_HOME": str(xdg), "HOME": str(home)}
    _assert_plain_bootstrap(
        _run_subprocess(
            (str(installed_wheel_python), "-c", _PLAIN_RALPH_BOOTSTRAP_SCRIPT),
            cwd=project,
            env=environment,
        )
    )
    migrated = config_path.read_text(encoding="utf-8")
    assert 'planning_analysis = "developer"' in migrated
