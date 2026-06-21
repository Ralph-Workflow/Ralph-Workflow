"""Tests for the installation/update workflow."""

from __future__ import annotations

import builtins
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
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


def test_install_dev_checkout_syncs_env_and_writes_rdev_launcher() -> None:
    commands: list[tuple[Sequence[str], Path]] = []
    launchers: list[tuple[Path, str]] = []

    def fake_run(command: Sequence[str], *, cwd: Path) -> None:
        commands.append((tuple(command), cwd))

    def fake_write_launcher(path: Path, content: str) -> None:
        launchers.append((path, content))

    package_dir = Path("/tmp/ralph-workflow")
    bin_dir = Path("/home/u/.local/bin")

    install_module.install_dev_checkout(
        run=fake_run,
        uv_executable="/usr/local/bin/uv",
        cwd=package_dir,
        launcher_dir=bin_dir,
        write_launcher=fake_write_launcher,
    )

    # The dev build syncs the project's own uv environment (editable project +
    # dev extras), then writes an `rdev` launcher so the dev build has a stable
    # command name that never shadows the stable `ralph`.
    assert commands == [
        (("/usr/local/bin/uv", "sync", "--extra", "dev"), package_dir),
    ]
    assert len(launchers) == 1
    launcher_path, content = launchers[0]
    assert launcher_path == bin_dir / "rdev"
    assert "uv run --project" in content
    assert str(package_dir) in content
    assert content.endswith('ralph "$@"\n')


def test_install_dev_checkout_requires_uv() -> None:
    commands: list[tuple[Sequence[str], Path]] = []
    launchers: list[tuple[Path, str]] = []

    def fake_run(command: Sequence[str], *, cwd: Path) -> None:
        commands.append((tuple(command), cwd))

    def fake_write_launcher(path: Path, content: str) -> None:
        launchers.append((path, content))

    with pytest.raises(RuntimeError, match="uv"):
        install_module.install_dev_checkout(
            run=fake_run,
            uv_executable=None,
            cwd=Path("/tmp/ralph-workflow"),
            launcher_dir=Path("/home/u/.local/bin"),
            write_launcher=fake_write_launcher,
        )

    assert commands == []
    assert launchers == []


def test_install_stable_release_installs_pinned_global_via_uv_tool() -> None:
    commands: list[tuple[Sequence[str], Path]] = []

    def fake_run(command: Sequence[str], *, cwd: Path) -> None:
        commands.append((tuple(command), cwd))

    package_dir = Path("/tmp/ralph-workflow")

    install_module.install_stable_release(
        run=fake_run,
        uv_executable="/usr/local/bin/uv",
        cwd=package_dir,
    )

    # No version pin -> install/upgrade to the latest published release.
    # --upgrade implies --refresh so an already-installed older `ralph` is bumped.
    assert commands == [
        (
            ("/usr/local/bin/uv", "tool", "install", "--force", "--upgrade", "ralph-workflow"),
            package_dir,
        ),
    ]


def test_install_stable_release_pins_requested_version() -> None:
    commands: list[tuple[Sequence[str], Path]] = []

    def fake_run(command: Sequence[str], *, cwd: Path) -> None:
        commands.append((tuple(command), cwd))

    package_dir = Path("/tmp/ralph-workflow")

    install_module.install_stable_release(
        run=fake_run,
        uv_executable="/usr/local/bin/uv",
        cwd=package_dir,
        version="1.2.3",
    )

    assert commands == [
        (
            ("/usr/local/bin/uv", "tool", "install", "--force", "ralph-workflow==1.2.3"),
            package_dir,
        ),
    ]


def test_install_stable_release_requires_uv() -> None:
    commands: list[tuple[Sequence[str], Path]] = []

    def fake_run(command: Sequence[str], *, cwd: Path) -> None:
        commands.append((tuple(command), cwd))

    with pytest.raises(RuntimeError, match="uv"):
        install_module.install_stable_release(
            run=fake_run,
            uv_executable=None,
            cwd=Path("/tmp/ralph-workflow"),
        )

    assert commands == []


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
    ) -> object:
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

    assert callable(loaded_module.install_dev_checkout)
    assert callable(loaded_module.install_stable_release)
    assert callable(loaded_module.main)


def test_main_default_installs_dev_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_install_dev_checkout(
        *,
        run: object,
        uv_executable: str | None,
        cwd: Path,
        launcher_dir: Path,
    ) -> None:
        captured["uv_executable"] = uv_executable
        captured["cwd"] = cwd
        captured["launcher_dir"] = launcher_dir

    def fail_stable(**_kwargs: object) -> None:
        raise AssertionError("default install must not touch the stable release")

    monkeypatch.setattr(install_module, "install_dev_checkout", fake_install_dev_checkout)
    monkeypatch.setattr(install_module, "install_stable_release", fail_stable)
    monkeypatch.setattr(install_module.shutil, "which", lambda name: f"/opt/bin/{name}")
    monkeypatch.setattr(install_module.Path, "home", classmethod(lambda _cls: Path("/home/u")))

    assert install_module.main([]) == 0
    assert captured == {
        "uv_executable": "/opt/bin/uv",
        "cwd": Path(install_module.__file__).resolve().parents[1],
        "launcher_dir": Path("/home/u/.local/bin"),
    }


def test_render_dev_launcher_runs_checkout_via_uv() -> None:
    package_dir = Path("/tmp/ralph-workflow")
    content = install_module.render_dev_launcher(package_dir)

    assert content.startswith("#!/usr/bin/env bash\n")
    assert f'exec uv run --project "{package_dir}" ralph "$@"\n' in content


def test_main_stable_flag_installs_pinned_release(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_install_stable_release(
        *,
        run: object,
        uv_executable: str | None,
        cwd: Path,
        version: str | None,
    ) -> None:
        captured["uv_executable"] = uv_executable
        captured["cwd"] = cwd
        captured["version"] = version

    def fail_dev(**_kwargs: object) -> None:
        raise AssertionError("stable install must not touch the dev checkout")

    monkeypatch.setattr(install_module, "install_stable_release", fake_install_stable_release)
    monkeypatch.setattr(install_module, "install_dev_checkout", fail_dev)
    monkeypatch.setattr(install_module.shutil, "which", lambda name: f"/opt/bin/{name}")

    assert install_module.main(["--stable"]) == 0
    assert captured == {
        "uv_executable": "/opt/bin/uv",
        "cwd": Path(install_module.__file__).resolve().parents[1],
        "version": None,
    }


def test_main_version_implies_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_install_stable_release(
        *,
        run: object,
        uv_executable: str | None,
        cwd: Path,
        version: str | None,
    ) -> None:
        captured["version"] = version

    monkeypatch.setattr(install_module, "install_stable_release", fake_install_stable_release)
    monkeypatch.setattr(install_module.shutil, "which", lambda name: f"/opt/bin/{name}")

    assert install_module.main(["--version", "9.9.9"]) == 0
    assert captured == {"version": "9.9.9"}


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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _build_wheel(repo_root: Path) -> Path:
    wheels = sorted((repo_root / "dist").glob("ralph_workflow-*.whl"))
    if wheels:
        return wheels[-1]

    build = _run_subprocess(("uv", "run", "hatch", "build", "--target", "wheel"), cwd=repo_root)
    assert build.returncode == 0, build.stderr or build.stdout
    wheels = sorted((repo_root / "dist").glob("ralph_workflow-*.whl"))
    assert wheels, "Expected hatch build to produce a wheel in dist/"
    return wheels[-1]


@pytest.fixture(scope="session")
def built_wheel_path() -> Path:
    """Build one wheel per test session for subprocess installation smoke tests."""
    return _build_wheel(_repo_root())


@pytest.fixture(scope="session")
def installed_wheel_python(
    tmp_path_factory: pytest.TempPathFactory,
    built_wheel_path: Path,
) -> Path:
    """Create one installed virtualenv per test session for wheel bootstrapping tests."""
    del tmp_path_factory
    cache_root = _repo_root() / "tmp" / "installed-wheel-cache" / built_wheel_path.stem
    launcher = cache_root / "bin" / "python"

    # Invalidate cache when wheel content changes (not just stem/version).
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


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(30)
def test_built_wheel_includes_policy_default_tomls(built_wheel_path: Path) -> None:
    wheel_path = built_wheel_path

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
@pytest.mark.timeout_seconds(10)
def test_write_dev_launcher_creates_executable_script(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "bin" / "rdev"
    content = '#!/usr/bin/env bash\nexec uv run --project /tmp/ralph ralph "$@"\n'

    install_module.write_dev_launcher(target, content)

    assert target.read_text(encoding="utf-8") == content
    assert os.access(target, os.X_OK), "launcher must be executable"


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(30)
def test_installed_wheel_plain_ralph_bootstraps_without_unbound_drain_failure(
    tmp_path: Path,
    installed_wheel_python: Path,
) -> None:
    project = tmp_path / "project"
    xdg = tmp_path / "xdg"
    home = tmp_path / "home"
    project.mkdir()
    xdg.mkdir()
    home.mkdir()

    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(xdg)
    env["HOME"] = str(home)

    plain = _run_subprocess((str(installed_wheel_python), "-m", "ralph"), cwd=project, env=env)
    assert plain.returncode == 2, plain.stderr or plain.stdout
    assert "not initialized" in plain.stdout.lower(), plain.stdout
    assert "Preflight error:" not in plain.stdout, plain.stdout
    assert "unbound drains" not in plain.stdout, plain.stdout
    assert "unbound drains" not in plain.stderr, plain.stderr

    load = _run_subprocess(
        (
            str(installed_wheel_python),
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
def test_installed_wheel_migrates_legacy_global_config_before_plain_ralph(
    tmp_path: Path,
    installed_wheel_python: Path,
) -> None:
    project = tmp_path / "project"
    xdg = tmp_path / "xdg"
    home = tmp_path / "home"
    project.mkdir()
    xdg.mkdir()
    home.mkdir()

    config_path = xdg / "ralph-workflow.toml"
    config_path.write_text(_LEGACY_GLOBAL_CONFIG, encoding="utf-8")

    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(xdg)
    env["HOME"] = str(home)

    plain = _run_subprocess((str(installed_wheel_python), "-m", "ralph"), cwd=project, env=env)
    assert plain.returncode == 2, plain.stderr or plain.stdout
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
