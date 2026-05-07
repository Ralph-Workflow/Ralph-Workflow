"""Regression guard: README.md and CONTRIBUTING.md must not hard-code stale test-cov flags.

Asserts invariants, not exact recipe strings, so the test survives valid internal
changes to worker counts or coverage thresholds without needing to be updated.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
CONTRIBUTING_PATH = REPO_ROOT / "CONTRIBUTING.md"

_STALE_FLAGS = [
    "--cov-report=term-missing",
    "--cov-report=html",
]


def test_readme_does_not_hard_code_stale_cov_report_flags() -> None:
    """README.md must not document obsolete --cov-report=term-missing or --cov-report=html."""
    readme = README_PATH.read_text(encoding="utf-8")
    found = [flag for flag in _STALE_FLAGS if flag in readme]
    assert not found, (
        "README.md documents obsolete coverage flags that no longer match Makefile:test-cov:\n"
        + "\n".join(f"  {f}" for f in found)
        + "\n\nUpdate README.md to describe the canonical `make verify` workflow instead."
    )


def test_contributing_does_not_hard_code_stale_cov_report_flags() -> None:
    """CONTRIBUTING.md must not document obsolete --cov-report=term-missing or --cov-report=html."""
    contributing = CONTRIBUTING_PATH.read_text(encoding="utf-8")
    found = [flag for flag in _STALE_FLAGS if flag in contributing]
    assert not found, (
        "CONTRIBUTING.md documents obsolete coverage flags that no longer match "
        "Makefile:test-cov:\n"
        + "\n".join(f"  {f}" for f in found)
        + "\n\nReplace with the canonical `make test-cov` command."
    )


def test_readme_verification_section_references_make_verify() -> None:
    """README.md Verification section must point to the canonical `make verify` workflow."""
    readme = README_PATH.read_text(encoding="utf-8")
    assert "make verify" in readme, (
        "README.md must reference the canonical `make verify` command in its Verification section."
    )


def test_contributing_required_verification_references_make_verify() -> None:
    """CONTRIBUTING.md Required verification must point to the canonical `make verify` workflow."""
    contributing = CONTRIBUTING_PATH.read_text(encoding="utf-8")
    assert "make verify" in contributing, (
        "CONTRIBUTING.md must reference the canonical `make verify` command "
        "in its Required verification section."
    )
