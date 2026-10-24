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

The adversarial regressions share one recipe (patch a file, narrow
the audit to the invariant that owns that seam, run the audit, assert
it fails and says why), so they are driven from a single table: the
row carries the source edit, the seam to narrow to, and the fragments
the audit output must name. A failure message always names the row.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, NamedTuple

import pytest

import ralph.testing.audit_terminal_escape_containment as audit_module
from ralph.testing.audit_terminal_escape_containment import (
    CallSiteInvariant,
    FunctionBodyInvariant,
    Invariant,
    PackageWideCallSiteInvariant,
)
from ralph.testing.audit_terminal_escape_containment import main as audit_main

if TYPE_CHECKING:
    from collections.abc import Callable

# NOTE: ``test_audit_returns_zero_when_all_invariants_satisfied`` and
# ``test_audit_module_path`` were removed in the wt-05-test-opti pass.
# The clean-tree check duplicates the audit already executed by
# ``make verify`` (the ``audit_terminal_escape_containment`` step in
# ``_VERIFY_STEPS``), and the module-path test is a pure import smoke
# that the same verify step implicitly proves. The remaining tests
# cover the AST + literal-string regression contract with monkey-patched
# sources.

#: The audit's UNPATCHED source reader, captured at import time. A
#: table-driven regression test patches several files in sequence; each
#: patch must start from the REAL source, not from the previous row's
#: already-patched reader.
_REAL_READ = audit_module._read

#: The audit's FULL invariant table, captured at import time. The same
#: reasoning applies to the narrowing helper: every row must narrow from
#: the complete table, not from the previous row's narrowed tuple.
_REAL_INVARIANTS = audit_module._INVARIANTS


class _RegressionCase(NamedTuple):
    """One adversarial regression row for the terminal-containment audit.

    Attributes:
        name: Human-readable label used in the failure messages.
        rel_path: The production file the regression is injected into.
        invariant_cls: The invariant class that owns the patched seam.
        expected_fragments: Fragments the audit output must name.
        qualname: Seam filter for the narrowing helper (``None`` = no filter).
        callee_name: Callee filter for the narrowing helper (``None`` = no filter).
        narrow_rel_path: Whether the narrowing filters on ``rel_path``.
        edits: ``(old_literal, new_literal)`` replacements applied to the source.
        appended: Text appended to the source after the replacements.
        narrow_package_files: Whether the package-wide file list must be
            narrowed to the patched file (the package-wide invariant would
            otherwise walk the whole tree).
    """

    name: str
    rel_path: str
    invariant_cls: type[object]
    expected_fragments: tuple[str, ...]
    qualname: str | None = None
    callee_name: str | None = None
    narrow_rel_path: bool = True
    edits: tuple[tuple[str, str], ...] = ()
    appended: str = ""
    narrow_package_files: bool = False


#: The adversarial regressions the audit must catch, one row per seam.
_REGRESSION_CASES: tuple[_RegressionCase, ...] = (
    _RegressionCase(
        name="activity_model.render_event_line drops the strip",
        rel_path="display/activity_model.py",
        invariant_cls=FunctionBodyInvariant,
        qualname="render_event_line",
        expected_fragments=("render_event_line body missing required literal",),
        edits=(
            (
                'safe_content = strip_terminal_control(content or "")',
                'safe_content = content or ""',
            ),
        ),
    ),
    _RegressionCase(
        name="ParallelDisplay.strip_markup drops the strip",
        rel_path="display/parallel_display.py",
        invariant_cls=FunctionBodyInvariant,
        qualname="ParallelDisplay.strip_markup",
        expected_fragments=("ParallelDisplay.strip_markup body missing required literal",),
        edits=(("return strip_terminal_control(line)", "return line"),),
    ),
    _RegressionCase(
        name="emit_activity_line sanitizes only one branch",
        rel_path="display/parallel_display.py",
        invariant_cls=FunctionBodyInvariant,
        qualname="emit_activity_line",
        expected_fragments=("minimum required is 2", "emit_activity_line body has 1 occurrence"),
        edits=(
            (
                "console.print(_sanitize(line), markup=False, highlight=False)",
                "console.print(line)",
            ),
        ),
    ),
    _RegressionCase(
        name="ParallelDisplay._render_titled_lines prints raw lines",
        rel_path="display/parallel_display.py",
        invariant_cls=FunctionBodyInvariant,
        qualname="ParallelDisplay._render_titled_lines",
        expected_fragments=("_render_titled_lines body missing required literal",),
        edits=(
            (
                "strip_terminal_control(line), markup=False, highlight=False",
                "line, markup=False, highlight=False",
            ),
        ),
    ),
    _RegressionCase(
        name="_process_reader re-opens the child stdin to INHERIT",
        rel_path="agents/invoke/_process_reader.py",
        invariant_cls=FunctionBodyInvariant,
        qualname="_run_subprocess_and_read_lines",
        expected_fragments=("stdin=None",),
        edits=(("stdin=subprocess.DEVNULL,", "stdin=None,"),),
    ),
    _RegressionCase(
        name="_process_reader hands the terminal to the child",
        rel_path="agents/invoke/_process_reader.py",
        invariant_cls=FunctionBodyInvariant,
        qualname="_run_subprocess_and_read_lines",
        expected_fragments=("stdin=sys.stdin",),
        edits=(("stdin=subprocess.DEVNULL,", "stdin=sys.stdin,"),),
    ),
    _RegressionCase(
        name="subprocess_executor passes stdin=None to SpawnOptions",
        rel_path="agents/subprocess_executor.py",
        invariant_cls=CallSiteInvariant,
        callee_name="SpawnOptions",
        expected_fragments=("no SpawnOptions call passes",),
        edits=(("stdin=_DEVNULL,", "stdin=None,"),),
    ),
    _RegressionCase(
        name="SpawnOptions.stdin defaults back to None",
        rel_path="process/manager/_spawn_options.py",
        invariant_cls=Invariant,
        expected_fragments=("subprocess.DEVNULL",),
        edits=(
            (
                "stdin: int | None = subprocess.DEVNULL",
                "stdin: int | None = None",
            ),
        ),
    ),
    _RegressionCase(
        name="a new SpawnOptions call re-opts into INHERIT",
        rel_path="agents/invoke/_process_reader.py",
        invariant_cls=PackageWideCallSiteInvariant,
        callee_name="SpawnOptions",
        narrow_rel_path=False,
        narrow_package_files=True,
        expected_fragments=("stdin=None",),
        appended="\nSpawnOptions(stdin=None,)\n",
    ),
    _RegressionCase(
        name="configure_logging hands sys.stderr to loguru",
        rel_path="logging.py",
        invariant_cls=FunctionBodyInvariant,
        qualname="configure_logging",
        expected_fragments=("configure_logging", "sys.stderr"),
        edits=(
            (
                "sink = console_sink if console_sink is not None else make_stderr_log_sink()",
                "sink = sys.stderr",
            ),
            ("colorize=False,", "colorize=True,"),
        ),
    ),
    _RegressionCase(
        name="cli._configure_logging hands sys.stderr to loguru",
        rel_path="cli/main.py",
        invariant_cls=FunctionBodyInvariant,
        qualname="_configure_logging",
        expected_fragments=("_configure_logging", "sys.stderr"),
        edits=(
            (
                "sink = console_sink if console_sink is not None else make_stderr_log_sink()",
                "sink = None  # pretend we forgot to wire it",
            ),
            (
                'logger.add(sink, level="ERROR")',
                'logger.add(sys.stderr, level="ERROR")',
            ),
        ),
    ),
    _RegressionCase(
        name="cli.main drops the sanitizing log sink",
        rel_path="cli/main.py",
        invariant_cls=FunctionBodyInvariant,
        qualname="main",
        expected_fragments=("main", "make_sanitizing_log_sink"),
        edits=(
            (
                "configure_logging(verbosity, console_sink=make_sanitizing_log_sink(_cli_ctx))",
                "configure_logging(verbosity)",
            ),
        ),
    ),
    _RegressionCase(
        name="make_sanitizing_log_sink skips the stripper",
        rel_path="display/log_sink.py",
        invariant_cls=FunctionBodyInvariant,
        qualname="make_sanitizing_log_sink",
        expected_fragments=("make_sanitizing_log_sink",),
        edits=(
            (
                'cleaned = strip_terminal_control(message.rstrip("\\n"))',
                'cleaned = message.rstrip("\\n")',
            ),
        ),
    ),
    _RegressionCase(
        name="make_stderr_log_sink skips the stripper",
        rel_path="display/log_sink.py",
        invariant_cls=FunctionBodyInvariant,
        qualname="make_stderr_log_sink",
        expected_fragments=("make_stderr_log_sink",),
        edits=(
            (
                'cleaned = strip_terminal_control(message.rstrip("\\n"))',
                'cleaned = message.rstrip("\\n")',
            ),
        ),
    ),
    _RegressionCase(
        name="log_sink constructs a Console inline",
        rel_path="display/log_sink.py",
        invariant_cls=Invariant,
        expected_fragments=("Console(",),
        appended="\n_console = Console(file=sys.stderr)\n",
    ),
    _RegressionCase(
        name="spawn_pty_process drops os.setsid()",
        rel_path="process/pty.py",
        invariant_cls=FunctionBodyInvariant,
        qualname="spawn_pty_process",
        expected_fragments=("spawn_pty_process", "os.setsid()"),
        edits=(("os.setsid()", "_ = None  # nosetsid"),),
    ),
    _RegressionCase(
        name="line_sanitizer loses the strip_terminal_control helper",
        rel_path="display/line_sanitizer.py",
        invariant_cls=Invariant,
        expected_fragments=("missing required literal",),
        edits=(("def strip_terminal_control", "def _gone"),),
    ),
    _RegressionCase(
        name="_plain_constants re-introduces the SGR-only regex",
        rel_path="display/_plain_constants.py",
        invariant_cls=Invariant,
        expected_fragments=("[0-9;]*m",),
        appended="\n_LEGACY = re.compile(r'[0-9;]*m')\n",
    ),
    _RegressionCase(
        name="_pty_runner paints the terminal with tqdm",
        rel_path="agents/invoke/_pty_runner.py",
        invariant_cls=Invariant,
        expected_fragments=("file=sys.stdout",),
        appended="\ntqdm(..., file=sys.stdout)\n",
    ),
)

#: Line separators that Python's tokenizer treats as ordinary characters
#: inside a line even though ``str.splitlines`` breaks on them.
_UNICODE_SEPARATORS: tuple[str, ...] = (
    "",
    "\u2028",
    "\u2029",
    "\x0c",
    "\u0085",
)

#: ``(rel_path, qualname, required_literal)`` rows: the safe-restoration
#: literals whose removal must be caught by the audit.
_TERMINAL_RESTORE_REQUIRED_LITERALS: tuple[tuple[str, str | None, str], ...] = (
    ("display/terminal_restore.py", "terminal_restore_sequence", "?25h"),
    ("display/terminal_restore.py", "terminal_restore_sequence", "[0m"),
    ("display/terminal_restore.py", "restore_terminal_modes", "_STATE.saved_modes"),
    ("display/terminal_restore.py", "restore_terminal_modes", "termios.tcsetattr"),
    ("display/_terminal_bg_query.py", "_probe", "get_global_snapshot()"),
    ("display/_terminal_bg_query.py", "_probe", "parsed_color is None"),
    ("display/_terminal_bg_query.py", "_probe", "tcflush"),
    ("display/_terminal_bg_query.py", "_probe", "TCIFLUSH"),
    ("cli/main.py", "ensure_cli_terminal_restore", "SIGTERM"),
    ("cli/main.py", "ensure_cli_terminal_restore", "SIGHUP"),
    ("cli/main.py", "ensure_cli_terminal_restore", "SIGQUIT"),
    ("cli/main.py", "ensure_cli_terminal_restore", "install_sanitizing_excepthook"),
    ("cli/main.py", "ensure_cli_terminal_restore", "restore_terminal_modes"),
    ("display/excepthook.py", None, "strip_terminal_control"),
    ("display/excepthook.py", None, "restore_terminal"),
    ("display/terminal_restore.py", "restore_terminal", "terminal_understands_vt"),
    ("display/_terminal_bg_query.py", "_probe", "terminal_understands_vt"),
    ("display/context.py", "install_sigwinch_refresher", "refresh_in_progress"),
    ("display/context.py", "install_sigwinch_refresher", "signal.SIG_DFL"),
)

#: Literals the parent cleanup must never use: switching buffers, repainting
#: or repositioning the display, or discarding queued operator input.
_DESTRUCTIVE_CLEANUP_LITERALS: tuple[str, ...] = (
    "?1049l",
    "?1047l",
    "?47l",
    "[2J",
    "[3J",
    "[H",
    "[1;1H",
    "tcflush",
)

#: Every seam the audit must own, in the form ``rel_path::qualname``. The
#: count alone does not pin WHICH sinks are covered, so the audit's
#: invariant table is compared against this list seam by seam.
_EXPECTED_INVARIANT_SEAMS: tuple[str, ...] = (
    "MarkupParseInvariant",
    "NarrowCsiParameterInvariant",
    "PackageWideCallSiteInvariant",
    "agents/invoke/_process_reader.py::_run_subprocess_and_read_lines",
    "agents/invoke/_pty_line_reader.py",
    "agents/invoke/_pty_runner.py",
    "agents/subprocess_executor.py::SpawnOptions()",
    "cli/main.py::_configure_logging",
    "cli/main.py::ensure_cli_terminal_restore",
    "cli/main.py::main",
    "display/_plain_constants.py::_sanitize",
    "display/_plain_constants.py",
    "display/_terminal_bg_query.py::_probe",
    "display/activity_model.py::render_event_line",
    "display/context.py::install_sigwinch_refresher",
    "display/excepthook.py",
    "display/log_sink.py::make_sanitizing_log_sink",
    "display/log_sink.py::make_stderr_log_sink",
    "display/log_sink.py",
    "display/line_sanitizer.py::strip_markup_safe",
    "display/line_sanitizer.py",
    "display/parallel_display.py::ParallelDisplay._render_titled_lines",
    "display/parallel_display.py::ParallelDisplay.strip_markup",
    "display/parallel_display.py::_strip_markup",
    "display/parallel_display.py::emit_activity_line",
    "display/parallel_display.py",
    "display/status_bar.py::StatusBar._fallback_cleanup",
    "display/terminal_restore.py::restore_terminal",
    "display/terminal_restore.py::restore_terminal",
    "display/terminal_restore.py::restore_terminal_modes",
    "display/terminal_restore.py::terminal_restore_sequence",
    "interrupt/controller.py::InterruptController.force_exit",
    "logging.py::configure_logging",
    "process/manager/_spawn_options.py",
    "process/pty.py::spawn_pty_process",
)


def _seam_key(invariant: object) -> str:
    """Return the ``rel_path::qualname`` seam an invariant owns."""
    rel_path = getattr(invariant, "rel_path", None)
    if not isinstance(rel_path, str):
        return type(invariant).__name__
    qualname = getattr(invariant, "qualname", None)
    if isinstance(qualname, str):
        return f"{rel_path}::{qualname}"
    callee_name = getattr(invariant, "callee_name", None)
    if isinstance(callee_name, str):
        return f"{rel_path}::{callee_name}()"
    return rel_path


def test_audit_invariant_count_matches_table() -> None:
    """The audit pins every sink (file-level, function-body, and SpawnOptions call-site)."""
    # 22 escape-containment invariants (including the package-wide narrow
    # CSI ban) + 4 markup-parse containment invariants and 9 terminal
    # restoration wiring invariants, including the Status Bar fallback gate.
    expected = 35
    invariants = audit_module._INVARIANTS
    assert len(invariants) == expected

    # The count alone does not pin WHICH sinks are covered: a dropped or
    # duplicated invariant would keep the count at 35. The seam list below
    # names every sink the audit must own, so a sink cannot disappear (or
    # be replaced by a duplicate of another sink) without this test failing.
    seams = sorted(_seam_key(invariant) for invariant in invariants)
    assert seams == sorted(_EXPECTED_INVARIANT_SEAMS), (
        f"the audit must own exactly these seams; "
        f"missing={sorted(set(_EXPECTED_INVARIANT_SEAMS) - set(seams))} "
        f"unexpected={sorted(set(seams) - set(_EXPECTED_INVARIANT_SEAMS))} "
        f"counts={{expected: {len(_EXPECTED_INVARIANT_SEAMS)}, actual: {len(seams)}}}"
    )


def test_audit_line_sanitizer_invariant_pins_full_csi_class() -> None:
    """The line_sanitizer invariant pins the [0-?] class explicitly."""
    ls_invariant = next(
        inv
        for inv in audit_module._INVARIANTS
        if isinstance(inv, Invariant) and inv.rel_path == "display/line_sanitizer.py"
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
        audit_module,
        "_read",
        lambda rel_path: fake_source if rel_path == "display/line_sanitizer.py" else "",
    )
    # DA-001: the previous shape ``(fake_invariant, *audit_module._INVARIANTS[1:])``
    # kept every other invariant (including MarkupParseInvariant and
    # PackageWideCallSiteInvariant, both of which walk the entire package)
    # in the audit's iteration. The first such walk per pytest worker
    # process performs an uncached ``os.walk(_PACKAGE_ROOT)`` plus an AST
    # parse of every ``from_markup`` / ``SpawnOptions`` call site in
    # the tree; under xdist contention that cold walk exceeded the 1 s
    # SIGALRM cap and aborted the test. Narrowing the audit to ONLY the
    # targeted ``fake_invariant`` keeps the same exit-code and banner
    # assertions (``display/line_sanitizer.py`` is named by the targeted
    # violation, ``TERMINAL-ESCAPE-CONTAINMENT AUDIT FAILED`` is the
    # canonical banner) while paying zero package-walk cost. The other
    # invariants remain covered by their own adversarial tests below.
    monkeypatch.setattr(audit_module, "_INVARIANTS", (fake_invariant,))

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


def test_process_reader_stdin_regression_accepts_conditional_pipe_or_devnull() -> None:
    """S-3: prompt PIPE and non-prompt DEVNULL are both isolated child-stdin choices."""
    invariant = next(
        inv
        for inv in audit_module._INVARIANTS
        if isinstance(inv, FunctionBodyInvariant)
        and inv.rel_path == "agents/invoke/_process_reader.py"
        and inv.qualname == "_run_subprocess_and_read_lines"
    )

    assert invariant.violations() == []


# ---------------------------------------------------------------------------
# AST-scoped invariants: prove the audit inspects FUNCTION BODIES, not just
# whether the helper name appears anywhere in the file. Each test below
# strips the call from the wired sink and keeps the helper name present
# elsewhere (docstring, import, etc.) -- a whole-file literal check would
# still pass, the AST check must fail.
# ---------------------------------------------------------------------------


def _patch_rel(
    monkeypatch: pytest.MonkeyPatch,
    rel_path: str,
    transform: Callable[[str], str],
) -> None:
    """Patch the audit's source reader for ONE file.

    The reader always delegates to the REAL reader captured at import
    time, so patching several files in sequence never compounds the
    previous row's transformation.
    """
    cached_transformed: str | None = None

    def _read(rel_path_arg: str) -> str:
        nonlocal cached_transformed
        if rel_path_arg == rel_path:
            if cached_transformed is None:
                cached_transformed = transform(_REAL_READ(rel_path_arg))
            return cached_transformed
        return _REAL_READ(rel_path_arg)

    monkeypatch.setattr(audit_module, "_read", _read)


def _narrow_invariants(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rel_path: str | None = None,
    qualname: str | None = None,
    callee_name: str | None = None,
    invariant_cls: type | None = None,
) -> None:
    """Replace ``_INVARIANTS`` with the single invariant matching the given filters.

    Each adversarial test drives exactly one regression; running the full
    35-invariant sweep inside every adversarial test costs ~10-15 s of
    per-shard wall time because the two package-wide invariants re-scan
    the entire package on every call. Narrowing the audit's invariant
    list to the targeted invariant keeps the same banner / assertion
    shape but executes only the seam under test.

    The filter always runs over the FULL invariant table captured at
    import time, so a table-driven test that narrows once per row never
    narrows an already-narrowed table.
    """
    matching = []
    for inv in _REAL_INVARIANTS:
        if rel_path is not None and getattr(inv, "rel_path", None) != rel_path:
            continue
        if qualname is not None and getattr(inv, "qualname", None) != qualname:
            continue
        if callee_name is not None and getattr(inv, "callee_name", None) != callee_name:
            continue
        if invariant_cls is not None and not isinstance(inv, invariant_cls):
            continue
        matching.append(inv)
    if not matching:
        raise RuntimeError(
            f"No invariant matched filters: "
            f"rel_path={rel_path!r} qualname={qualname!r} "
            f"callee_name={callee_name!r} invariant_cls={invariant_cls!r}"
        )
    monkeypatch.setattr(audit_module, "_INVARIANTS", tuple(matching))


def _apply_case_edits(case: _RegressionCase, source: str) -> str:
    """Apply one regression row's literal replacements and append to the source."""
    for old, new in case.edits:
        assert old in source, (
            f"{case.name}: {case.rel_path!r} must contain {old!r} for the "
            f"regression to be injectable"
        )
        source = source.replace(old, new)
    if case.appended:
        source += case.appended
    return source


def _narrow_to_case(monkeypatch: pytest.MonkeyPatch, case: _RegressionCase) -> None:
    """Narrow the audit to the invariant that owns the row's seam."""
    _narrow_invariants(
        monkeypatch,
        rel_path=case.rel_path if case.narrow_rel_path else None,
        qualname=case.qualname,
        callee_name=case.callee_name,
        invariant_cls=case.invariant_cls,
    )
    if case.narrow_package_files:
        # This case exercises the driver's violation rendering for an
        # injected package member. The invariant's package-walk behavior
        # is covered below with a synthetic path list; avoiding a full
        # source-tree rescan here keeps this adversarial test
        # deterministic under xdist I/O.
        monkeypatch.setattr(
            audit_module.PackageWideCallSiteInvariant,
            "_package_files",
            classmethod(lambda _cls, _path=case.rel_path: [_path]),
        )


@pytest.mark.timeout_seconds(5)
def test_audit_blocks_each_injected_regression(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every adversarial regression must make the audit fail and say why.

    Each row injects ONE regression into ONE production file, narrows the
    audit to the invariant that owns that seam, and asserts the audit
    exits 1 while naming the patched file and the offending literal.
    """
    for case in _REGRESSION_CASES:
        _patch_rel(monkeypatch, case.rel_path, partial(_apply_case_edits, case))
        _narrow_to_case(monkeypatch, case)

        rc = audit_main([])
        captured = capsys.readouterr()

        assert rc == 1, f"{case.name}: the audit must exit 1 for the injected regression"
        assert case.rel_path in captured.out, (
            f"{case.name}: the audit output must name {case.rel_path!r}; got {captured.out!r}"
        )
        for fragment in case.expected_fragments:
            assert fragment in captured.out, (
                f"{case.name}: the audit output must name {fragment!r}; got {captured.out!r}"
            )


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
    assert any(
        "not found" in v or "no SpawnOptions" in v.lower() or "call site" in v for v in violations
    ), violations


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
# Each adversarial case is one row of ``_REGRESSION_CASES`` above: it
# monkeypatches the source via _patch_rel, runs the audit, and asserts
# it exits 1 and prints the relevant violation banner.
# ---------------------------------------------------------------------------


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
    monkeypatch.setattr(
        audit_module.PackageWideCallSiteInvariant,
        "_package_files",
        classmethod(lambda _cls: ["fake.py"]),
    )

    violations = inv.violations()
    # We don't assert on the absolute number (depends on file count), only
    # that AT LEAST ONE violation names the offending literal.
    assert any("stdin=None" in v for v in violations), (
        f"PackageWideCallSiteInvariant must flag stdin=None violations; got {violations!r}"
    )

    # Reset the cache so other tests see the real source again.
    audit_module.PackageWideCallSiteInvariant.reset_cache()


@pytest.mark.timeout_seconds(5)
def test_package_wide_call_site_invariant_flags_stdin_none_despite_unicode_separators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line-splitting must match Python AST line semantics for every separator.

    ``str.splitlines`` also breaks on U+2028/U+2029, NEL, and form feed,
    but Python's tokenizer does not: a call preceded by such a character
    is on AST line 3 while a naive split puts it on line 4, so extraction
    silently missed the forbidden literal. The audit must flag it for all
    of them.
    """
    for separator in _UNICODE_SEPARATORS:
        fake_source = f'x = "a{separator}"\nSpawnOptions(stdin=None,)\n'
        inv = PackageWideCallSiteInvariant(
            callee_name="SpawnOptions",
            absent=("stdin=None",),
        )

        monkeypatch.setattr(audit_module, "_read", lambda rel_path, source=fake_source: source)
        monkeypatch.setattr(
            audit_module.PackageWideCallSiteInvariant,
            "_package_files",
            classmethod(lambda _cls: ["fake.py"]),
        )

        violations = inv.violations()
        assert any("stdin=None" in v for v in violations), (
            f"audit missed stdin=None after separator {separator!r}; got {violations!r}"
        )

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


def _remove_literal(source: str, literal: str) -> str:
    """Return the source with every occurrence of ``literal`` removed."""
    assert literal in source, (
        f"the source must contain {literal!r} for this regression to be injectable"
    )
    return source.replace(literal, "REMOVED")


def _replace_literal(source: str, old: str, new: str) -> str:
    """Return the source with the FIRST occurrence of ``old`` replaced by ``new``."""
    assert old in source, (
        f"the source must contain {old!r} for this regression to be injectable"
    )
    return source.replace(old, new, 1)


@pytest.mark.timeout_seconds(5)
def test_terminal_restore_audit_regression_rejects_missing_safe_restoration(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S-3: each owned restore or probe-timeout operation remains load-bearing."""
    for rel_path, qualname, required_literal in _TERMINAL_RESTORE_REQUIRED_LITERALS:
        _patch_rel(monkeypatch, rel_path, partial(_remove_literal, literal=required_literal))
        _narrow_invariants(
            monkeypatch,
            rel_path=rel_path,
            qualname=qualname,
            invariant_cls=FunctionBodyInvariant if qualname is not None else Invariant,
        )

        assert audit_main([]) == 1, (
            f"{rel_path} {qualname!r} must fail the audit when {required_literal!r} is removed"
        )
        assert required_literal in capsys.readouterr().out


def _destructive_cleanup_edit(forbidden_literal: str) -> tuple[str, str, str]:
    """Return the ``(qualname, old_literal, new_literal)`` edit for a forbidden literal."""
    if forbidden_literal == "tcflush":
        return (
            "restore_terminal",
            "target_fd = _fd_of(target_stream)",
            "termios.tcflush(0, termios.TCIFLUSH)\n        target_fd = _fd_of(target_stream)",
        )
    return (
        "terminal_restore_sequence",
        'return "\\x1b[?25h\\x1b[0m"',
        f'return "\\x1b[?25h\\x1b[0m{forbidden_literal}"',
    )


@pytest.mark.timeout_seconds(5)
def test_terminal_restore_audit_regression_rejects_destructive_parent_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S-3: parent cleanup may not switch buffers, repaint, reposition, or flush input."""
    path = "display/terminal_restore.py"

    for forbidden_literal in _DESTRUCTIVE_CLEANUP_LITERALS:
        qualname, old_literal, new_literal = _destructive_cleanup_edit(forbidden_literal)

        _patch_rel(
            monkeypatch,
            path,
            partial(_replace_literal, old=old_literal, new=new_literal),
        )
        _narrow_invariants(
            monkeypatch,
            rel_path=path,
            qualname=qualname,
            invariant_cls=FunctionBodyInvariant,
        )

        assert audit_main([]) == 1, f"{forbidden_literal!r} must fail the audit in {qualname!r}"
        assert forbidden_literal in capsys.readouterr().out
