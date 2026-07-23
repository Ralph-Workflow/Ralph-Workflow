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
    PackageWideCallSiteInvariant,
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
    expected = 21
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


def test_audit_blocks_regression_when_parallel_display_strip_markup_drops_control_strip(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: remove terminal-control stripping from ``strip_markup``."""
    path = "display/parallel_display.py"

    def _transform(src: str) -> str:
        # Replacing the composed sanitization with ``return line`` drops both
        # Rich-markup and terminal-control stripping; the body invariant catches it.
        return src.replace(
            "return _strip_markup(strip_terminal_control(line))",
            "return line",
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


# ---------------------------------------------------------------------------
# Adversarial cases for the new invariants added in the wt-036 rework:
#   * SpawnOptions.devnull default restored to None
#   * SpawnOptions(stdin=None) injected anywhere under ralph/
#   * ralph.logging.configure_logging reintroduces sys.stderr or colorize=True
#   * ralph.cli.main._configure_logging reintroduces sys.stderr
#   * ralph.cli.main.main drops make_sanitizing_log_sink
#   * ralph.display.log_sink.make_sanitizing_log_sink drops the stripper
#   * ralph.display.log_sink.make_stderr_log_sink drops the stripper
#   * ralph.display.log_sink constructs a Console inline
#   * ralph.process.pty.spawn_pty_process drops os.setsid() / TIOCSCTTY
# Each adversarial case below mirrors the pattern of the existing ones:
# monkeypatch the source via _patch_rel (or _read), run the audit, assert
# it exits 1 and prints the relevant violation banner.
# ---------------------------------------------------------------------------


def test_audit_blocks_regression_when_spawn_options_devnull_default_reverted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: revert SpawnOptions.stdin default to None."""
    path = "process/manager/_spawn_options.py"

    def _transform(src: str) -> str:
        return src.replace(
            "stdin: int | None = subprocess.DEVNULL",
            "stdin: int | None = None",
        )

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert path in captured.out
    # Either the file-level invariant or the FunctionBody invariant can fire;
    # we also assert the literal specifically.
    assert "subprocess.DEVNULL" in captured.out or "stdin=None" in captured.out


def test_audit_blocks_regression_when_a_new_spawn_options_call_passes_stdin_none(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: inject ``SpawnOptions(stdin=None)`` into a brand-new file.

    Drives :class:`PackageWideCallSiteInvariant`: the audit must
    catch the INHERIT leak anywhere under ``ralph/`` regardless
    of which file re-introduces it.
    """
    path = "agents/invoke/_process_reader.py"

    def _transform(src: str) -> str:
        # The file already has ``stdin=subprocess.DEVNULL``; add a SECOND
        # call site that re-opts into INHERIT.
        return src + "\nSpawnOptions(stdin=None,)\n"

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert path in captured.out
    # The package-wide invariant emits 'stdin=None' on the offending call.
    assert "stdin=None" in captured.out


def test_audit_blocks_regression_when_logging_configure_logging_drops_console_sink(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: revert ralph.logging.configure_logging to raw ``sys.stderr`` + colorize=True."""
    path = "logging.py"

    def _transform(src: str) -> str:
        # Replace the injected sink block with a raw sys.stderr + colorize=True
        # restore -- the function body's \"sink = console_sink...\" line is
        # the load-bearing seam, so removing it triggers BOTH the missing-
        # required-literal and the forbidden-literal checks.
        return src.replace(
            "sink = console_sink if console_sink is not None else make_stderr_log_sink()",
            "sink = sys.stderr",
        ).replace(
            "colorize=False,",
            "colorize=True,",
        )

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert path in captured.out
    assert "configure_logging" in captured.out
    assert "sys.stderr" in captured.out


def test_audit_blocks_regression_when_cli_configure_logging_drops_sink(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: revert ralph.cli.main._configure_logging to raw ``sys.stderr``."""
    path = "cli/main.py"

    def _transform(src: str) -> str:
        return src.replace(
            "sink = console_sink if console_sink is not None else make_stderr_log_sink()",
            "sink = None  # pretend we forgot to wire it",
        ).replace(
            "logger.add(sink, level=\"ERROR\")",
            "logger.add(sys.stderr, level=\"ERROR\")",
        )

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert path in captured.out
    assert "_configure_logging" in captured.out
    assert "sys.stderr" in captured.out


def test_audit_blocks_regression_when_cli_main_drops_sanitizing_log_sink(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: the CLI call site drops ``make_sanitizing_log_sink``."""
    path = "cli/main.py"

    def _transform(src: str) -> str:
        return src.replace(
            "configure_logging(verbosity, console_sink=make_sanitizing_log_sink(_cli_ctx))",
            "configure_logging(verbosity)",
        )

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert "main" in captured.out
    assert "make_sanitizing_log_sink" in captured.out


def test_audit_blocks_regression_when_sanitizing_log_sink_drops_stripper(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: ``make_sanitizing_log_sink`` skips ``strip_terminal_control``."""
    path = "display/log_sink.py"

    def _transform(src: str) -> str:
        return src.replace(
            "cleaned = strip_terminal_control(message.rstrip(\"\\n\"))",
            "cleaned = message.rstrip(\"\\n\")",
        )

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert path in captured.out
    assert "make_sanitizing_log_sink" in captured.out


def test_audit_blocks_regression_when_stderr_log_sink_drops_stripper(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: ``make_stderr_log_sink`` skips ``strip_terminal_control``."""
    path = "display/log_sink.py"

    def _transform(src: str) -> str:
        return src.replace(
            "cleaned = strip_terminal_control(message.rstrip(\"\\n\"))",
            "cleaned = message.rstrip(\"\\n\")",
        )

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert path in captured.out
    assert "make_stderr_log_sink" in captured.out


def test_audit_blocks_regression_when_log_sink_constructs_console_inline(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: ``ralph.display.log_sink`` constructs a Console inline.

    Tests/display/test_di_invariants.py also enforces this, but the
    audit is the load-bearing \"machines never speak unless asked\"
    layer so the regression must surface here too.
    """
    path = "display/log_sink.py"

    def _transform(src: str) -> str:
        return src + "\n_console = Console(file=sys.stderr)\n"

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert path in captured.out
    assert "Console(" in captured.out


def test_audit_blocks_regression_when_pty_spawn_drops_setsid_or_tiocsctty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adversarial: remove either ``os.setsid()`` or ``TIOCSCTTY`` from the PTY spawn."""
    path = "process/pty.py"

    def _transform(src: str) -> str:
        return src.replace("os.setsid()", "_ = None  # nosetsid")

    _patch_rel(monkeypatch, path, _transform)

    rc = audit_main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert path in captured.out
    assert "spawn_pty_process" in captured.out
    assert "os.setsid()" in captured.out


def test_package_wide_call_site_invariant_flags_stdin_none_in_any_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PackageWideCallSiteInvariant can be driven directly with a fake source.

    Pins the contract in isolation -- even without a call site
    already present in the package, the invariant must report a
    forbidden ``stdin=None`` when one is injected.
    """
    fake_source = "SpawnOptions(stdin=None,)\n"
    inv = PackageWideCallSiteInvariant(
        callee_name="SpawnOptions",
        absent=("stdin=None",),
    )

    # Use the audit's _read so the invariant reads our fake source everywhere;
    # the package file list (cached) still drives which rel_paths are scanned.
    monkeypatch.setattr(audit_module, "_read", lambda rel_path: fake_source)
    audit_module.PackageWideCallSiteInvariant.reset_cache()

    violations = inv.violations()
    # We don't assert on the absolute number (depends on file count), only
    # that AT LEAST ONE violation names the offending literal.
    assert any("stdin=None" in v for v in violations), (
        f"PackageWideCallSiteInvariant must flag stdin=None violations; got {violations!r}"
    )

    # Reset the cache so other tests see the real source again.
    audit_module.PackageWideCallSiteInvariant.reset_cache()


def test_package_wide_call_site_invariant_passes_when_no_stdin_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PackageWideCallSiteInvariant returns no violations when the package is clean."""
    inv = PackageWideCallSiteInvariant(
        callee_name="SpawnOptions",
        absent=("stdin=None",),
    )

    # No-op read: every file looks empty, so no SpawnOptions call is found at all.
    monkeypatch.setattr(audit_module, "_read", lambda rel_path: "")
    violations = inv.violations()
    assert violations == [], (
        f"PackageWideCallSiteInvariant must return no violations on an empty "
        f"package; got {violations!r}"
    )
