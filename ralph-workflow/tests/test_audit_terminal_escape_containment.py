"""Tests for ``ralph.testing.audit_terminal_escape_containment``.

The audit enforces literal-string AND AST-scoped invariants that
keep the terminal-escape containment contract wired across the
seven files the wt-036 rework touches. Each test mirrors
``tests/test_audit_parallelization_dormant.py`` in structure:
clean-tree assertions, regression-flavor assertions with
monkey-patched sources, and a violation case for the most
important invariants (the ``[0-9;?]`` narrowing regression and
each per-sink adversarial mutation).

The AST-scoped invariants use ``FunctionBodyInvariant`` and
``CallSiteInvariant`` so the audit cannot be satisfied by a
helper name appearing anywhere in the file -- the strip call
must actually live inside the wired sink.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import ralph.testing.audit_terminal_escape_containment as audit_module
from ralph.testing.audit_terminal_escape_containment import (
    CallSiteInvariant,
    FunctionBodyInvariant,
    Invariant,
)
from ralph.testing.audit_terminal_escape_containment import main as audit_main

if TYPE_CHECKING:
    import pytest


def test_audit_returns_zero_when_all_invariants_satisfied() -> None:
    """``main()`` returns 0 on the in-tree literal set."""
    assert audit_main([]) == 0


def test_audit_module_path() -> None:
    """Audit must be importable as ``ralph.testing.audit_terminal_escape_containment``."""
    assert hasattr(audit_module, "main")


def test_audit_invariant_count_matches_table() -> None:
    """The audit pins every sink (file-level, function-body, and SpawnOptions call-site)."""
    expected = 12
    assert len(audit_module._INVARIANTS) == expected


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


# ---------------------------------------------------------------------------
# AST-scoped invariants: prove the audit inspects FUNCTION BODIES, not just
# whether the helper name appears anywhere in the file. Each test below
# strips the call from the wired sink and keeps the helper name present
# elsewhere (docstring, import, etc.) -- a whole-file literal check would
# still pass, the AST check must fail.
# ---------------------------------------------------------------------------


def _patch_rel(monkeypatch: pytest.MonkeyPatch, rel_path: str, transform) -> None:
    real_read = audit_module._read

    def _read(rel_path_arg: str) -> str:
        content = real_read(rel_path_arg)
        if rel_path_arg == rel_path:
            return transform(content)
        return content

    monkeypatch.setattr(audit_module, "_read", _read)


def test_audit_blocks_regression_when_activity_model_render_event_line_drops_strip(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: keep the helper name (in import + docstring) but remove the strip from ``render_event_line``.

    A naive whole-file check would still see ``strip_terminal_control`` in
    the import line and pass. The FunctionBodyInvariant on
    ``render_event_line`` must catch the regression.
    """
    path = "display/activity_model.py"

    def _transform(src: str) -> str:
        return src.replace(
            "safe_content = strip_terminal_control(content or \"\")",
            "safe_content = content or \"\"",
        )

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1, (
        "audit must exit 1 when activity_model.render_event_line body drops strip_terminal_control"
    )
    assert "render_event_line body missing required literal" in captured.out
    assert path in captured.out


def test_audit_blocks_regression_when_parallel_display_strip_markup_drops_strip(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: revert ``ParallelDisplay.strip_markup`` to the pre-fix ``_strip_markup``-only form."""
    path = "display/parallel_display.py"

    def _transform(src: str) -> str:
        return src.replace(
            "return strip_terminal_control(_strip_markup(line))",
            "return _strip_markup(line)",
        )

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert "ParallelDisplay.strip_markup body missing required literal" in captured.out


def test_audit_blocks_regression_when_module_level_emit_activity_line_drops_sanitize(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: revert ``emit_activity_line`` unit_id-is-None branch to unsanitized print."""
    path = "display/parallel_display.py"

    def _transform(src: str) -> str:
        # The pre-fix branch had ``console.print(line)`` -- this matches
        # the whole-file forbidden literal AND removes the _sanitize call
        # from emit_activity_line's body.
        return src.replace(
            "console.print(_sanitize(line), markup=False, highlight=False)",
            "console.print(line)",
        )

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert path in captured.out
    # The function-body invariant MUST fire (in addition to the file-level
    # forbidden literal). The audit pins ``_sanitize(line)`` with min_count=2
    # so reverting ONE branch is caught even if the other still sanitizes.
    assert "minimum required is 2" in captured.out
    assert "emit_activity_line body has 1 occurrence" in captured.out


def test_audit_blocks_regression_when_render_titled_lines_drops_strip(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: revert ``_render_titled_lines`` to print raw lines."""
    path = "display/parallel_display.py"

    def _transform(src: str) -> str:
        return src.replace(
            "strip_terminal_control(line), markup=False, highlight=False",
            "line, markup=False, highlight=False",
        )

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert "_render_titled_lines body missing required literal" in captured.out


def test_audit_blocks_regression_when_spawn_options_drops_devnull(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: revert SpawnOptions ``stdin=subprocess.DEVNULL`` back to ``stdin=None``.

    This is the critical regression -- re-introducing INHERIT means the
    agent child can take over Ralph's TTY. Both the file-level invariant
    and the SpawnOptions call-site invariant must fire.
    """
    path = "agents/invoke/_process_reader.py"

    def _transform(src: str) -> str:
        return src.replace(
            "stdin=subprocess.DEVNULL,",
            "stdin=None,",
        )

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert path in captured.out
    # The call-site invariant must fire.
    assert "no SpawnOptions call passes" in captured.out


def test_audit_blocks_regression_when_subprocess_executor_drops_devnull(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: revert SpawnOptions ``stdin=_DEVNULL`` back to ``stdin=None``."""
    path = "agents/subprocess_executor.py"

    def _transform(src: str) -> str:
        return src.replace(
            "stdin=_DEVNULL,",
            "stdin=None,",
        )

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert path in captured.out
    assert "no SpawnOptions call passes" in captured.out


def test_function_body_invariant_returns_violations_for_missing_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FunctionBodyInvariant reports the missing target as a violation."""
    inv = FunctionBodyInvariant(
        rel_path="display/parallel_display.py",
        qualname="ParallelDisplay.does_not_exist",
        present=("strip_terminal_control",),
    )
    monkeypatch.setattr(audit_module, "_read", lambda rel_path: "")
    violations = inv.violations()
    assert any("not found" in v for v in violations), violations


def test_call_site_invariant_reports_missing_callee() -> None:
    """CallSiteInvariant reports no callee as a violation."""
    inv = CallSiteInvariant(
        rel_path="agents/invoke/_process_reader.py",
        callee_name="NoSuchCallable",
        present=("stdin=DEVNULL",),
    )
    violations = inv.violations()
    assert any("not found" in v or "no SpawnOptions" in v.lower() or "call site" in v for v in violations), (
        violations
    )
