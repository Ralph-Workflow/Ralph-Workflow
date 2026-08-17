"""Tests for dev-snapshot identity: which checkout owns the machine-wide `rdev`.

The snapshot directory and the `rdev` launcher are shared by every checkout and
worktree on the machine, so an install silently takes them over from whoever
installed last. These tests pin the reporting that makes the handover visible.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

from ralph import install as install_module

if TYPE_CHECKING:
    import pytest

_build_meta = import_module("ralph._build_meta")


def _write_snapshot(root: Path, *, version: str, commit: str, source_path: str) -> Path:
    """Materialise a minimal dev snapshot so its identity can be read back."""
    package = root / "ralph"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (package / "_build_meta.py").write_text(
        'BUILD_FLAVOR: str = "-dev"\n'
        f'BUILD_SOURCE_COMMIT: str = "{commit}"\n'
        f'BUILD_SOURCE_PATH: str = "{source_path}"\n'
        'BUILD_INSTALLED_AT: str = "2026-08-02T12:00:00+00:00"\n',
        encoding="utf-8",
    )
    return root


def test_read_snapshot_identity_reports_the_checkout_a_snapshot_came_from(
    tmp_path: Path,
) -> None:
    """A single global snapshot must be able to name the checkout that owns it."""
    snapshot = _write_snapshot(
        tmp_path / "current",
        version="0.9.19",
        commit="205114bfc38f4069723a3bfa8c7926c610aef8ce",
        source_path="/checkouts/wt-063-kimi-support/ralph-workflow",
    )

    identity = install_module.read_snapshot_identity(snapshot)

    assert identity == install_module.SnapshotIdentity(
        source_path="/checkouts/wt-063-kimi-support/ralph-workflow",
        source_commit="205114bfc38f4069723a3bfa8c7926c610aef8ce",
        version="0.9.19",
    )


def test_read_snapshot_identity_returns_none_when_no_snapshot_is_installed(
    tmp_path: Path,
) -> None:
    """A first-time install has no previous identity to report."""
    assert install_module.read_snapshot_identity(tmp_path / "missing") is None


def test_install_summary_flags_a_snapshot_taken_over_by_another_checkout() -> None:
    """The clobber that makes `rdev` run a stale build must be stated outright."""
    summary = install_module.render_install_summary(
        source=Path("/checkouts/main/ralph-workflow"),
        commit="483cd5cc9d3977dc8d2a499414a76c5a814384ed",
        version="0.9.20-dev",
        snapshot=Path("/home/u/.local/share/ralph-workflow-dev/current"),
        launcher=Path("/home/u/.local/bin/rdev"),
        replaced=install_module.SnapshotIdentity(
            source_path="/checkouts/wt-063-kimi-support/ralph-workflow",
            source_commit="205114bfc38f4069723a3bfa8c7926c610aef8ce",
            version="0.9.19",
        ),
    )

    assert "/checkouts/main/ralph-workflow" in summary
    assert "483cd5cc" in summary
    assert "0.9.20-dev" in summary
    assert "/home/u/.local/share/ralph-workflow-dev/current" in summary
    assert "/home/u/.local/bin/rdev" in summary
    assert "/checkouts/wt-063-kimi-support/ralph-workflow" in summary
    assert "0.9.19" in summary
    assert "different checkout" in summary


def test_install_summary_does_not_cry_takeover_for_a_reinstall_of_the_same_checkout() -> None:
    """Re-installing from the same checkout is routine and must not read as a warning."""
    summary = install_module.render_install_summary(
        source=Path("/checkouts/main/ralph-workflow"),
        commit="483cd5cc9d3977dc8d2a499414a76c5a814384ed",
        version="0.9.20-dev",
        snapshot=Path("/snapshot/current"),
        launcher=Path("/bin/rdev"),
        replaced=install_module.SnapshotIdentity(
            source_path="/checkouts/main/ralph-workflow",
            source_commit="205114bfc38f4069723a3bfa8c7926c610aef8ce",
            version="0.9.19",
        ),
    )

    assert "different checkout" not in summary


def test_install_summary_omits_the_replaced_line_on_a_first_install() -> None:
    summary = install_module.render_install_summary(
        source=Path("/checkouts/main/ralph-workflow"),
        commit="",
        version="0.9.20-dev",
        snapshot=Path("/snapshot/current"),
        launcher=Path("/bin/rdev"),
        replaced=None,
    )

    assert "replaced" not in summary
    assert "0.9.20-dev" in summary


def test_install_dev_checkout_reports_what_it_installed_and_what_it_replaced() -> None:
    """`make dev` must say which checkout now owns `rdev`, and which one lost it."""
    emitted: list[str] = []
    previous = install_module.SnapshotIdentity(
        source_path="/checkouts/wt-063-kimi-support/ralph-workflow",
        source_commit="205114bfc38f4069723a3bfa8c7926c610aef8ce",
        version="0.9.19",
    )

    install_module.install_dev_checkout(
        run=lambda _command, *, cwd: None,
        uv_executable="uv",
        cwd=Path("/checkouts/main/ralph-workflow"),
        launcher_dir=Path("/home/u/.local/bin"),
        install_root=Path("/install"),
        copy_tree=lambda _source, destination: destination,
        write_flavor=lambda *_args, **_kwargs: None,
        resolve_commit=lambda _source: "483cd5cc9d3977dc8d2a499414a76c5a814384ed",
        installed_at=lambda: "2026-08-02T12:00:00+00:00",
        write_launcher=lambda _path, _content: None,
        read_identity=lambda _snapshot: previous,
        emit=emitted.append,
    )

    report = "\n".join(emitted)
    assert "/checkouts/main/ralph-workflow" in report
    assert "/checkouts/wt-063-kimi-support/ralph-workflow" in report
    assert "0.9.19" in report
    assert "different checkout" in report


def test_install_dev_checkout_reads_the_previous_identity_before_overwriting_it() -> None:
    """The replaced build can only be reported if it is read before the copy runs."""
    order: list[str] = []

    def fake_copy(_source: Path, destination: Path) -> Path:
        order.append("copy")
        return destination

    def fake_read_identity(_snapshot: Path) -> install_module.SnapshotIdentity | None:
        order.append("read")
        return None

    install_module.install_dev_checkout(
        run=lambda _command, *, cwd: None,
        uv_executable="uv",
        cwd=Path("/checkouts/main/ralph-workflow"),
        launcher_dir=Path("/bin"),
        install_root=Path("/install"),
        copy_tree=fake_copy,
        write_flavor=lambda *_args, **_kwargs: None,
        resolve_commit=lambda _source: "",
        installed_at=lambda: "",
        write_launcher=lambda _path, _content: None,
        read_identity=fake_read_identity,
        emit=lambda _line: None,
    )

    assert order == ["read", "copy"]


def test_install_dev_checkout_stamps_the_source_checkout_path() -> None:
    """The snapshot must carry the checkout path so `rdev` can name its origin."""
    stamped: dict[str, str] = {}

    def fake_writer(
        _path: Path,
        _flavor: str,
        *,
        source_commit: str = "",
        source_path: str = "",
        installed_at: str = "",
    ) -> None:
        del source_commit, installed_at
        stamped["source_path"] = source_path

    install_module.install_dev_checkout(
        run=lambda _command, *, cwd: None,
        uv_executable="uv",
        cwd=Path("/checkouts/main/ralph-workflow"),
        launcher_dir=Path("/bin"),
        install_root=Path("/install"),
        copy_tree=lambda _source, destination: destination,
        write_flavor=fake_writer,
        resolve_commit=lambda _source: "",
        installed_at=lambda: "",
        write_launcher=lambda _path, _content: None,
        read_identity=lambda _snapshot: None,
        emit=lambda _line: None,
    )

    assert stamped == {"source_path": str(Path("/checkouts/main/ralph-workflow"))}


def test_build_provenance_line_names_the_source_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--version` must expose which checkout the running build came from."""
    monkeypatch.setattr(_build_meta, "BUILD_SOURCE_PATH", "/checkouts/main/ralph-workflow")
    monkeypatch.setattr(
        _build_meta, "BUILD_SOURCE_COMMIT", "483cd5cc9d3977dc8d2a499414a76c5a814384ed"
    )

    line = _build_meta.build_provenance_line()

    assert "/checkouts/main/ralph-workflow" in line
    assert "483cd5cc" in line


def test_build_provenance_line_is_empty_for_a_published_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A released install has no checkout provenance and must stay silent."""
    monkeypatch.setattr(_build_meta, "BUILD_SOURCE_PATH", "")
    monkeypatch.setattr(_build_meta, "BUILD_SOURCE_COMMIT", "")

    assert _build_meta.build_provenance_line() == ""
