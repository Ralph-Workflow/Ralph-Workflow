from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph import install as install_module

if TYPE_CHECKING:
    from collections.abc import Sequence


def test_install_dev_checkout_syncs_env_and_writes_rdev_launcher() -> None:
    commands: list[tuple[Sequence[str], Path]] = []
    launchers: list[tuple[Path, str]] = []
    flavors: list[tuple[Path, str]] = []
    install_module.install_dev_checkout(
        run=lambda command, *, cwd: commands.append((tuple(command), cwd)),
        uv_executable="/usr/local/bin/uv",
        cwd=Path("/checkout"),
        launcher_dir=Path("/bin"),
        install_root=Path("/install"),
        copy_tree=lambda _source, destination: destination,
        write_flavor=lambda path, flavor, **_kwargs: flavors.append((path, flavor)),
        write_launcher=lambda path, content: launchers.append((path, content)),
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
    assert commands == [
        (("/usr/local/bin/uv", "lock", "--check"), Path("/install/.staging/candidate")),
        (("/usr/local/bin/uv", "sync", "--locked", "--extra", "dev"), Path("/install/.staging/candidate")),
        (
            ("/usr/local/bin/uv", "sync", "--locked", "--extra", "dev", "--check"),
            Path("/install/.staging/candidate"),
        ),
    ]
    assert flavors == [(Path("/install/.staging/candidate"), "-dev")]
    assert launchers[0][0] == Path("/bin/rdev")
    assert "uv run --locked --project" in launchers[0][1]


def test_install_dev_checkout_requires_uv() -> None:
    with pytest.raises(RuntimeError, match="uv"):
        install_module.install_dev_checkout(
            uv_executable=None, cwd=Path("/checkout"), launcher_dir=Path("/bin")
        )


def test_install_dev_checkout_regression_checks_the_same_dev_extra_selection() -> None:
    commands: list[tuple[str, ...]] = []

    install_module.install_dev_checkout(
        run=lambda command, *, cwd: commands.append(tuple(command)),
        uv_executable="uv",
        cwd=Path("/checkout"),
        launcher_dir=Path("/bin"),
        install_root=Path("/install"),
        copy_tree=lambda _source, destination: destination,
        write_flavor=lambda *_args, **_kwargs: None,
        resolve_commit=lambda _source: "",
        installed_at=lambda: "",
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

    assert commands[-2:] == [
        ("uv", "sync", "--locked", "--extra", "dev"),
        ("uv", "sync", "--locked", "--extra", "dev", "--check"),
    ]


def test_snapshot_validation_accepts_the_cli_version_banner() -> None:
    from ralph._install_runtime import validate_snapshot_version

    validate_snapshot_version(
        lambda _uv, _snapshot: "Ralph Workflow v0.9.27-build\n",
        "uv",
        Path("/install/current"),
        "-build",
    )


def test_snapshot_validation_rejects_a_cli_version_banner_without_the_selected_flavor() -> None:
    from ralph._install_runtime import validate_snapshot_version

    with pytest.raises(RuntimeError, match="-build"):
        validate_snapshot_version(
            lambda _uv, _snapshot: "Ralph Workflow v0.9.27\n",
            "uv",
            Path("/install/current"),
            "-build",
        )


@pytest.mark.parametrize(
    "hostile_path",
    [
        Path('/snapshot/space and "quote"'),
        Path("/snapshot/$() backtick`"),
        Path("/snapshot/line\nbreak"),
    ],
)
def test_dev_launcher_quotes_absolute_uv_and_snapshot_paths(hostile_path: Path) -> None:
    launcher = install_module.render_dev_launcher(hostile_path, "/tools/uv $() `quoted`")

    assert "exec '/tools/uv $() `quoted`' run --locked --project" in launcher
    assert 'ralph "$@"' in launcher
