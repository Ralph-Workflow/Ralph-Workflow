from __future__ import annotations

import fcntl
from pathlib import Path

import pytest

from ralph._install_errors import InstallError
from ralph.install_transaction import (
    InstallLockUnavailableError,
    cleanup_staging,
    finalize_generation,
    install_lock,
    point_current,
    publish_generation,
)


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_finalize_generation_atomically_promotes_a_fully_prepared_candidate(tmp_path: Path) -> None:
    candidate = tmp_path / ".staging" / "candidate"
    generations = tmp_path / "generations"
    _write_tree(candidate, {"ralph/__init__.py": "new", ".venv/bin/python": "new interpreter"})

    generation = finalize_generation(candidate, generations)

    assert generation.parent == generations
    assert (generation / "ralph/__init__.py").read_text(encoding="utf-8") == "new"
    assert (generation / ".venv/bin/python").read_text(encoding="utf-8") == "new interpreter"
    assert not candidate.exists()


def test_cleanup_staging_removes_abandoned_candidates_without_touching_generations(tmp_path: Path) -> None:
    staging = tmp_path / ".staging"
    generations = tmp_path / "generations"
    _write_tree(staging / "abandoned", {"ralph/__init__.py": "candidate"})
    _write_tree(generations / "live", {"ralph/__init__.py": "published", ".venv/bin/python": "live"})

    cleanup_staging(staging)

    assert not staging.exists()
    assert (generations / "live" / "ralph/__init__.py").read_text(encoding="utf-8") == "published"
    assert (generations / "live" / ".venv/bin/python").read_text(encoding="utf-8") == "live"


def test_publish_generation_discards_unpublished_generation_when_launcher_fails(tmp_path: Path) -> None:
    candidate = tmp_path / ".staging" / "candidate"
    generations = tmp_path / "generations"
    _write_tree(candidate, {"ralph/__init__.py": "new", ".venv/bin/python": "new interpreter"})

    with pytest.raises(OSError, match="launcher failure"):
        publish_generation(
            candidate,
            generations,
            publish_launcher=lambda _generation: (_ for _ in ()).throw(OSError("launcher failure")),
        )

    assert not candidate.exists()
    assert not (generations / "candidate").exists()


def test_publish_generation_regression_retains_generation_when_launcher_recovery_is_not_durable(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / ".staging" / "candidate"
    generations = tmp_path / "generations"
    _write_tree(candidate, {"ralph/__init__.py": "new", ".venv/bin/python": "new interpreter"})

    with pytest.raises(InstallError, match="recovery was not durable"):
        publish_generation(
            candidate,
            generations,
            publish_launcher=lambda _generation: (_ for _ in ()).throw(
                InstallError("launcher publication failed and recovery was not durable")
            ),
        )

    assert not candidate.exists()
    assert (generations / "candidate" / "ralph" / "__init__.py").is_file()


def test_point_current_leaves_legacy_directory_untouched(tmp_path: Path) -> None:
    current = tmp_path / "current"
    generation = tmp_path / "generations" / "new"
    _write_tree(current, {"ralph/__init__.py": "legacy", ".venv/bin/python": "live"})
    _write_tree(generation, {"ralph/__init__.py": "new"})

    point_current(current, generation)

    assert not current.is_symlink()
    assert (current / ".venv/bin/python").read_text(encoding="utf-8") == "live"


def test_install_lock_fails_immediately_when_the_shared_install_is_busy(tmp_path: Path) -> None:
    lock_path = tmp_path / ".install.lock"

    with (
        install_lock(lock_path),
        pytest.raises(InstallLockUnavailableError, match="another Ralph install"),
        install_lock(lock_path),
    ):
        pass


def test_install_lock_does_not_unlock_when_acquisition_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[int] = []

    def fail_acquisition(_fd: int, operation: int) -> None:
        calls.append(operation)
        if operation & fcntl.LOCK_EX:
            raise OSError("busy")

    monkeypatch.setattr("ralph.install_transaction.fcntl.flock", fail_acquisition)

    with pytest.raises(InstallLockUnavailableError), install_lock(tmp_path / ".install.lock"):
        pass

    assert calls == [fcntl.LOCK_EX | fcntl.LOCK_NB]


def test_install_lock_rejects_symlinked_root_or_lock(tmp_path: Path) -> None:
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(InstallLockUnavailableError, match="symlink"), install_lock(
        root_link / ".install.lock"
    ):
        pass

    lock_target = tmp_path / "other-lock"
    lock_target.write_text("", encoding="utf-8")
    lock_link = real_root / ".install.lock"
    lock_link.symlink_to(lock_target)
    with pytest.raises(InstallLockUnavailableError, match="symlink"), install_lock(lock_link):
        pass
