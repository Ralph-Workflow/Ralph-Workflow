from __future__ import annotations

import ast
import os
import sys
from collections import OrderedDict
from pathlib import Path


def _split_python_lines(source: str) -> list[str]:
    lines = [part + "\n" for part in source.split("\n")[:-1]]
    last = source.rsplit("\n", 1)[-1]
    if last:
        lines.append(last)
    return lines


_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_READ_CACHE_LIMIT = 2048
_PARSE_CACHE_LIMIT = 2048
_READ_CACHE: OrderedDict[str, str] = OrderedDict()
_PARSE_CACHE: OrderedDict[str, ast.Module] = OrderedDict()
_LINES_CACHE: OrderedDict[str, list[str]] = OrderedDict()
_BODY_CACHE: OrderedDict[tuple[str, str], str | None] = OrderedDict()


def _read(rel_path: str) -> str:
    cached = _READ_CACHE.get(rel_path)
    if cached is not None:
        _READ_CACHE.move_to_end(rel_path)
        return cached
    source = (_PACKAGE_ROOT / rel_path).read_text(encoding="utf-8")
    _READ_CACHE[rel_path] = source
    if len(_READ_CACHE) > _READ_CACHE_LIMIT:
        _READ_CACHE.popitem(last=False)
    return source


def _parse_source(source: str, filename: str = "<unknown>") -> ast.Module:
    cached = _PARSE_CACHE.get(source)
    if cached is not None:
        _PARSE_CACHE.move_to_end(source)
        return cached
    tree = ast.parse(source, filename=filename)
    _PARSE_CACHE[source] = tree
    if len(_PARSE_CACHE) > _PARSE_CACHE_LIMIT:
        _PARSE_CACHE.popitem(last=False)
    return tree


def _split_lines(source: str) -> list[str]:
    cached = _LINES_CACHE.get(source)
    if cached is not None:
        _LINES_CACHE.move_to_end(source)
        return cached
    lines = _split_python_lines(source)
    _LINES_CACHE[source] = lines
    if len(_LINES_CACHE) > _PARSE_CACHE_LIMIT:
        _LINES_CACHE.popitem(last=False)
    return lines


def _get_source_segment_from_lines(lines: list[str], node: ast.AST) -> str | None:
    lineno_val: object = getattr(node, "lineno", None)
    end_lineno_val: object = getattr(node, "end_lineno", None)
    col_offset_val: object = getattr(node, "col_offset", None)
    end_col_offset_val: object = getattr(node, "end_col_offset", None)
    if (
        not isinstance(lineno_val, int)
        or not isinstance(end_lineno_val, int)
        or not isinstance(col_offset_val, int)
        or not isinstance(end_col_offset_val, int)
    ):
        return None

    lineno: int = lineno_val - 1
    end_lineno: int = end_lineno_val - 1
    col_offset: int = col_offset_val
    end_col_offset: int = end_col_offset_val

    if lineno < 0 or end_lineno >= len(lines) or lineno > end_lineno:
        return None

    try:
        if end_lineno == lineno:
            return (
                lines[lineno].encode()[col_offset:end_col_offset].decode("utf-8", errors="replace")
            )

        first = lines[lineno].encode()[col_offset:].decode("utf-8", errors="replace")
        last = lines[end_lineno].encode()[:end_col_offset].decode("utf-8", errors="replace")
        mid = lines[lineno + 1 : end_lineno]
        return first + "".join(mid) + last
    except Exception:
        return None


def _get_source_segment(source: str, node: ast.AST) -> str | None:
    return _get_source_segment_from_lines(_split_lines(source), node)


def _function_body(rel_path: str, *, qualname: str) -> str | None:
    source = _read(rel_path)
    cache_key = (source, qualname)
    cached_body = _BODY_CACHE.get(cache_key)
    if cached_body is not None:
        _BODY_CACHE.move_to_end(cache_key)
        return cached_body

    tree = _parse_source(source, rel_path)
    parts = qualname.split(".")
    target_name = parts[-1]

    def _walk(node: ast.AST, depth: int) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        for child in ast.iter_child_nodes(node):
            if (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name == target_name
            ):
                if depth == len(parts) - 1:
                    return child
                if depth < len(parts) - 1:
                    inner = _walk(child, depth + 1)
                    if inner is not None:
                        return inner
            elif isinstance(child, ast.ClassDef) and child.name == parts[depth]:
                if depth < len(parts) - 1:
                    return _walk(child, depth + 1)
                for item in child.body:
                    if (
                        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name == target_name
                    ):
                        return item
        return None

    func = _walk(tree, 0)
    if func is None:
        return None
    segment = _get_source_segment(source, func)
    body = segment if segment is not None else ""
    _BODY_CACHE[cache_key] = body
    if len(_BODY_CACHE) > _PARSE_CACHE_LIMIT:
        _BODY_CACHE.popitem(last=False)
    return body


def _call_site_sources(rel_path: str, *, callee_name: str) -> list[str]:
    source = _read(rel_path)
    tree = _parse_source(source, rel_path)
    found: list[str] = []
    lines = _split_lines(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        match = (isinstance(func, ast.Name) and func.id == callee_name) or (
            isinstance(func, ast.Attribute) and func.attr == callee_name
        )
        if not match:
            continue
        segment = _get_source_segment_from_lines(lines, node)
        if segment is not None:
            found.append(segment)
    return found


def _check_literal(
    rel_path: str,
    *,
    needle: str,
    present: bool,
    scope: str,
) -> list[str]:
    label = "missing required literal" if present else "forbidden literal still present"
    return [f"{rel_path}: {scope}: {label} {needle!r}"]


class Invariant:
    def __init__(
        self,
        *,
        rel_path: str,
        present: tuple[str, ...] = (),
        absent: tuple[str, ...] = (),
    ) -> None:
        self.rel_path = rel_path
        self.present = present
        self.absent = absent

    def violations(self) -> list[str]:
        content = _read(self.rel_path)
        return [
            *(
                f"{self.rel_path}: missing required literal {needle!r}"
                for needle in self.present
                if needle not in content
            ),
            *(
                f"{self.rel_path}: forbidden literal still present {needle!r}"
                for needle in self.absent
                if needle in content
            ),
        ]


class FunctionBodyInvariant:
    def __init__(
        self,
        *,
        rel_path: str,
        qualname: str,
        present: tuple[str, ...] = (),
        absent: tuple[str, ...] = (),
        min_counts: dict[str, int] | None = None,
    ) -> None:
        self.rel_path = rel_path
        self.qualname = qualname
        self.present = present
        self.absent = absent
        self.min_counts = min_counts or {}

    def violations(self) -> list[str]:
        body = _function_body(self.rel_path, qualname=self.qualname)
        if body is None:
            return [f"{self.rel_path}: target function {self.qualname!r} not found"]
        problems: list[str] = []
        problems.extend(
            f"{self.rel_path}: {self.qualname} body missing required literal {needle!r}"
            for needle in self.present
            if needle not in body
        )
        problems.extend(
            f"{self.rel_path}: {self.qualname} body carries forbidden literal {needle!r}"
            for needle in self.absent
            if needle in body
        )
        for needle, min_count in self.min_counts.items():
            actual = body.count(needle)
            if actual < min_count:
                problems.append(
                    f"{self.rel_path}: {self.qualname} body has {actual} occurrence(s) "
                    f"of {needle!r}, minimum required is {min_count}"
                )
        return problems


class CallSiteInvariant:
    def __init__(
        self,
        *,
        rel_path: str,
        callee_name: str,
        present: tuple[str, ...] = (),
        absent: tuple[str, ...] = (),
        require_any: bool = True,
    ) -> None:
        self.rel_path = rel_path
        self.callee_name = callee_name
        self.present = present
        self.absent = absent
        self.require_any = require_any

    def violations(self) -> list[str]:
        call_sources = _call_site_sources(self.rel_path, callee_name=self.callee_name)
        if not call_sources:
            return [f"{self.rel_path}: no {self.callee_name!r} call site found"]
        problems: list[str] = []
        if self.require_any:
            problems.extend(
                f"{self.rel_path}: no {self.callee_name} call passes {needle!r}"
                for needle in self.present
                if not any(needle in segment for segment in call_sources)
            )
        else:
            joined = "\n".join(call_sources)
            problems.extend(
                f"{self.rel_path}: {self.callee_name} calls missing required literal {needle!r}"
                for needle in self.present
                if needle not in joined
            )
        problems.extend(
            f"{self.rel_path}: {self.callee_name} call carries forbidden literal {needle!r}"
            for needle in self.absent
            if any(needle in segment for segment in call_sources)
        )
        return problems


class NarrowCsiParameterInvariant:
    def violations(self) -> list[str]:
        needle = "[0-" + "9;?]"
        problems: list[str] = []
        for path in _PACKAGE_ROOT.rglob("*.py"):
            rel_path = path.relative_to(_PACKAGE_ROOT).as_posix()
            if rel_path == Path(__file__).relative_to(_PACKAGE_ROOT).as_posix():
                continue
            if needle in _read(rel_path):
                problems.append(f"{rel_path}: forbidden narrow CSI parameter class {needle!r}")
        return problems


class PackageWideCallSiteInvariant:
    _PACKAGE_FILE_LIST_CACHE: list[str] | None = None

    def __init__(
        self,
        *,
        callee_name: str,
        absent: tuple[str, ...] = (),
        present: tuple[str, ...] = (),
    ) -> None:
        self.callee_name = callee_name
        self.absent = absent
        self.present = present

    @classmethod
    def _package_files(cls) -> list[str]:
        if cls._PACKAGE_FILE_LIST_CACHE is None:
            cls._PACKAGE_FILE_LIST_CACHE = sorted(
                str((Path(root) / name).relative_to(_PACKAGE_ROOT).as_posix())
                for root, _dirs, files in os.walk(_PACKAGE_ROOT)
                for name in files
                if name.endswith(".py")
            )
        return cls._PACKAGE_FILE_LIST_CACHE

    @classmethod
    def reset_cache(cls) -> None:
        cls._PACKAGE_FILE_LIST_CACHE = None

    def violations(self) -> list[str]:
        problems: list[str] = []
        callee_token = self.callee_name
        for rel_path in self._package_files():
            try:
                source = _read(rel_path)
            except OSError:
                continue
            if callee_token not in source:
                continue
            if (
                self.absent
                and not self.present
                and not any(needle in source for needle in self.absent)
            ):
                continue
            try:
                tree = _parse_source(source, rel_path)
            except SyntaxError:
                continue
            lines = _split_lines(source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                match = (isinstance(func, ast.Name) and func.id == self.callee_name) or (
                    isinstance(func, ast.Attribute) and func.attr == self.callee_name
                )
                if not match:
                    continue
                segment = _get_source_segment_from_lines(lines, node)
                if segment is None:
                    continue
                snippet = segment.replace("\n", " ")
                problems.extend(
                    f"{rel_path}:{node.lineno}: {self.callee_name}() call "
                    f"carries forbidden literal {needle!r}: {snippet}"
                    for needle in self.absent
                    if needle in segment
                )
                problems.extend(
                    f"{rel_path}:{node.lineno}: {self.callee_name}() call "
                    f"missing required literal {needle!r}: {snippet}"
                    for needle in self.present
                    if needle not in segment
                )
        return problems


_MARKUP_PARSE_ALLOWLIST: dict[str, str] = {
    "display/line_sanitizer.py": "the guarded choke point itself",
    "cli/commands/contribute.py": "static author-written banner literals",
}


class MarkupParseInvariant:
    _PACKAGE_FILE_LIST_CACHE: list[str] | None = None

    def __init__(self, *, callee_name: str = "from_markup") -> None:
        self.callee_name = callee_name

    @classmethod
    def _package_files(cls) -> list[str]:
        if cls._PACKAGE_FILE_LIST_CACHE is None:
            cls._PACKAGE_FILE_LIST_CACHE = sorted(
                str(p.relative_to(_PACKAGE_ROOT).as_posix()) for p in _PACKAGE_ROOT.rglob("*.py")
            )
        return cls._PACKAGE_FILE_LIST_CACHE

    @classmethod
    def reset_cache(cls) -> None:
        cls._PACKAGE_FILE_LIST_CACHE = None

    @staticmethod
    def _is_string_literal(node: ast.expr) -> bool:
        return isinstance(node, ast.Constant) and isinstance(node.value, str)

    def violations(self) -> list[str]:
        problems: list[str] = []
        for rel_path in self._package_files():
            if rel_path in _MARKUP_PARSE_ALLOWLIST:
                continue
            try:
                source = _read(rel_path)
            except OSError:
                continue
            if self.callee_name not in source:
                continue
            try:
                tree = _parse_source(source, rel_path)
            except SyntaxError:
                continue
            lines = _split_lines(source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                match = (isinstance(func, ast.Name) and func.id == self.callee_name) or (
                    isinstance(func, ast.Attribute) and func.attr == self.callee_name
                )
                if not match:
                    continue
                if node.args and self._is_string_literal(node.args[0]):
                    continue
                segment = _get_source_segment_from_lines(lines, node) or self.callee_name
                snippet = " ".join(segment.split())[:120]
                problems.append(
                    f"{rel_path}:{node.lineno}: unguarded {self.callee_name}() on "
                    f"non-literal text: {snippet} -- route it through "
                    f"ralph.display.line_sanitizer.strip_markup_safe"
                )
        return problems


def logging_configurator_violations() -> list[str]:
    return [
        violation
        for invariant in _INVARIANTS
        if isinstance(invariant, (Invariant, FunctionBodyInvariant, CallSiteInvariant))
        and invariant.rel_path in {"logging.py", "cli/main.py"}
        for violation in invariant.violations()
    ]


_INVARIANTS: tuple[
    Invariant
    | NarrowCsiParameterInvariant
    | FunctionBodyInvariant
    | CallSiteInvariant
    | PackageWideCallSiteInvariant
    | MarkupParseInvariant,
    ...,
] = (
    Invariant(
        rel_path="display/line_sanitizer.py",
        present=(
            "def strip_terminal_control",
            "[0-?]",
        ),
        absent=("[0-" + "9;?]",),
    ),
    NarrowCsiParameterInvariant(),
    FunctionBodyInvariant(
        rel_path="display/line_sanitizer.py",
        qualname="strip_markup_safe",
        present=(
            "Text.from_markup(text)",
            "except Exception:",
            "strip_terminal_control(plain)",
        ),
        absent=(
            "except ValueError",
            "except (ValueError",
        ),
    ),
    Invariant(
        rel_path="display/_plain_constants.py",
        present=("strip_markup_safe",),
        absent=("[0-9;]*m",),
    ),
    FunctionBodyInvariant(
        rel_path="display/_plain_constants.py",
        qualname="_sanitize",
        present=("strip_markup_safe(text)",),
        absent=("from_markup",),
    ),
    FunctionBodyInvariant(
        rel_path="display/parallel_display.py",
        qualname="_strip_markup",
        present=("strip_markup_safe(line)",),
        absent=("from_markup",),
    ),
    MarkupParseInvariant(),
    Invariant(
        rel_path="display/parallel_display.py",
        present=("markup=False",),
        absent=(
            "_ANSI_ESCAPE",
            "console.print(line)",
        ),
    ),
    FunctionBodyInvariant(
        rel_path="display/parallel_display.py",
        qualname="ParallelDisplay.strip_markup",
        present=("strip_terminal_control(line)",),
    ),
    FunctionBodyInvariant(
        rel_path="display/parallel_display.py",
        qualname="ParallelDisplay._render_titled_lines",
        present=(
            "strip_terminal_control(line)",
            "markup=False",
        ),
    ),
    FunctionBodyInvariant(
        rel_path="display/parallel_display.py",
        qualname="emit_activity_line",
        present=("markup=False",),
        min_counts={"_sanitize(line)": 2},
    ),
    FunctionBodyInvariant(
        rel_path="display/activity_model.py",
        qualname="render_event_line",
        present=("strip_terminal_control(content or",),
    ),
    Invariant(
        rel_path="agents/invoke/_pty_runner.py",
        absent=(
            "file=sys.stdout",
            "tqdm(",
        ),
    ),
    FunctionBodyInvariant(
        rel_path="agents/invoke/_process_reader.py",
        qualname="_run_subprocess_and_read_lines",
        present=("stdin=subprocess.PIPE", "stdin=subprocess.DEVNULL"),
        absent=("stdin=None", "stdin=sys.stdin"),
    ),
    CallSiteInvariant(
        rel_path="agents/subprocess_executor.py",
        callee_name="SpawnOptions",
        present=("stdin=_DEVNULL",),
    ),
    Invariant(
        rel_path="agents/invoke/_pty_line_reader.py",
        present=("yield queued_line",),
    ),
    Invariant(
        rel_path="process/manager/_spawn_options.py",
        present=("import subprocess", "stdin: int | None = subprocess.DEVNULL"),
        absent=("stdin: int | None = None",),
    ),
    PackageWideCallSiteInvariant(
        callee_name="SpawnOptions",
        absent=("stdin=None",),
    ),
    FunctionBodyInvariant(
        rel_path="logging.py",
        qualname="configure_logging",
        present=("console_sink", "make_stderr_log_sink"),
        absent=("sys.stderr", "colorize=True"),
    ),
    FunctionBodyInvariant(
        rel_path="cli/main.py",
        qualname="_configure_logging",
        present=("console_sink", "make_stderr_log_sink"),
        absent=("sys.stderr",),
    ),
    FunctionBodyInvariant(
        rel_path="cli/main.py",
        qualname="main",
        present=("make_sanitizing_log_sink",),
    ),
    FunctionBodyInvariant(
        rel_path="display/log_sink.py",
        qualname="make_sanitizing_log_sink",
        present=("strip_terminal_control", "markup=False", "highlight=False"),
    ),
    FunctionBodyInvariant(
        rel_path="display/log_sink.py",
        qualname="make_stderr_log_sink",
        present=("strip_terminal_control",),
    ),
    Invariant(
        rel_path="display/log_sink.py",
        absent=("Console" + "(",),
    ),
    FunctionBodyInvariant(
        rel_path="process/pty.py",
        qualname="spawn_pty_process",
        present=("os.setsid()", "TIOCSCTTY"),
    ),
    FunctionBodyInvariant(
        rel_path="display/terminal_restore.py",
        qualname="terminal_restore_sequence",
        present=("?25h", "[0m"),
        absent=("?1049l", "?1047l", "?47l", "[2J", "[3J", "[H", "[1;1H"),
    ),
    FunctionBodyInvariant(
        rel_path="display/terminal_restore.py",
        qualname="restore_terminal_modes",
        present=("_STATE.saved_modes", "termios.tcsetattr"),
    ),
    FunctionBodyInvariant(
        rel_path="display/terminal_restore.py",
        qualname="restore_terminal",
        absent=("tcflush",),
    ),
    FunctionBodyInvariant(
        rel_path="display/status_bar.py",
        qualname="StatusBar._fallback_cleanup",
        present=("_real_tty", "terminal_understands_vt"),
    ),
    Invariant(
        rel_path="display/excepthook.py",
        present=("strip_terminal_control", "restore_terminal"),
    ),
    FunctionBodyInvariant(
        rel_path="interrupt/controller.py",
        qualname="InterruptController.force_exit",
        present=(
            "restore = self.restore_terminal or _restore_terminal",
            "restore()",
            "hard_exit(",
        ),
    ),
    FunctionBodyInvariant(
        rel_path="cli/main.py",
        qualname="ensure_cli_terminal_restore",
        present=(
            "atexit",
            "snapshot_terminal_modes",
            "restore_terminal_modes",
            "SIGTERM",
            "SIGHUP",
            "SIGQUIT",
            "install_sanitizing_excepthook",
        ),
    ),
    FunctionBodyInvariant(
        rel_path="display/_terminal_bg_query.py",
        qualname="_probe",
        present=(
            "get_global_snapshot()",
            "set_global_snapshot(original)",
            "terminal_understands_vt",
            "parsed_color is None",
            "tcflush",
            "TCIFLUSH",
            "tty.setraw",
        ),
        absent=("set_global_snapshot(None)",),
    ),
    FunctionBodyInvariant(
        rel_path="display/terminal_restore.py",
        qualname="restore_terminal",
        present=("terminal_understands_vt", "restore_terminal_modes"),
    ),
    FunctionBodyInvariant(
        rel_path="display/context.py",
        qualname="install_sigwinch_refresher",
        present=("refresh_in_progress", "except Exception:", "signal.SIG_DFL"),
    ),
)


def main(argv: list[str] | None = None) -> int:
    del argv
    problems: list[str] = []
    for invariant in _INVARIANTS:
        problems.extend(invariant.violations())

    if problems:
        print(f"TERMINAL-ESCAPE-CONTAINMENT AUDIT FAILED: {len(problems)} invariant violation(s)")
        print("=" * 72)
        for line in problems:
            print(f"  {line}")
        print()
        print(
            "The terminal-escape containment contract from the wt-036 rework "
            "is not satisfied. Re-read the rework plan and restore the "
            "missing/forbidden literals -- do NOT weaken this audit."
        )
        return 1

    print(
        "All terminal-escape containment invariants OK: "
        "line_sanitizer has strip_terminal_control with the full CSI class and "
        "no production narrow parameter regex; the totally-guarded strip_markup_safe choke point "
        "(no except-ValueError narrowing); _sanitize and _strip_markup "
        "delegate to it and no from_markup call anywhere under ralph/ "
        "takes non-literal text outside the allowlist; "
        "_plain_constants no longer carries the SGR-only regex; "
        "parallel_display.strip_markup / _render_titled_lines and the "
        "module-level emit_activity_line delegate to "
        "strip_terminal_control with markup=False (no _ANSI_ESCAPE, no "
        "unsanitized console.print(line)); activity_model.render_event_line "
        "calls strip_terminal_control before truncation; _pty_runner "
        "dropped tqdm + file=sys.stdout; _process_reader uses PIPE only for "
        "prompt delivery and DEVNULL otherwise, while subprocess_executor "
        "passes DEVNULL (no stdin=None/sys.stdin INHERIT); _pty_line_reader "
        "still yields raw VT text. SpawnOptions.stdin defaults to subprocess.DEVNULL and no "
        "SpawnOptions call anywhere under ralph/ passes stdin=None. "
        "ralph.logging.configure_logging AND ralph.cli.main._configure_logging "
        "route through an injected console_sink (no raw sys.stderr, "
        "colorize=False); the CLI call site wires make_sanitizing_log_sink "
        "for the DisplayContext Console. Both log_sink factories call "
        "strip_terminal_control with markup=False / highlight=False and "
        "no raw Console construction. display.excepthook strips crash output "
        "and restores the terminal. Parent restoration shows the cursor, resets "
        "SGR, restores saved termios, and avoids buffer/display/input destruction; "
        "the OSC 11 probe retains its owned timeout TCIFLUSH. "
        "ralph.process.pty.spawn_pty_process still calls os.setsid() + TIOCSCTTY."
    )
    return 0


PackageWideCallSiteInvariant._package_files()
MarkupParseInvariant._package_files()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
