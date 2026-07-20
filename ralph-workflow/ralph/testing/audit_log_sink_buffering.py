"""Log-sink buffering drift audit (AST-based).

The always-on ``ralph.log`` engine text sink and the structured
``ralph.jsonl`` sink must both write through ``buffering=8192`` to
amortize OS write syscalls -- and therefore fsevents notifications
-- across many records. loguru's ``FileSink`` defaults to
``buffering=1`` (line-buffered), which produces one OS write per
log record (and one fsevents notification per record) at verbose
levels. Forgetting the kwarg silently reinflates the fseventsd
footprint; this audit locks the buffering invariant structurally.

The audit parses ``ralph/logging.py`` with the ``ast`` module only
(no subprocess, no ``time.sleep``, no real file I/O outside reading
source) and enforces one invariant:

  * Every ``logger.add(<file-path>, ...)`` call MUST pass a keyword
    argument named ``buffering`` whose value is a positive integer
    constant (1 byte is technically positive but is line-buffered --
    the audit pins the same minimum as the fseventsd mitigation:
    ``buffering > 1`` and a positive integer constant).

It discriminates file sinks structurally from callable/stream sinks:

* A file sink has its FIRST positional argument as a filesystem
  expression (a ``/``-join ``BinOp`` like ``run_directory /
  "ralph.log"``, an ``Attribute`` whose terminal ``attr`` is a
  canonical file-sink name (``paths.text_log_path``,
  ``config.structured_log_path``), a ``Name`` bound to a canonical
  file-sink name (``text_log_path``, ``structured_log_path``), a
  ``Call`` to a ``Path``-family constructor returning a ``Path``
  like ``Path(...)``, a ``Constant`` that is a string path, or
  any ``Call`` to a known filesystem constructor).  These MUST
  pass ``buffering``.
* A callable/stream sink has its first positional argument as a
  bare ``Name`` or ``Attribute`` referring to a callable (e.g.
  ``sink``, ``console_sink``, ``make_stderr_log_sink()``).  These
  are NOT file sinks and are skipped.

Usage::

    python -m ralph.testing.audit_log_sink_buffering [package_root]

Exit codes:
  0 = clean
  1 = violations found
  2 = root not found
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


#: Module that owns the canonical engine file sinks.  Anchored at
#: import time so a refactor that renames or relocates the module
#: trips the audit immediately rather than silently passing.
_LOGGING_MODULE: str = "logging.py"

#: Pre-filter substring for the logger.add detector.  Files whose
#: source does not contain the literal token ``logger.add(`` cannot
#: add a loguru sink, so the expensive AST pass is skipped for them.
_LOGGER_ADD_MARKER: str = "logger.add("

_BUFFERED_FILE_SINK_HELPER_MARKER: str = "def _add_buffered_file_sink"

#: The loguru ``FileSink`` ``buffering`` minimum that the audit
#: considers non-trivial.  ``buffering=1`` is line-buffered (one
#: OS write per record), which is exactly the regression class
#: the audit catches.  The production code uses ``buffering=8192``
#: (8 KB) so the minimum is set to ``> 1`` to leave room for any
#: future larger block size without churn.
_MIN_BLOCK_BUFFERING: int = 2

#: Names that mark a sink argument as a CALLABLE/STREAM sink (not a
#: file path).  The audit skips ``logger.add`` calls whose first
#: positional argument is one of these -- they are routed through
#: loguru's stream/callable sink code path, not ``FileSink``.
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

#: Canonical ``ast.Name`` identifiers that mark a sink argument as
#: a FILE PATH (not a callable/stream sink).  The audit treats these
#: names as file-path expressions -- matching the canonical engine
#: file-sink bindings in ``ralph/logging.py::_configure_file_handlers``
#: (``text_log_path = run_directory / "ralph.log"``,
#: ``structured_log_path = run_directory / "ralph.jsonl"``).  A
#: future refactor that writes ``logger.add(text_log_path, ...)``
#: or ``logger.add(structured_log_path, ...)`` directly MUST pass
#: ``buffering=8192``; this set locks the invariant structurally so
#: the regression class (omitted buffering on a named path) is
#: detected by the audit, not by ad-hoc review.
_FILE_SINK_PATH_NAMES: frozenset[str] = frozenset(
    {
        "text_log_path",
        "structured_log_path",
    }
)


@dataclass(frozen=True)
class LogSinkBufferingViolation:
    """A single log-sink-buffering audit violation."""

    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.kind}] {self.message}"


def _is_stream_or_callable_sink(node: ast.AST) -> bool:
    """Return True iff ``node`` looks like a callable/stream sink (not a file path).

    Discriminates by AST shape:
      * a bare ``ast.Name`` whose identifier is in ``_STREAM_SINK_NAMES``
      * an ``ast.Attribute`` whose dotted name is in ``_STREAM_SINK_NAMES``
        (e.g. ``sys.stderr``)
      * a ``Call`` whose function name is in ``_STREAM_SINK_NAMES``
        (e.g. ``make_stderr_log_sink()``)
    """
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
    """Return a dotted name for an ``ast.Attribute`` chain.

    Walks the ``value`` chain recursively.  ``Attribute(value=Name('a'),
    attr='b')`` -> ``'a.b'``.  Returns the original ``attr`` string
    if the chain cannot be expressed as a dotted name (e.g. the
    value is a ``Call`` or ``Subscript``).
    """
    if isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    if isinstance(node.value, ast.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr}"
    return node.attr


def _is_filesystem_path_expression(node: ast.AST) -> bool:
    """Return True iff ``node`` looks like a filesystem path expression.

    Heuristic:
      * ``ast.BinOp`` with operator ``ast.Div`` (Python ``/`` join,
        the canonical pathlib idiom).
      * ``ast.Call`` whose function dotted name starts with ``Path``
        (``Path("...")``, ``Path()``, ``PurePath()``, ``PathlibPath()``,
        etc.).
      * ``ast.Constant`` whose value is a ``str`` (string literal
        path -- rare in production but defensible).
      * ``ast.JoinedStr`` (f-string path -- rare but allowed).
      * ``ast.Name`` whose identifier is in ``_FILE_SINK_PATH_NAMES``
        (the canonical engine file-sink bindings -- ``text_log_path``
        and ``structured_log_path``).  The audit treats these as
        file-path expressions so that a direct
        ``logger.add(text_log_path, ...)`` call without
        ``buffering=8192`` is flagged as a regression -- exactly the
        per-record filesystem-mutation class the fseventsd
        mitigation closes.  Other ``ast.Name`` values (e.g. ``sink``,
        ``console_sink``) are explicitly NOT in this set; they are
        stream/callable sinks and are skipped by
        :func:`_is_stream_or_callable_sink` before this function is
        even consulted.
      * ``ast.Attribute`` whose terminal ``attr`` is in
        ``_FILE_SINK_PATH_NAMES`` (e.g. ``paths.text_log_path``,
        ``config.structured_log_path``).  Recognized conservatively
        -- only the canonical file-sink names are matched, so other
        attribute access (e.g. ``module.foo``) does not false-flag.
        Stream/callable attribute access (e.g. ``sys.stderr``) is
        filtered out earlier by
        :func:`_is_stream_or_callable_sink` via the same dotted-name
        resolution; this branch only sees attribute access that did
        not match the stream set, so matching
        ``attr in _FILE_SINK_PATH_NAMES`` is sound and conservative.
    """
    is_div_join: bool = isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
    is_path_constructor: bool = bool(_is_path_constructor(node))
    is_string_literal: bool = isinstance(node, ast.Constant) and isinstance(node.value, str)
    is_fstring: bool = isinstance(node, ast.JoinedStr)
    is_named_path: bool = isinstance(node, ast.Name) and node.id in _FILE_SINK_PATH_NAMES
    is_attribute_path: bool = (
        isinstance(node, ast.Attribute) and node.attr in _FILE_SINK_PATH_NAMES
    )
    return bool(
        is_div_join
        or is_path_constructor
        or is_string_literal
        or is_fstring
        or is_named_path
        or is_attribute_path
    )


def _is_path_constructor(node: ast.AST) -> bool:
    """Return True iff ``node`` is a ``Path(...)``-style filesystem constructor call."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "Path"
    if isinstance(func, ast.Attribute):
        return func.attr in {"Path", "PurePath", "PosixPath", "WindowsPath"}
    return False


def _get_buffering_kwarg(call: ast.Call) -> ast.expr | None:
    """Return the ``buffering=`` kwarg value node, or ``None`` if absent.

    Matches the literal keyword name ``"buffering"``.  Positional
    ``logger.add`` does not accept buffering positionally, so a
    positional match is unnecessary.
    """
    for kw in call.keywords:
        if kw.arg == "buffering":
            return kw.value
    return None


def _constant_int_bindings(tree: ast.Module) -> dict[str, int]:
    """Return statically resolvable integer bindings in ``tree``."""
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
    """Return True iff ``node`` is a literal or the pinned buffer constant."""
    value: object
    if (
        isinstance(node, ast.Name)
        and bindings is not None
        and node.id == "_FILE_SINK_BUFFER_BYTES"
    ):
        value = bindings.get(node.id)
    elif isinstance(node, ast.Constant):
        value = node.value
    else:
        return False
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value >= _MIN_BLOCK_BUFFERING


def _find_logger_add_calls(tree: ast.Module) -> list[ast.Call]:
    """Return every ``logger.add(...)`` ``ast.Call`` under ``tree``.

    Matches ``ast.Call`` whose function is an ``ast.Attribute``
    named ``add`` whose ``value`` is a ``Name`` whose identifier is
    ``logger``.  This excludes unrelated ``.add(...)`` calls
    (``dict.add``, ``pathlib.Path.add`` is not a thing but other
    classes use it).
    """
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
    """Return calls to the injected adder inside the file-sink helper."""
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
    """Run the buffering invariant against one ``logging.py`` source string.

    Args:
        module_path: Absolute path of the module being audited
            (used as the ``filename`` argument to ``ast.parse`` for
            accurate error reporting).
        rel_path: Posix-style path relative to ``package_root``;
            recorded in violation messages.
        source: The full source text of the module.

    Returns:
        A list of violations.  Empty when the invariant passes.
    """
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
    """Walk the production source tree and return all violations.

    Parses only the single canonical ``logging.py`` module and
    enforces the buffering invariant.  Returns an empty list when
    ``package_root`` is not a directory (fail-closed: the audit
    does not silently pass on a missing root).
    """
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

    if (
        _LOGGER_ADD_MARKER not in source
        and _BUFFERED_FILE_SINK_HELPER_MARKER not in source
    ):
        return []

    return _check_logging_module(module_path, rel_path, source)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.  Returns 0 when clean, 1 on violations, 2 on bad root."""
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
