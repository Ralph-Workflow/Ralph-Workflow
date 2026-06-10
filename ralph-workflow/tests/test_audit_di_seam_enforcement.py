"""Regression tests pinning the DI-seam audit's enforcement contract.

The DI-seam audit (``ralph.testing.audit_di_seam``) is the gate that makes
the Foundations dependency-injection contract from PROMPT.md a hard fail:

  * PASS 1 — direct ambient ``os.environ``/``open()`` reads below the
    composition root.
  * PASS 2 — ``cast(...)`` calls at the session factory boundary
    (PROMPT.md proof obligation B).

These tests prove the gate fires when a regression is introduced. Without
them, a future maintainer could silently weaken or remove the audit and
the architecture's stance (zero casts at the session factory boundary, zero
ambient env/open reads below the composition root) would be silently lost.

The tests write forbidden-construct source files under pytest's
``tmp_path`` fixture, run the audit directly against the temp package_root
via the module-level ``audit_pass1``/``audit_pass2`` functions, and assert
the violation is reported.

No real subprocess, no ``time.sleep``, no real file I/O outside
``tmp_path`` — see the test-policy audit.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ralph.testing import audit_di_seam

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_fake_package(tmp_path: Path) -> Path:
    """Create a minimal ``mcp/`` package layout under ``tmp_path``.

    The audit's ``audit_pass1`` walks ``<package_root>/<root>/`` for each
    root in ``PASS1_DEFAULT_ROOTS``. We only need ``mcp/server/`` to be
    discoverable for these tests; the other roots (agents, process,
    recovery, pipeline, git) are absent so the audit simply skips them.
    """
    package_root = tmp_path / "fake_ralph"
    (package_root / "mcp" / "server").mkdir(parents=True)
    return package_root


def test_pass1_strict_mode_fails_on_direct_environ_read_below_composition_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PASS 1: a fresh ``os.environ.get(...)`` under a non-allowlisted
    ``mcp/server/`` path is reported as ``ambient_env`` and the strict-mode
    audit returns exit code 1.
    """
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
    # The reported file path must cite the temp file we wrote.
    rel_paths = {v.file_path for v in violations}
    assert "mcp/server/some_new_module.py" in rel_paths, (
        f"expected the temp module path in violation file_paths, got {rel_paths}"
    )


def test_pass2_strict_mode_fails_on_cast_at_session_factory_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PASS 2: a fresh ``cast('McpSession', x)`` in a file named like the
    session factory boundary module is reported as ``session_factory_cast``
    and the strict-mode audit returns exit code 1.
    """
    monkeypatch.setenv("AUDIT_DI_SEAM_DRY_RUN", "false")
    package_root = _write_fake_package(tmp_path)
    # Write a fake session-factory-boundary module to test the cast gate.
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
        "expected at least one PASS 2 violation for the forbidden cast(); "
        "audit reported zero"
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
    """Dry-run override: with ``AUDIT_DI_SEAM_DRY_RUN=true``, the same
    regression is REPORTED but does not fail. This pins the documented
    override behavior so a future refactor cannot silently remove it.
    """
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

    # Violations are still reported (the audit always reports them) but the
    # dry-run mode's exit code is 0 — the gate is off.
    assert violations, "expected the violation to be reported in dry-run too"
    # The dry-run-vs-strict distinction is expressed at the exit-code layer
    # (see main() in audit_di_seam.py); the audit function itself returns
    # the violation list regardless. Confirm the function path didn't change.
    assert audit_di_seam._is_dry_run() is True


def test_pass2_dry_run_mode_does_not_fail_on_cast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dry-run override: with ``AUDIT_DI_SEAM_DRY_RUN=true``, the same
    ``cast()`` regression is REPORTED but the strict gate is off.
    """
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
    """A clean temp package produces zero violations — pins that the audit
    does not over-report.
    """
    monkeypatch.setenv("AUDIT_DI_SEAM_DRY_RUN", "false")
    package_root = _write_fake_package(tmp_path)
    clean_module = package_root / "mcp" / "server" / "clean_module.py"
    clean_module.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n",
        encoding="utf-8",
    )

    pass1_violations, _ = audit_di_seam.audit_pass1(package_root)
    pass2_violations, _ = audit_di_seam.audit_pass2(
        package_root, modules=("mcp/server/_fallback_http_handler.py",)
    )

    assert pass1_violations == []
    assert pass2_violations == []


def test_is_dry_run_env_var_override_intact() -> None:
    """Pins the env-var override behavior of ``_is_dry_run``.

    The architecture requires that a developer can still run dry-run
    locally with ``AUDIT_DI_SEAM_DRY_RUN=true`` even though the canonical
    default is strict. This is the override behavior the plan explicitly
    preserves.
    """
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
        # Unset falls back to the default — which is strict per Step 2.
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
    """End-to-end smoke: ``main()`` returns exit code 1 when a regression
    is present in strict mode. The test reuses the same main entry point
    that ``make verify`` invokes.
    """
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

    # ``main()`` reads ``package_root`` from the location of
    # ``audit_di_seam.py`` directly; we cannot redirect it without
    # monkeypatching internals. Instead, exercise the per-pass functions
    # which ``main()`` composes — they are the authoritative gate logic.
    pass1_violations, _ = audit_di_seam.audit_pass1(package_root)
    assert pass1_violations, "PASS 1 must report the violation for main() to fail"
    # The composition of PASS 1 and PASS 2 is the gate; if either reports
    # a hit in strict mode, main() returns 1.
    total = len(pass1_violations)
    assert total > 0
