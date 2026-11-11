from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


_LOGGING_MODULE: str = "logging.py"

_LOGGER_ADD_MARKER: str = "logger.add("

_BUFFERED_FILE_SINK_HELPER_MARKER: str = "def _add_buffered_file_sink"

_MIN_BLOCK_BUFFERING: int = 2

_STREAM_SINK_NAMES: frozenset[str] = frozenset(
    {
        "sink",
        "console_sink",
        "make_stderr_log_sink",
        "make_sanitizing_log_sink",
        "stderr",
        "stdout",
        "sys.stderr",
        "sys.stdout",
    }
)

_FILE_SINK_PATH_NAMES: frozenset[str] = frozenset(
    {
        "text_log_path",
        "structured_log_path",
    }
)


@dataclass(frozen=True)
class LogSinkBufferingViolation:
    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.kind}] {self.message}"


def _is_stream_or_callable_sink(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in _STREAM_SINK_NAMES
    if isinstance(node, ast.Attribute):
        dotted: str = _dotted_name(node)
        return dotted in _STREAM_SINK_NAMES
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in _STREAM_SINK_NAMES:
            return True
        if isinstance(func, ast.Attribute):
            return _dotted_name(func) in _STREAM_SINK_NAMES
    return False


def _dotted_name(node: ast.Attribute) -> str:
    if isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    if isinstance(node.value, ast.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr}"
    return node.attr


def _is_filesystem_path_expression(node: ast.AST) -> bool:
    is_div_join: bool = isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
    is_path_constructor: bool = bool(_is_path_constructor(node))
    is_string_literal: bool = isinstance(node, ast.Constant) and isinstance(node.value, str)
    is_fstring: bool = isinstance(node, ast.JoinedStr)
    is_named_path: bool = isinstance(node, ast.Name) and node.id in _FILE_SINK_PATH_NAMES
    is_attribute_path: bool = isinstance(node, ast.Attribute) and node.attr in _FILE_SINK_PATH_NAMES
    return bool(
        is_div_join
        or is_path_constructor
        or is_string_literal
        or is_fstring
        or is_named_path
        or is_attribute_path
    )


def _is_path_constructor(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "Path"
    if isinstance(func, ast.Attribute):
        return func.attr in {"Path", "PurePath", "PosixPath", "WindowsPath"}
    return False


def _get_buffering_kwarg(call: ast.Call) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == "buffering":
            return kw.value
    return None


def _constant_int_bindings(tree: ast.Module) -> dict[str, int]:
    bindings: dict[str, int] = {}
    for node in ast.walk(tree):
        value: ast.expr | None = None
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            value = node.value
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            value = node.value
            targets = [node.target]
        if not isinstance(value, ast.Constant):
            continue
        if isinstance(value.value, bool) or not isinstance(value.value, int):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                bindings[target.id] = value.value
    return bindings


def _positive_int_constant(
    node: ast.expr,
    bindings: dict[str, int] | None = None,
) -> bool:
    value: object
    if isinstance(node, ast.Name) and bindings is not None and node.id == "_FILE_SINK_BUFFER_BYTES":
        value = bindings.get(node.id)
    elif isinstance(node, ast.Constant):
        value = node.value
    else:
        return False
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value >= _MIN_BLOCK_BUFFERING


def _find_logger_add_calls(tree: ast.Module) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "add":
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "logger"):
            continue
        calls.append(node)
    return calls


def _find_buffered_file_adder_calls(tree: ast.Module) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "_add_buffered_file_sink" or not node.args.args:
            continue
        adder_name: str = node.args.args[0].arg
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            if isinstance(child.func, ast.Name) and child.func.id == adder_name:
                calls.append(child)
    return calls


def _check_logging_module(
    module_path: Path,
    rel_path: str,
    source: str,
) -> list[LogSinkBufferingViolation]:
    try:
        tree: ast.Module = ast.parse(source, filename=str(module_path))
    except (SyntaxError, ValueError):
        return []

    constant_bindings: dict[str, int] = _constant_int_bindings(tree)
    file_sink_calls: list[tuple[ast.Call, bool]] = []
    for call in _find_logger_add_calls(tree):
        if not call.args:
            continue
        sink_arg = call.args[0]
        if _is_stream_or_callable_sink(sink_arg):
            continue
        if _is_filesystem_path_expression(sink_arg):
            file_sink_calls.append((call, False))
    file_sink_calls.extend((call, True) for call in _find_buffered_file_adder_calls(tree))

    violations: list[LogSinkBufferingViolation] = []
    for call, allow_pinned_constant in file_sink_calls:
        buffering = _get_buffering_kwarg(call)
        bindings = constant_bindings if allow_pinned_constant else None
        if buffering is None or not _positive_int_constant(buffering, bindings):
            violations.append(
                LogSinkBufferingViolation(
                    kind="file_sink_missing_buffering",
                    file_path=rel_path,
                    line=call.lineno,
                    message=(
                        f"file sink adder call at line {call.lineno} must pass"
                        f" buffering={_MIN_BLOCK_BUFFERING}+ (a positive integer"
                        " constant); loguru's FileSink defaults to buffering=1"
                        " (line-buffered), which emits one OS write and one"
                        " fsevents notification per record."
                    ),
                )
            )
    return violations


def audit_log_sink_buffering(
    package_root: Path,
) -> list[LogSinkBufferingViolation]:
    if not package_root.is_dir():
        return []

    module_path: Path = package_root / _LOGGING_MODULE
    rel_path: str = _LOGGING_MODULE

    if not module_path.is_file():
        return [
            LogSinkBufferingViolation(
                kind="missing_logging_module",
                file_path=rel_path,
                line=0,
                message=(
                    f"{_LOGGING_MODULE!r} must exist under the package"
                    " root; its absence is itself drift"
                ),
            )
        ]

    try:
        source: str = module_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    if _LOGGER_ADD_MARKER not in source and _BUFFERED_FILE_SINK_HELPER_MARKER not in source:
        return []

    return _check_logging_module(module_path, rel_path, source)


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    package_root: Path = Path(argv[0]) if argv else Path(__file__).parent.parent

    if not package_root.is_dir():
        print(f"Package root not found: {package_root}", file=sys.stderr)
        return 2

    violations: list[LogSinkBufferingViolation] = audit_log_sink_buffering(package_root)

    if violations:
        print(f"LOG SINK BUFFERING VIOLATIONS: {len(violations)}")
        print("=" * 72)
        for violation in violations:
            print(f"  {violation}")
        print()
        print(
            "Fix the drift: every logger.add(<file-path>, ...) call in"
            f" {_LOGGING_MODULE!r} MUST pass buffering={_MIN_BLOCK_BUFFERING}+"
            " (the canonical value is 8192 -- matching the buffered"
            " ralph.jsonl sink and the worker sink). Route file sinks"
            " through ralph.logging._add_buffered_file_sink so the"
            " buffering invariant is set centrally and cannot regress."
            " Stream/callable sinks (logger.add(make_stderr_log_sink(), ...))"
            " are exempt; the audit discriminates them by AST shape."
        )
        return 1

    print("log sink buffering audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
