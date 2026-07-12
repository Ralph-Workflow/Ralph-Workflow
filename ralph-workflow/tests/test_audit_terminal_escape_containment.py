"""Tests for ``ralph.testing.audit_terminal_escape_containment``.

The audit enforces literal-string invariants that keep the terminal-escape
containment contract wired across the seven files the wt-036 rework
touches. Each test mirrors ``tests/test_audit_parallelization_dormant.py``
in structure: clean-tree assertions, regression-flavor assertions with
monkey-patched sources, and a violation case for the most important
invariant (the ``[0-9;?]`` narrowing regression).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import ralph.testing.audit_terminal_escape_containment as audit_module
from ralph.testing.audit_terminal_escape_containment import Invariant
from ralph.testing.audit_terminal_escape_containment import main as audit_main

if TYPE_CHECKING:
    import pytest


def test_audit_returns_zero_when_all_invariants_satisfied() -> None:
    """``main()`` returns 0 on the in-tree literal set."""
    assert audit_main([]) == 0


def test_audit_module_path() -> None:
    """Audit must be importable as ``ralph.testing.audit_terminal_escape_containment``."""
    assert hasattr(audit_module, "main")


def test_audit_invariant_count_is_eight() -> None:
    """Eight files carry one invariant each (one per touched surface)."""
    assert len(audit_module._INVARIANTS) == 8


def test_audit_line_sanitizer_invariant_pins_full_csi_class() -> None:
    """The line_sanitizer invariant pins the [0-?] class explicitly."""
    ls_invariant = next(
        inv
        for inv in audit_module._INVARIANTS
        if inv.rel_path == "display/line_sanitizer.py"
    )
    assert "def strip_terminal_control" in ls_invariant.present
    assert "[0-?]" in ls_invariant.present
    assert "[0-9;?]" in ls_invariant.absent


def test_audit_blocks_regression_when_csi_class_is_narrowed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: the line_sanitizer invariant must catch a narrowing back to a digit-only class.

    Drives the ``Invariant`` class directly with an in-memory fake source
    that omits ``[0-?]`` and carries the narrower ``[0-9;?]`` form. No file
    on disk is touched. Asserts:

      - ``violations()`` reports the missing required literal AND the
        forbidden literal,
      - ``main([])`` exits 1 and prints a banner naming the file.
    """
    fake_source = (
        "# intentionally narrow -- regression test\n"
        "def strip_terminal_control(text):\n"
        "    return re.sub(r'[0-9;?]*', '', text)\n"
    )
    fake_invariant = Invariant(
        rel_path="display/line_sanitizer.py",
        present=("def strip_terminal_control", "[0-?]"),
        absent=("[0-9;?]",),
    )

    monkeypatch.setattr(
        audit_module, "_read", lambda rel_path: fake_source if rel_path == "display/line_sanitizer.py" else ""
    )
    monkeypatch.setattr(
        audit_module,
        "_INVARIANTS",
        (fake_invariant, *audit_module._INVARIANTS[1:]),
    )

    violations = fake_invariant.violations()
    assert any("[0-?]" in v for v in violations), (
        f"violation report must name the missing required literal; got {violations!r}"
    )
    assert any("[0-9;?]" in v for v in violations), (
        f"violation report must name the forbidden narrower literal; got {violations!r}"
    )

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1, "audit must exit 1 when [0-?] is missing and [0-9;?] is present"
    assert "display/line_sanitizer.py" in captured.out
    assert "TERMINAL-ESCAPE-CONTAINMENT AUDIT FAILED" in captured.out


def test_audit_blocks_regression_when_strip_terminal_control_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: removing the helper name from line_sanitizer must fail the audit."""
    real_read = audit_module._read
    path = "display/line_sanitizer.py"

    def _read_without_helper(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path != path:
            return content
        return content.replace("def strip_terminal_control", "def _gone")

    monkeypatch.setattr(audit_module, "_read", _read_without_helper)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1, "audit must exit 1 when strip_terminal_control is removed"
    assert path in captured.out


def test_audit_blocks_regression_when_sgr_only_regex_returns(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: re-introducing ``[0-9;]*m`` in _plain_constants must fail."""
    real_read = audit_module._read
    path = "display/_plain_constants.py"

    def _read_with_sgr(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path != path:
            return content
        # Inject the forbidden root-cause regex on a fresh line.
        return content + "\n_LEGACY = re.compile(r'[0-9;]*m')\n"

    monkeypatch.setattr(audit_module, "_read", _read_with_sgr)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert path in captured.out
    assert "[0-9;]*m" in captured.out


def test_audit_blocks_regression_when_pty_runner_paint_returns(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: re-introducing ``file=sys.stdout`` in _pty_runner must fail."""
    real_read = audit_module._read
    path = "agents/invoke/_pty_runner.py"

    def _read_with_paint(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path != path:
            return content
        return content + "\ntqdm(..., file=sys.stdout)\n"

    monkeypatch.setattr(audit_module, "_read", _read_with_paint)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert path in captured.out


def test_audit_blocks_regression_when_stdin_none_returns_to_process_reader(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: re-introducing ``stdin=None,`` in the reader must fail."""
    real_read = audit_module._read
    path = "agents/invoke/_process_reader.py"

    def _read_with_inherit(rel_path: str) -> str:
        content = real_read(rel_path)
        if rel_path != path:
            return content
        return content + "\nSpawnOptions(stdin=None,)\n"

    monkeypatch.setattr(audit_module, "_read", _read_with_inherit)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert path in captured.out
    assert "stdin=None," in captured.out
