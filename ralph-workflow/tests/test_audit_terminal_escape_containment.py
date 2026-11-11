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


_REAL_READ = audit_module._read

_REAL_INVARIANTS = audit_module._INVARIANTS


class _RegressionCase(NamedTuple):
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

_UNICODE_SEPARATORS: tuple[str, ...] = (
    "",
    "\u2028",
    "\u2029",
    "\x0c",
    "\u0085",
)

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
    expected = 35
    invariants = audit_module._INVARIANTS
    assert len(invariants) == expected

    seams = sorted(_seam_key(invariant) for invariant in invariants)
    assert seams == sorted(_EXPECTED_INVARIANT_SEAMS), (
        f"the audit must own exactly these seams; "
        f"missing={sorted(set(_EXPECTED_INVARIANT_SEAMS) - set(seams))} "
        f"unexpected={sorted(set(seams) - set(_EXPECTED_INVARIANT_SEAMS))} "
        f"counts={{expected: {len(_EXPECTED_INVARIANT_SEAMS)}, actual: {len(seams)}}}"
    )


def test_audit_line_sanitizer_invariant_pins_full_csi_class() -> None:
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
    invariant = next(
        inv
        for inv in audit_module._INVARIANTS
        if isinstance(inv, FunctionBodyInvariant)
        and inv.rel_path == "agents/invoke/_process_reader.py"
        and inv.qualname == "_run_subprocess_and_read_lines"
    )

    assert invariant.violations() == []


def _patch_rel(
    monkeypatch: pytest.MonkeyPatch,
    rel_path: str,
    transform: Callable[[str], str],
) -> None:
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
    _narrow_invariants(
        monkeypatch,
        rel_path=case.rel_path if case.narrow_rel_path else None,
        qualname=case.qualname,
        callee_name=case.callee_name,
        invariant_cls=case.invariant_cls,
    )
    if case.narrow_package_files:
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
    inv = FunctionBodyInvariant(
        rel_path="display/parallel_display.py",
        qualname="ParallelDisplay.does_not_exist",
        present=("strip_terminal_control",),
    )
    monkeypatch.setattr(audit_module, "_read", lambda rel_path: "")
    violations = inv.violations()
    assert any("not found" in v for v in violations), violations


def test_call_site_invariant_reports_missing_callee() -> None:
    inv = CallSiteInvariant(
        rel_path="agents/invoke/_process_reader.py",
        callee_name="NoSuchCallable",
        present=("stdin=DEVNULL",),
    )
    violations = inv.violations()
    assert any(
        "not found" in v or "no SpawnOptions" in v.lower() or "call site" in v for v in violations
    ), violations


def test_package_wide_call_site_invariant_flags_stdin_none_in_any_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_source = "SpawnOptions(stdin=None,)\n"
    inv = PackageWideCallSiteInvariant(
        callee_name="SpawnOptions",
        absent=("stdin=None",),
    )

    monkeypatch.setattr(audit_module, "_read", lambda rel_path: fake_source)
    monkeypatch.setattr(
        audit_module.PackageWideCallSiteInvariant,
        "_package_files",
        classmethod(lambda _cls: ["fake.py"]),
    )

    violations = inv.violations()
    assert any("stdin=None" in v for v in violations), (
        f"PackageWideCallSiteInvariant must flag stdin=None violations; got {violations!r}"
    )

    audit_module.PackageWideCallSiteInvariant.reset_cache()


@pytest.mark.timeout_seconds(5)
def test_package_wide_call_site_invariant_flags_stdin_none_despite_unicode_separators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    inv = PackageWideCallSiteInvariant(
        callee_name="SpawnOptions",
        absent=("stdin=None",),
    )

    monkeypatch.setattr(audit_module, "_read", lambda rel_path: "")
    violations = inv.violations()
    assert violations == [], (
        f"PackageWideCallSiteInvariant must return no violations on an empty "
        f"package; got {violations!r}"
    )


def _remove_literal(source: str, literal: str) -> str:
    assert literal in source, (
        f"the source must contain {literal!r} for this regression to be injectable"
    )
    return source.replace(literal, "REMOVED")


def _replace_literal(source: str, old: str, new: str) -> str:
    assert old in source, f"the source must contain {old!r} for this regression to be injectable"
    return source.replace(old, new, 1)


@pytest.mark.timeout_seconds(5)
def test_terminal_restore_audit_regression_rejects_missing_safe_restoration(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
