from __future__ import annotations

from contextlib import nullcontext
from importlib import import_module
from pathlib import Path

from ralph import install as install_module

_build_meta = import_module("ralph._build_meta")


def test_install_dev_checkout_stamps_snapshot_provenance() -> None:
    written: dict[str, str] = {}

    def write_flavor(
        _path: Path,
        _flavor: str,
        *,
        source_commit: str = "",
        source_path: str = "",
        installed_at: str = "",
    ) -> None:
        del source_path
        written.update(source_commit=source_commit, installed_at=installed_at)

    install_module.install_dev_checkout(
        run=lambda _command, *, cwd: None,
        uv_executable="uv",
        cwd=Path("/checkout"),
        launcher_dir=Path("/bin"),
        install_root=Path("/install"),
        copy_tree=lambda _source, destination: destination,
        write_flavor=write_flavor,
        resolve_commit=lambda _source: "abc123",
        installed_at=lambda: "2026-08-02T12:00:00+00:00",
        write_launcher=lambda _path, _content: None,
        preflight=lambda _uv: None,
        run_version=lambda _uv, _snapshot: "0.9.27-dev",
        lock=lambda _path: nullcontext(),
        candidate_factory=lambda staging: staging / "candidate",
        publish=lambda candidate, _generations, *, publish_launcher: (
            publish_launcher(candidate),
            candidate,
        )[1],
        update_current=lambda _current, _generation: None,
    )
    assert written == {"source_commit": "abc123", "installed_at": "2026-08-02T12:00:00+00:00"}


def test_copy_install_tree_preserves_runtime_assets_and_omits_build_artifacts(
    tmp_path: Path,
) -> None:
    from ralph._install_copy_tree import copy_install_tree

    source, destination = tmp_path / "source", tmp_path / "snapshot"
    (source / "ralph" / "prompts" / "templates").mkdir(parents=True)
    (source / "ralph" / "policy" / "defaults").mkdir(parents=True)
    (source / "ralph" / "prompts" / "templates" / "task.jinja").write_text("task", encoding="utf-8")
    (source / "ralph" / "policy" / "defaults" / "pipeline.toml").write_text("[x]", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".venv").mkdir()
    copy_install_tree(source, destination)
    assert (destination / "ralph" / "prompts" / "templates" / "task.jinja").is_file()
    assert not (destination / ".git").exists()
    assert not (destination / ".venv").exists()


def test_copy_install_tree_omits_source_virtualenv_from_new_candidate(tmp_path: Path) -> None:
    from ralph._install_copy_tree import copy_install_tree

    source, destination = tmp_path / "source", tmp_path / "snapshot"
    (source / ".venv" / "bin").mkdir(parents=True)
    (source / "ralph").mkdir()
    copy_install_tree(source, destination)
    assert not (destination / ".venv").exists()


def test_install_metadata_serializes_hostile_path_and_omits_invalid_commit(tmp_path: Path) -> None:
    from ralph._install_runtime import write_build_flavor

    build_meta = tmp_path / "ralph" / "_build_meta.py"
    build_meta.parent.mkdir()
    build_meta.write_text(
        'BUILD_FLAVOR: str = ""\n'
        'BUILD_SOURCE_COMMIT: str = ""\n'
        'BUILD_SOURCE_PATH: str = ""\n'
        'BUILD_INSTALLED_AT: str = ""\n',
        encoding="utf-8",
    )

    write_build_flavor(
        tmp_path,
        "-dev",
        source_commit="not-a-git-commit; injected",
        source_path='checkout "quoted"\n$(not-shell)',
        installed_at="2026-08-02T12:00:00+00:00",
    )

    namespace: dict[str, str] = {}
    exec(build_meta.read_text(encoding="utf-8"), namespace)
    assert namespace["BUILD_SOURCE_COMMIT"] == ""
    assert namespace["BUILD_SOURCE_PATH"] == 'checkout "quoted"\n$(not-shell)'
