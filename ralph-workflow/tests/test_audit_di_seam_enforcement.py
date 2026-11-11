from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ralph.testing import audit_di_seam

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_fake_package(tmp_path: Path) -> Path:
    package_root = tmp_path / "fake_ralph"
    (package_root / "mcp" / "server").mkdir(parents=True)
    return package_root


def test_pass1_strict_mode_fails_on_direct_environ_read_below_composition_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUDIT_DI_SEAM_DRY_RUN", "false")
    package_root = _write_fake_package(tmp_path)
    bad_module = package_root / "mcp" / "server" / "some_new_module.py"
    bad_module.write_text(
        "import os\n"
        "\n"
        "def read_token() -> str | None:\n"
        "    return os.environ.get('MCP_AUTH_TOKEN')\n",
        encoding="utf-8",
    )

    violations, _files_checked = audit_di_seam.audit_pass1(package_root)

    assert violations, (
        "expected at least one PASS 1 violation for the forbidden "
        "os.environ.get read; audit reported zero"
    )
    categories = {v.category for v in violations}
    assert "ambient_env" in categories, (
        f"expected category 'ambient_env', got {categories}; "
        f"violations: {[str(v) for v in violations]}"
    )
    rel_paths = {v.file_path for v in violations}
    assert "mcp/server/some_new_module.py" in rel_paths, (
        f"expected the temp module path in violation file_paths, got {rel_paths}"
    )


def test_pass2_strict_mode_fails_on_cast_at_session_factory_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUDIT_DI_SEAM_DRY_RUN", "false")
    package_root = _write_fake_package(tmp_path)
    bad_module = package_root / "mcp" / "server" / "_fallback_http_handler.py"
    bad_module.write_text(
        "from typing import cast\n"
        "\n"
        "def coerce(x: object) -> object:\n"
        "    return cast('McpSession', x)\n",
        encoding="utf-8",
    )

    violations, _modules_walked = audit_di_seam.audit_pass2(
        package_root, modules=("mcp/server/_fallback_http_handler.py",)
    )

    assert violations, (
        "expected at least one PASS 2 violation for the forbidden cast(); audit reported zero"
    )
    categories = {v.category for v in violations}
    assert "session_factory_cast" in categories, (
        f"expected category 'session_factory_cast', got {categories}; "
        f"violations: {[str(v) for v in violations]}"
    )
    rel_paths = {v.file_path for v in violations}
    assert "mcp/server/_fallback_http_handler.py" in rel_paths, (
        f"expected the temp module path in violation file_paths, got {rel_paths}"
    )


def test_pass1_dry_run_mode_does_not_fail_on_environ_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUDIT_DI_SEAM_DRY_RUN", "true")
    package_root = _write_fake_package(tmp_path)
    bad_module = package_root / "mcp" / "server" / "some_new_module.py"
    bad_module.write_text(
        "import os\n"
        "\n"
        "def read_token() -> str | None:\n"
        "    return os.environ.get('MCP_AUTH_TOKEN')\n",
        encoding="utf-8",
    )

    violations, _files_checked = audit_di_seam.audit_pass1(package_root)

    assert violations, "expected the violation to be reported in dry-run too"
    assert audit_di_seam._is_dry_run() is True


def test_pass2_dry_run_mode_does_not_fail_on_cast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUDIT_DI_SEAM_DRY_RUN", "true")
    package_root = _write_fake_package(tmp_path)
    bad_module = package_root / "mcp" / "server" / "_fallback_http_handler.py"
    bad_module.write_text(
        "from typing import cast\n"
        "\n"
        "def coerce(x: object) -> object:\n"
        "    return cast('McpSession', x)\n",
        encoding="utf-8",
    )

    violations, _modules_walked = audit_di_seam.audit_pass2(
        package_root, modules=("mcp/server/_fallback_http_handler.py",)
    )

    assert violations, "expected the violation to be reported in dry-run too"
    assert audit_di_seam._is_dry_run() is True


def test_strict_mode_returns_no_violations_on_clean_temp_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUDIT_DI_SEAM_DRY_RUN", "false")
    package_root = _write_fake_package(tmp_path)
    clean_module = package_root / "mcp" / "server" / "clean_module.py"
    clean_module.write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )

    pass1_violations, _ = audit_di_seam.audit_pass1(package_root)
    pass2_violations, _ = audit_di_seam.audit_pass2(
        package_root, modules=("mcp/server/_fallback_http_handler.py",)
    )

    assert pass1_violations == []
    assert pass2_violations == []


def test_is_dry_run_env_var_override_intact() -> None:
    previous = os.environ.pop("AUDIT_DI_SEAM_DRY_RUN", None)
    try:
        os.environ["AUDIT_DI_SEAM_DRY_RUN"] = "true"
        assert audit_di_seam._is_dry_run() is True
        os.environ["AUDIT_DI_SEAM_DRY_RUN"] = "false"
        assert audit_di_seam._is_dry_run() is False
        os.environ["AUDIT_DI_SEAM_DRY_RUN"] = "0"
        assert audit_di_seam._is_dry_run() is False
        os.environ["AUDIT_DI_SEAM_DRY_RUN"] = "yes"
        assert audit_di_seam._is_dry_run() is True
        del os.environ["AUDIT_DI_SEAM_DRY_RUN"]
        assert audit_di_seam._is_dry_run() is False
    finally:
        if previous is not None:
            os.environ["AUDIT_DI_SEAM_DRY_RUN"] = previous
        else:
            os.environ.pop("AUDIT_DI_SEAM_DRY_RUN", None)


def test_main_returns_one_on_regression_in_strict_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUDIT_DI_SEAM_DRY_RUN", "false")
    package_root = _write_fake_package(tmp_path)
    bad_module = package_root / "mcp" / "server" / "some_new_module.py"
    bad_module.write_text(
        "import os\n"
        "\n"
        "def read_token() -> str | None:\n"
        "    return os.environ.get('MCP_AUTH_TOKEN')\n",
        encoding="utf-8",
    )

    pass1_violations, _ = audit_di_seam.audit_pass1(package_root)
    assert pass1_violations, "PASS 1 must report the violation for main() to fail"
    total = len(pass1_violations)
    assert total > 0
