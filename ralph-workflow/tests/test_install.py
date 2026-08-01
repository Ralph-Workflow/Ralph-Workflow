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
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph import install as install_module

_build_meta = import_module("ralph._build_meta")

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
    flavors: list[tuple[Path, str]] = []

    def fake_copy(source: Path, destination: Path) -> Path:
        assert source == package_dir
        return destination

    def fake_flavor(path: Path, flavor: str) -> None:
        flavors.append((path, flavor))

    install_module.install_dev_checkout(
        run=fake_run,
        uv_executable="/usr/local/bin/uv",
        cwd=package_dir,
        launcher_dir=bin_dir,
        install_root=Path("/install"),
        copy_tree=fake_copy,
        write_flavor=fake_flavor,
        write_launcher=fake_write_launcher,
    )

    # The dev build syncs the project's own uv environment (editable project +
    # dev extras), then writes an `rdev` launcher so the dev build has a stable
    # command name that never shadows the stable `ralph`.
    assert commands == [
        (("/usr/local/bin/uv", "sync", "--extra", "dev"), Path("/install/current")),
    ]
    assert len(launchers) == 1
    launcher_path, content = launchers[0]
    assert launcher_path == bin_dir / "rdev"
    assert "uv run --project" in content
    assert str(Path("/install/current")) in content
    assert content.endswith('ralph "$@"\n')
    assert flavors == [(Path("/install/current"), "-dev")]


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


def test_install_stable_release_marks_local_wheel_as_manual_build() -> None:
    commands: list[tuple[Sequence[str], Path]] = []
    flavors: list[tuple[Path, str]] = []

    def fake_run(command: Sequence[str], *, cwd: Path) -> None:
        commands.append((tuple(command), cwd))

    install_module.install_stable_release(
        run=fake_run,
        uv_executable="/usr/local/bin/uv",
        cwd=Path("/tmp/ralph-workflow"),
        from_path=Path("/tmp/ralph-workflow/dist/ralph.whl"),
        which_fn=lambda _name: "/home/u/.local/bin/ralph",
        resolve_installed_package_file=lambda _exe: Path(
            "/home/u/.local/share/uv/tools/ralph-workflow/lib/python3.14/site-packages/ralph/__init__.py"
        ),
        write_flavor=lambda path, flavor: flavors.append((path, flavor)),
    )

    assert commands == [
        (
            (
                "/usr/local/bin/uv",
                "tool",
                "install",
                "--force",
                "/tmp/ralph-workflow/dist/ralph.whl",
            ),
            Path("/tmp/ralph-workflow"),
        ),
    ]
    assert flavors == [
        (
            Path("/home/u/.local/share/uv/tools/ralph-workflow/lib/python3.14/site-packages"),
            "-build",
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
    loaded_module = module

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
        flavor: str,
    ) -> None:
        captured["uv_executable"] = uv_executable
        captured["cwd"] = cwd
        captured["launcher_dir"] = launcher_dir
        captured["flavor"] = flavor

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
        "flavor": "-dev",
    }


def test_main_build_regression_writes_build_flavor_without_global_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_install_dev_checkout(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(install_module, "install_dev_checkout", fake_install_dev_checkout)
    monkeypatch.setattr(install_module, "install_stable_release", lambda **_kwargs: None)
    monkeypatch.setattr(install_module.shutil, "which", lambda name: f"/opt/bin/{name}")
    monkeypatch.setattr(install_module.Path, "home", classmethod(lambda _cls: Path("/home/u")))

    assert install_module.main(["--build"]) == 0
    assert captured["flavor"] == "-build"
    assert captured["launcher_dir"] == Path("/home/u/.local/bin")


def test_main_default_install_preflights_existing_global_ralph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2: `make install` checks an existing global Ralph before installing."""
    preflight_calls: list[object] = []
    monkeypatch.setattr(
        install_module,
        "_resolve_install_conflict",
        lambda **kwargs: preflight_calls.append(kwargs["run"]),
    )
    monkeypatch.setattr(install_module, "install_dev_checkout", lambda **_kwargs: None)
    monkeypatch.setattr(install_module.shutil, "which", lambda _name: "/opt/bin/uv")

    assert install_module.main([]) == 0
    assert preflight_calls == [install_module._run_command]


def test_flavored_version_reports_build_and_dev_suffixes(monkeypatch: pytest.MonkeyPatch) -> None:
    """S-2: installer flavor suffixes appear in the public version."""
    assert _build_meta.flavored_version() == _build_meta._BASE_VERSION

    for flavor in ("-build", "-dev"):
        monkeypatch.setattr(_build_meta, "BUILD_FLAVOR", flavor)
        assert _build_meta.flavored_version() == _build_meta._BASE_VERSION + flavor


def test_write_build_flavor_changes_only_the_runtime_suffix(tmp_path: Path) -> None:
    package_dir = tmp_path / "snapshot"
    build_meta = package_dir / "ralph" / "_build_meta.py"
    build_meta.parent.mkdir(parents=True)
    build_meta.write_text('BUILD_FLAVOR: str = ""\n', encoding="utf-8")

    install_module._write_build_flavor(package_dir, "-dev")

    assert build_meta.read_text(encoding="utf-8") == 'BUILD_FLAVOR: str = "-dev"\n'


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
        **_kwargs: object,
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


def test_main_stable_install_preflights_global_ralph(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stable installation owns `ralph`, so it keeps the global conflict guard."""
    preflight_calls: list[object] = []
    monkeypatch.setattr(
        install_module,
        "_resolve_install_conflict",
        lambda **kwargs: preflight_calls.append(kwargs["run"]),
    )
    monkeypatch.setattr(install_module, "install_stable_release", lambda **_kwargs: None)
    monkeypatch.setattr(install_module.shutil, "which", lambda _name: "/opt/bin/uv")

    assert install_module.main(["--stable"]) == 0
    assert preflight_calls == [install_module._run_command]


def test_main_version_implies_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_install_stable_release(
        *,
        run: object,
        uv_executable: str | None,
        cwd: Path,
        version: str | None,
        **_kwargs: object,
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


_PLAIN_RALPH_BOOTSTRAP_SCRIPT = """\
import subprocess
import sys
from pathlib import Path

from ralph.config.loader import load_config
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

plain = subprocess.run(
    [sys.executable, "-m", "ralph"],
    capture_output=True,
    text=True,
    check=False,
)
print(f"PLAIN_RC={plain.returncode}")
print("---PLAIN_STDOUT---")
print(plain.stdout, end="")
print("---PLAIN_STDERR---")
print(plain.stderr, end="")

scope = WorkspaceScope(Path.cwd())
cfg = load_config(workspace_scope=scope)
bundle = load_policy(Path.cwd() / ".agent", config=cfg)
print("---DRAINS---")
print(sorted(bundle.agents.agent_drains))
"""


def _parse_plain_ralph_bootstrap_output(combined: subprocess.CompletedProcess[str]) -> None:
    assert combined.returncode == 0, combined.stderr or combined.stdout
    text = combined.stdout
    rc_line, _, rest = text.partition("\n")
    assert rc_line.startswith("PLAIN_RC="), text
    assert rc_line.removeprefix("PLAIN_RC=") == "2", text
    assert "---PLAIN_STDOUT---" in rest, text
    stdout_part, _, tail = rest.partition("---PLAIN_STDOUT---")
    del stdout_part
    plain_stdout, _, tail = tail.partition("---PLAIN_STDERR---")
    plain_stderr, _, drains_part = tail.partition("---DRAINS---")
    assert "not initialized" in plain_stdout.lower(), plain_stdout
    assert "Preflight error:" not in plain_stdout, plain_stdout
    assert "unbound drains" not in plain_stdout, plain_stdout
    assert "unbound drains" not in plain_stderr, plain_stderr
    for drain in (
        "planning",
        "planning_analysis",
        "development",
        "development_analysis",
        "development_commit",
    ):
        assert drain in drains_part, drains_part


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

    combined = _run_subprocess(
        (str(installed_wheel_python), "-c", _PLAIN_RALPH_BOOTSTRAP_SCRIPT),
        cwd=project,
        env=env,
    )
    _parse_plain_ralph_bootstrap_output(combined)


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

    combined = _run_subprocess(
        (str(installed_wheel_python), "-c", _PLAIN_RALPH_BOOTSTRAP_SCRIPT),
        cwd=project,
        env=env,
    )
    _parse_plain_ralph_bootstrap_output(combined)

    migrated = config_path.read_text(encoding="utf-8")
    for line in (
        'planning_analysis = "developer"',
        'development_analysis = "developer"',
        'development_commit = "reviewer"',
    ):
        assert line in migrated, migrated


def test_copy_install_tree_preserves_runtime_assets_and_omits_build_artifacts(
    tmp_path: Path,
) -> None:
    """S-3: the dev snapshot keeps runtime assets after its source checkout is gone."""
    from ralph._install_copy_tree import copy_install_tree

    source = tmp_path / "source"
    destination = tmp_path / "snapshot"
    (source / "ralph" / "prompts" / "templates").mkdir(parents=True)
    (source / "ralph" / "policy" / "defaults").mkdir(parents=True)
    (source / "ralph" / "prompts" / "templates" / "task.jinja").write_text("task", encoding="utf-8")
    (source / "ralph" / "policy" / "defaults" / "pipeline.toml").write_text("[x]", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("ignored", encoding="utf-8")
    (source / ".venv").mkdir()
    (source / ".venv" / "marker").write_text("ignored", encoding="utf-8")

    assert copy_install_tree(source, destination) == destination
    assert (destination / "ralph" / "prompts" / "templates" / "task.jinja").read_text() == "task"
    assert (destination / "ralph" / "policy" / "defaults" / "pipeline.toml").read_text() == "[x]"
    assert not (destination / ".git").exists()
    assert not (destination / ".venv").exists()


def test_install_stable_release_leaves_published_package_flavor_clean() -> None:
    """S-4: only a local manual wheel receives the build suffix."""
    writes: list[tuple[Path, str]] = []

    install_module.install_stable_release(
        run=lambda _command, *, cwd: None,
        uv_executable="uv",
        cwd=Path("/checkout"),
        write_flavor=lambda path, flavor: writes.append((path, flavor)),
    )

    assert writes == []
