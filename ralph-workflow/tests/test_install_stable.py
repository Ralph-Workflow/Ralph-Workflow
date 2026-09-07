from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph import install as install_module

if TYPE_CHECKING:
    from collections.abc import Sequence


def test_install_stable_release_installs_pinned_global_via_uv_tool() -> None:
    commands: list[tuple[Sequence[str], Path]] = []
    preflighted: list[str] = []
    install_module.install_stable_release(
        run=lambda command, *, cwd: commands.append((tuple(command), cwd)),
        uv_executable="/usr/local/bin/uv",
        cwd=Path("/tmp/ralph-workflow"),
        preflight=preflighted.append,
    )
    assert preflighted == ["/usr/local/bin/uv"]
    assert commands == [
        (
            ("/usr/local/bin/uv", "tool", "install", "--force", "--upgrade", "ralph-workflow"),
            Path("/tmp/ralph-workflow"),
        )
    ]


def test_install_stable_release_pins_requested_version() -> None:
    commands: list[tuple[Sequence[str], Path]] = []
    install_module.install_stable_release(
        run=lambda command, *, cwd: commands.append((tuple(command), cwd)),
        uv_executable="/usr/local/bin/uv",
        cwd=Path("/tmp/ralph-workflow"),
        version="1.2.3",
        preflight=lambda _uv: None,
    )
    assert commands == [
        (
            ("/usr/local/bin/uv", "tool", "install", "--force", "ralph-workflow==1.2.3"),
            Path("/tmp/ralph-workflow"),
        )
    ]


def test_install_stable_release_marks_local_wheel_as_manual_build() -> None:
    commands: list[tuple[Sequence[str], Path]] = []
    flavors: list[tuple[Path, str]] = []
    install_module.install_stable_release(
        run=lambda command, *, cwd: commands.append((tuple(command), cwd)),
        uv_executable="/usr/local/bin/uv",
        cwd=Path("/tmp/ralph-workflow"),
        from_path=Path("/tmp/ralph-workflow/dist/ralph.whl"),
        which_fn=lambda _name: "/home/u/.local/bin/ralph",
        resolve_installed_package_file=lambda _exe: Path(
            "/home/u/.local/share/uv/tools/ralph-workflow/lib/python3.14/site-packages/ralph/__init__.py"
        ),
        write_flavor=lambda path, flavor, **_kwargs: flavors.append((path, flavor)),
        preflight=lambda _uv: None,
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
        )
    ]
    assert flavors == [
        (
            Path("/home/u/.local/share/uv/tools/ralph-workflow/lib/python3.14/site-packages"),
            "-build",
        )
    ]


def test_install_stable_release_requires_uv() -> None:
    commands: list[tuple[Sequence[str], Path]] = []
    with pytest.raises(RuntimeError, match="uv"):
        install_module.install_stable_release(
            run=lambda command, *, cwd: commands.append((tuple(command), cwd)),
            uv_executable=None,
            cwd=Path("/tmp/ralph-workflow"),
        )
    assert commands == []


@pytest.mark.parametrize("reported_version", ["uv 0.6.9", "uv definitely-not-a-version"])
def test_install_stable_release_rejects_unusable_uv_before_tool_mutation(
    reported_version: str,
) -> None:
    commands: list[tuple[Sequence[str], Path]] = []

    with pytest.raises(RuntimeError, match="uv >= 0.7.0"):
        install_module.install_stable_release(
            run=lambda command, *, cwd: commands.append((tuple(command), cwd)),
            uv_executable="/usr/local/bin/uv",
            cwd=Path("/tmp/ralph-workflow"),
            preflight=lambda _uv: (_ for _ in ()).throw(
                RuntimeError(f"uv >= 0.7.0 is required: {reported_version}")
            ),
        )

    assert commands == []


def test_install_stable_release_leaves_published_package_flavor_clean() -> None:
    writes: list[tuple[Path, str]] = []
    install_module.install_stable_release(
        run=lambda _command, *, cwd: None,
        uv_executable="uv",
        cwd=Path("/checkout"),
        write_flavor=lambda path, flavor: writes.append((path, flavor)),
        preflight=lambda _uv: None,
    )
    assert writes == []
