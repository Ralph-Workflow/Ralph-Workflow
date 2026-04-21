from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE_PATH = REPO_ROOT / "Makefile"


def _target_body(name: str) -> list[str]:
    lines = MAKEFILE_PATH.read_text(encoding="utf-8").splitlines()
    body: list[str] = []
    in_target = False

    for line in lines:
        if not in_target:
            if line.startswith(f"{name}:"):
                in_target = True
            continue

        if not line.startswith("\t"):
            break

        body.append(line.strip())

    if not body:
        raise AssertionError(f"target {name!r} not found")

    return body


def test_verify_runs_single_covered_suite_instead_of_repeating_pytest() -> None:
    verify_body = _target_body("verify")

    assert '@$(MAKE) lint' in verify_body
    assert '@$(MAKE) typecheck' in verify_body
    assert '@$(MAKE) test-cov' in verify_body
    assert '@$(MAKE) test' not in verify_body
    assert '@$(MAKE) coverage' not in verify_body


def test_test_cov_runs_pytest_once_with_coverage() -> None:
    test_cov_body = _target_body("test-cov")
    pytest_lines = [line for line in test_cov_body if "pytest tests/" in line]

    assert len(pytest_lines) == 1
    assert "--cov=ralph" in pytest_lines[0]
    assert "-n $(PYTEST_WORKERS)" in pytest_lines[0]


def test_makefile_exposes_explicit_unit_and_integration_targets() -> None:
    unit_body = _target_body("test-unit")
    integration_body = _target_body("test-integration")

    assert len(unit_body) == 1
    assert len(integration_body) == 1
    assert "pytest tests/ -q" in unit_body[0]
    assert "--ignore=tests/integration" in unit_body[0]
    assert "pytest tests/integration/ -q" in integration_body[0]
