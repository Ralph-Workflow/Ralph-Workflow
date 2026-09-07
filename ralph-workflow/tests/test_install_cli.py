from __future__ import annotations

import builtins
import importlib.util
from importlib import import_module
from pathlib import Path

import pytest

from ralph import install as install_module
from ralph._install_conflicts import ExistingInstall
from ralph.update_check._install_kind import InstallKind

_build_meta = import_module("ralph._build_meta")


def _uv_tool_ralph() -> ExistingInstall:
    return ExistingInstall(
        Path("/home/u/.local/bin/ralph"),
        Path("/home/u/.local/share/uv/tools/ralph-workflow/lib/ralph/__init__.py"),
        InstallKind.UV_TOOL,
    )


def test_install_module_imports_without_process_manager_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def fail_on_missing_psutil(name: str, *args: object, **kwargs: object) -> object:
        if name == "psutil":
            raise ModuleNotFoundError(f"No module named {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_on_missing_psutil)
    spec = importlib.util.spec_from_file_location(
        "bootstrap_safe_install_module",
        Path(__file__).resolve().parents[1] / "ralph" / "install.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)


def test_main_default_installs_dev_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        install_module, "install_dev_checkout", lambda **kwargs: captured.update(kwargs)
    )
    monkeypatch.setattr(
        install_module,
        "install_stable_release",
        lambda **_kwargs: pytest.fail("unexpected stable install"),
    )
    monkeypatch.setattr(install_module.shutil, "which", lambda name: f"/opt/bin/{name}")
    monkeypatch.setattr(install_module, "_absolute_uv_path", lambda path: path)
    monkeypatch.setattr(install_module.Path, "home", classmethod(lambda _cls: Path("/home/u")))
    assert install_module.main([]) == 0
    assert captured["flavor"] == "-dev"
    assert captured["launcher_dir"] == Path("/home/u/.local/bin")


@pytest.mark.parametrize("argv", [[], ["--build"]])
def test_main_default_install_keeps_conflicting_ralph_and_explains_rdev(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    installs: list[object] = []
    monkeypatch.setattr(install_module, "detect_existing_ralph", lambda **_kwargs: _uv_tool_ralph())
    monkeypatch.setattr(
        install_module,
        "prompt_for_conflict",
        lambda *_args, **_kwargs: pytest.fail("unexpected conflict prompt"),
    )
    monkeypatch.setattr(
        install_module, "install_dev_checkout", lambda **kwargs: installs.append(kwargs)
    )
    monkeypatch.setattr(install_module.shutil, "which", lambda _name: "/opt/bin/uv")
    monkeypatch.setattr(install_module, "_absolute_uv_path", lambda path: path)
    assert install_module.main(argv) == 0
    assert installs
    assert "rdev" in capsys.readouterr().out


def test_main_stable_flag_installs_pinned_release(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        install_module, "install_stable_release", lambda **kwargs: captured.update(kwargs)
    )
    monkeypatch.setattr(
        install_module,
        "install_dev_checkout",
        lambda **_kwargs: pytest.fail("unexpected dev install"),
    )
    monkeypatch.setattr(install_module, "preflight_uv", lambda _uv: None)
    monkeypatch.setattr(install_module.shutil, "which", lambda name: f"/opt/bin/{name}")
    monkeypatch.setattr(install_module, "_absolute_uv_path", lambda path: path)
    assert install_module.main(["--stable"]) == 0
    assert captured["version"] is None


def test_main_stable_preflights_before_resolving_install_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    monkeypatch.setattr(install_module.shutil, "which", lambda _name: "/opt/bin/uv")
    monkeypatch.setattr(install_module, "_absolute_uv_path", lambda path: path)
    monkeypatch.setattr(install_module, "preflight_uv", lambda _uv: order.append("preflight"))
    monkeypatch.setattr(
        install_module, "_resolve_install_conflict", lambda **_kwargs: order.append("conflict")
    )
    monkeypatch.setattr(
        install_module, "install_stable_release", lambda **_kwargs: order.append("install")
    )

    assert install_module.main(["--stable"]) == 0
    assert order == ["preflight", "conflict", "install"]


def test_flavored_version_reports_build_and_dev_suffixes(monkeypatch: pytest.MonkeyPatch) -> None:
    for flavor in ("", "-build", "-dev"):
        monkeypatch.setattr(_build_meta, "BUILD_FLAVOR", flavor)
        assert _build_meta.flavored_version() == _build_meta._BASE_VERSION + flavor
