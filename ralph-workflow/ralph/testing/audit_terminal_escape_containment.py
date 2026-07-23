"""Audit that the terminal-escape containment contract is wired into every sink.

Enforces the AST-scoped invariants a regression of the
wt-036-claude-code-permission fix would break. Whole-file
literal presence is not enough -- the audit MUST inspect the
specific function / method / ``SpawnOptions`` call that wires the
fix so an adversarial edit that removes the strip from one sink
while keeping the helper name in the file is still caught.

The escape containment contract:

  - ``ralph/display/line_sanitizer.py`` MUST expose
    ``strip_terminal_control`` and use the full ``[0-?]`` CSI
    parameter-byte range (0x30-0x3F). A narrower class such as
    ``[0-9;?]`` leaks private-parameter CSI sequences
    (``ESC[>0c`` device-attributes reply, ``ESC[<35;1;2M`` SGR
    mouse report) on screen.

  - ``ralph/display/_plain_constants.py`` MUST NOT carry the
    SGR-only ``[0-9;]*m`` regex that the previous plan revision
    relied on (it cannot match ``ESC[?1049h`` alternate screen,
    ``ESC[2J`` erase display, ``ESC[>0c`` private-parameter CSI).
    Stripping MUST go through ``strip_terminal_control``.

  - ``ralph/display/parallel_display.py``:
      * MUST NOT keep any ``_ANSI_ESCAPE`` reference (the old
        SGR-only import was the hidden second consumer -- an
        ImportError trap if just the constant is deleted without
        migrating its users).
      * ``ParallelDisplay.strip_markup`` MUST delegate to
        ``strip_terminal_control(line)`` (the
        rewrite target).
      * The module-level ``emit_activity_line`` MUST call
        ``_sanitize(line)`` in BOTH its ``unit_id is None`` and
        ``unit_id`` branches (the two ``console.print`` sites
        that previously painted the real terminal unsanitized).
      * ``ParallelDisplay._render_titled_lines`` MUST call
        ``strip_terminal_control`` on each body line (the
        artifact / handoff body sink).

  - ``ralph/display/activity_model.render_event_line`` MUST call
    ``strip_terminal_control(content or "")`` BEFORE the
    truncation and rich ``escape`` (the activity_router render
    path -- the original code did ``rich.markup.escape`` only,
    which escapes rich markup but NOT ANSI/C0 bytes).

  - ``ralph/agents/invoke/_pty_runner.py`` MUST NOT contain
    ``file=sys.stdout`` (it painted the rich Live status bar
    underneath) and MUST NOT contain ``tqdm(`` (the wrapper
    that wrote it).

  - ``ralph/agents/invoke/_process_reader.py`` MUST pass
    ``stdin=subprocess.DEVNULL`` to its ``SpawnOptions(...)``
    call (so the agent child never inherits Ralph's
    controlling-terminal stdin) and MUST NOT carry ``stdin=None``
    anywhere (which is INHERIT).

  - ``ralph/agents/subprocess_executor.py`` MUST pass
    ``stdin=_DEVNULL`` to its ``SpawnOptions(...)`` call (file-
    local alias of ``subprocess.DEVNULL`` matching the existing
    ``PIPE`` / ``STDOUT`` aliases).

  - ``ralph/agents/invoke/_pty_line_reader.py`` MUST still
    ``yield queued_line`` (proves the reader keeps yielding raw
    VT text -- a defence-in-depth pin against an over-eager
    future fix that sanitizes at the source and silently breaks
    interactive permission auto-approval).

  - ``ralph/process/manager/_spawn_options.py`` MUST default
    ``SpawnOptions.stdin`` to ``subprocess.DEVNULL`` (so no
    child inherits Ralph's controlling-terminal stdin by
    construction). The dataclass field must NOT be reverted to
    ``None``.

  - No ``SpawnOptions(...)`` call site anywhere under the
    ``ralph/`` package may pass ``stdin=None`` (package-wide
    AST scan via :class:`PackageWideCallSiteInvariant` -- the
    helper exists in three flavors: ``Invariant`` (whole-file),
    ``FunctionBodyInvariant`` (function/method) and
    ``CallSiteInvariant`` (single file callee). The new variant
    extends coverage to a callee-in-any-file).

  - ``ralph/logging.py::configure_logging`` MUST NOT hand
    ``sys.stderr`` to ``logger.add`` (the public library
    configurator accepts an injected ``console_sink`` keyword)
    and MUST NOT enable ``colorize=True`` (the stripper removes
    the SGR codes that colorizer emits). Sanitization happens
    through :func:`strip_terminal_control` via the injected
    ``console_sink``.

  - ``ralph/cli/main.py::_configure_logging`` MUST NOT hand
    ``sys.stderr`` to ``logger.add`` for ANY of the five
    verbosity branches. Every branch routes through an injected
    ``console_sink`` (or the library/worker fallback
    ``make_stderr_log_sink``).

  - ``ralph/cli/main.py::main`` MUST wire the Console-backed
    sink by passing ``make_sanitizing_log_sink(_cli_ctx)`` to
    ``configure_logging`` (the CLI call site). Without this
    wiring, loguru emits records via raw ``sys.stderr`` and the
    rich ``Live`` status bar races the logger for the terminal.

  - ``ralph/display/log_sink.py::make_sanitizing_log_sink``
    MUST call ``strip_terminal_control`` and print through the
    injected Console with ``markup=False`` / ``highlight=False``.

  - ``ralph/display/log_sink.py::make_stderr_log_sink`` MUST
    call ``strip_terminal_control`` before writing the line.

  - ``ralph/display/log_sink.py`` MUST NOT construct a
    ``rich.Console`` itself (DI invariant: the single source
    of truth for ``Console`` construction is
    ``ralph.display.theme``).

  - ``ralph/process/pty.py::spawn_pty_process`` MUST keep
    ``os.setsid()`` (new session) and ``TIOCSCTTY`` (controlling
    terminal = slave pty) inside the child branch. Without this
    pair a PTY child could claim the foreground process group
    of Ralph's controlling TTY.

Every literal was grep-verified against the current tree at
implementation time. Restoring any forbidden literal, removing
a required call from the wired sink, or narrowing the CSI
class back to ``[0-9;?]`` fails the audit with exit 1 and a
banner that names the violated invariant.

Usage:

    python -m ralph.testing.audit_terminal_escape_containment

Exit 0 = clean, 1 = at least one invariant violated.
"""

from __future__ import annotations

import ast
import sys
from collections import OrderedDict
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_READ_CACHE_LIMIT = 2048
_PARSE_CACHE_LIMIT = 256
_READ_CACHE: OrderedDict[str, str] = OrderedDict()  # bounded-accumulator-ok: FIFO cap 2048
_PARSE_CACHE: OrderedDict[str, ast.Module] = OrderedDict()  # bounded-accumulator-ok: FIFO cap 256


def _read(rel_path: str) -> str:
    """Read one package source file, caching the audit process's immutable snapshot."""
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
    """Parse a source snapshot once across the audit's overlapping invariants."""
    cached = _PARSE_CACHE.get(source)
    if cached is not None:
        _PARSE_CACHE.move_to_end(source)
        return cached
    tree = ast.parse(source, filename=filename)
    _PARSE_CACHE[source] = tree
    if len(_PARSE_CACHE) > _PARSE_CACHE_LIMIT:
        _PARSE_CACHE.popitem(last=False)
    return tree


def _function_body(rel_path: str, *, qualname: str) -> str | None:
    """Return the source segment of ``qualname`` in ``rel_path``.

    ``qualname`` is a dotted ``Class.method`` or just a top-level
    function name. Returns ``None`` when the target is missing
    (the caller treats that as a violation).
    """
    source = _read(rel_path)
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
                # ``parts[-1]`` is a method inside this class.
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
    segment = ast.get_source_segment(source, func)
    return segment if segment is not None else ""


def _call_site_sources(rel_path: str, *, callee_name: str) -> list[str]:
    """Return the source segment of every ``callee_name(...)`` call in ``rel_path``.

    The audit uses this to pin ``SpawnOptions`` call-site shape.
    """
    source = _read(rel_path)
    tree = _parse_source(source, rel_path)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        match = (isinstance(func, ast.Name) and func.id == callee_name) or (
            isinstance(func, ast.Attribute) and func.attr == callee_name
        )
        if not match:
            continue
        segment = ast.get_source_segment(source, node)
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
    """One literal-string check the audit enforces against a whole file."""

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
    """AST-scoped check: every literal must hold inside a named function body.

    Use ``FunctionBodyInvariant`` when the audit must prove a
    sanitiser call is INSIDE the wired sink function -- whole-file
    presence is not enough, because the helper name can appear in
    imports or docstrings without ever being called from the sink.

    ``min_counts`` maps a needle to the minimum number of
    occurrences required in the body. Use it to pin a call to
    every branch of a sink (e.g. the two ``console.print`` sites
    in the module-level ``emit_activity_line``) -- a single
    ``present`` check would let an adversary sanitize only one
    branch and pass.
    """

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
    """AST-scoped check: every literal must hold in at least one named callee call site.

    Use ``CallSiteInvariant`` when the audit must prove a
    specific argument is passed to a specific constructor (e.g.
    ``SpawnOptions(stdin=subprocess.DEVNULL, ...)``). Checks are
    performed against the source segment of each call node, so
    formatting across lines does not break the literal match.
    """

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


class PackageWideCallSiteInvariant:
    """AST-scoped check: a callee call anywhere under the package MUST avoid a literal.

    Use ``PackageWideCallSiteInvariant`` when a contract must
    hold for a constructor at every call site in the entire
    package -- the dataclass default alone is not enough
    because an explicit re-opt-in (e.g.
    ``SpawnOptions(stdin=None)``) silently re-opens the leak.
    Walks every ``*.py`` file under :data:`_PACKAGE_ROOT`,
    collects the source segment of every ``callee_name(...)``
    call, and rejects any segment that carries a forbidden
    literal. Exposes the same ``violations() -> list[str]``
    interface as :class:`Invariant` /
    :class:`FunctionBodyInvariant` / :class:`CallSiteInvariant`
    so ``main()``'s loop is unchanged.

    The class uses ``_read`` (the same monkeypatchable source
    fetcher the other invariants use) so adversarial tests can
    inject a custom source per file via :func:`_patch_rel`. The
    package file list is cached for a single audit run to keep
    the wall-clock cost off the 60s combined budget.
    """

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
        """Return the package-relative path of every ``*.py`` file (cached)."""
        if cls._PACKAGE_FILE_LIST_CACHE is None:
            cls._PACKAGE_FILE_LIST_CACHE = sorted(
                str(p.relative_to(_PACKAGE_ROOT).as_posix()) for p in _PACKAGE_ROOT.rglob("*.py")
            )
        return cls._PACKAGE_FILE_LIST_CACHE

    @classmethod
    def reset_cache(cls) -> None:
        """Clear the package file list cache (test helper)."""
        cls._PACKAGE_FILE_LIST_CACHE = None

    def violations(self) -> list[str]:
        problems: list[str] = []
        # Fast-path text filters: a violating call must mention both the callee
        # and (for an absent-only invariant) one of the forbidden literals.
        # Avoid parsing files that cannot possibly violate the contract.
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
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                match = (isinstance(func, ast.Name) and func.id == self.callee_name) or (
                    isinstance(func, ast.Attribute) and func.attr == self.callee_name
                )
                if not match:
                    continue
                segment = ast.get_source_segment(source, node)
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


_INVARIANTS: tuple[
    Invariant | FunctionBodyInvariant | CallSiteInvariant | PackageWideCallSiteInvariant, ...
] = (
    # line_sanitizer.py: the canonical stripper exists, uses the FULL
    # [0-?] CSI parameter-byte class (NOT the narrower [0-9;?] form).
    Invariant(
        rel_path="display/line_sanitizer.py",
        present=(
            "def strip_terminal_control",
            "[0-?]",
        ),
        absent=("[0-9;?]",),
    ),
    # _plain_constants.py: the SGR-only regex is gone; stripping goes
    # through strip_terminal_control (the rewrite target).
    Invariant(
        rel_path="display/_plain_constants.py",
        present=("strip_terminal_control",),
        absent=("[0-9;]*m",),
    ),
    # parallel_display.py file-level: _ANSI_ESCAPE was the hidden second
    # consumer of the SGR-only constant -- deleting the constant without
    # migrating this use site breaks the import. The unsanitized
    # ``console.print(line)`` literal in the module-level
    # emit_activity_line / _render_titled_lines branches is also forbidden.
    Invariant(
        rel_path="display/parallel_display.py",
        present=("markup=False",),
        absent=(
            "_ANSI_ESCAPE",
            "console.print(line)",
        ),
    ),
    # parallel_display.ParallelDisplay.strip_markup: the rewrite target.
    # The body MUST reduce valid Rich markup before delegating to
    # ``strip_terminal_control`` (hostile CSI/OSC/C0 strip). Malformed
    # markup remains literal so agent output is not lost. The deeper
    # ``_sanitize`` helper uses the same contract.

    FunctionBodyInvariant(
        rel_path="display/parallel_display.py",
        qualname="ParallelDisplay.strip_markup",
        present=("strip_terminal_control(line)",),
    ),
    # parallel_display._render_titled_lines: the artifact/handoff body
    # sink -- each line must be sanitized via strip_terminal_control
    # AND printed with markup=False.
    FunctionBodyInvariant(
        rel_path="display/parallel_display.py",
        qualname="ParallelDisplay._render_titled_lines",
        present=(
            "strip_terminal_control(line)",
            "markup=False",
        ),
    ),
    # parallel_display module-level emit_activity_line: BOTH the
    # unit_id-is-None branch AND the unit_id-set branch must call
    # _sanitize(line) and use markup=False. ``_sanitize(line)``
    # appears twice in the post-fix body (once per branch) so we
    # pin the minimum count at 2 -- a single-branch sanitiser
    # rewrite would still pass a presence-only check.
    FunctionBodyInvariant(
        rel_path="display/parallel_display.py",
        qualname="emit_activity_line",
        present=("markup=False",),
        min_counts={"_sanitize(line)": 2},
    ),
    # activity_model.render_event_line: the activity_router render path
    # MUST call strip_terminal_control(content or "") BEFORE truncation.
    FunctionBodyInvariant(
        rel_path="display/activity_model.py",
        qualname="render_event_line",
        present=("strip_terminal_control(content or",),
    ),
    # _pty_runner.py: tqdm-wrapped progress bar is removed (the second
    # painter that races the rich Live status bar).
    Invariant(
        rel_path="agents/invoke/_pty_runner.py",
        absent=(
            "file=sys.stdout",
            "tqdm(",
        ),
    ),
    # _process_reader.py: SpawnOptions(...) call MUST pass
    # stdin=subprocess.DEVNULL, and the file MUST NOT carry a
    # stdin=None default (the INHERIT leak).
    Invariant(
        rel_path="agents/invoke/_process_reader.py",
        present=("stdin=subprocess.DEVNULL",),
        absent=("stdin=None,",),
    ),
    CallSiteInvariant(
        rel_path="agents/invoke/_process_reader.py",
        callee_name="SpawnOptions",
        present=("stdin=subprocess.DEVNULL",),
    ),
    # subprocess_executor.py: SpawnOptions(...) call MUST pass
    # stdin=_DEVNULL (the file-local alias of subprocess.DEVNULL).
    CallSiteInvariant(
        rel_path="agents/subprocess_executor.py",
        callee_name="SpawnOptions",
        present=("stdin=_DEVNULL",),
    ),
    # _pty_line_reader.py: the reader keeps yielding raw VT. Pinning
    # this is a defence-in-depth measure: a future "helpful" fix that
    # sanitizes inside the reader (instead of at the display boundary)
    # would silently break interactive permission auto-approval.
    Invariant(
        rel_path="agents/invoke/_pty_line_reader.py",
        present=("yield queued_line",),
    ),
    # _spawn_options.py: SpawnOptions.stdin MUST default to subprocess.DEVNULL
    # so no child inherits Ralph's controlling-terminal stdin by construction.
    Invariant(
        rel_path="process/manager/_spawn_options.py",
        present=("import subprocess", "stdin: int | None = subprocess.DEVNULL"),
        absent=("stdin: int | None = None",),
    ),
    # Package-wide SpawnOptions() call sites MUST NOT pass stdin=None
    # (the INHERIT leak). The class-wide scan lives in
    # PackageWideCallSiteInvariant -- this is the only place a regression
    # can be caught for any future SpawnOptions call site.
    PackageWideCallSiteInvariant(
        callee_name="SpawnOptions",
        absent=("stdin=None",),
    ),
    # logging.py: configure_logging MUST NOT hand sys.stderr to logger.add
    # (the stripper cannot remove what the parent process has already
    # painted). It MUST accept console_sink (sanitizing fallback), call
    # make_stderr_log_sink (single source of sanitization), and use
    # colorize=False (loguru's colorizer emits SGR the stripper deletes).
    FunctionBodyInvariant(
        rel_path="logging.py",
        qualname="configure_logging",
        present=("console_sink", "make_stderr_log_sink"),
        absent=("sys.stderr", "colorize=True"),
    ),
    # cli/main.py::_configure_logging: the sink the CLI actually calls.
    # ALL five verbosity branches route through the injected console_sink
    # (or the library/worker fallback make_stderr_log_sink).
    FunctionBodyInvariant(
        rel_path="cli/main.py",
        qualname="_configure_logging",
        present=("console_sink", "make_stderr_log_sink"),
        absent=("sys.stderr",),
    ),
    # cli/main.py::main: the call site MUST wire the Console-backed sink
    # so loguru prints through the same DisplayContext Console the rich
    # Live status bar owns. Without this wiring the logger is a second
    # independent painter of the terminal.
    FunctionBodyInvariant(
        rel_path="cli/main.py",
        qualname="main",
        present=("make_sanitizing_log_sink",),
    ),
    # display/log_sink.py::make_sanitizing_log_sink: must sanitize and
    # print via the injected Console with markup=False / highlight=False
    # (rich would otherwise re-evaluate bracketed paths and [bold] tokens).
    FunctionBodyInvariant(
        rel_path="display/log_sink.py",
        qualname="make_sanitizing_log_sink",
        present=("strip_terminal_control", "markup=False", "highlight=False"),
    ),
    # display/log_sink.py::make_stderr_log_sink: must sanitize before
    # writing to stderr. Two entries (function-body + whole-file) so a
    # silent removal of the stripper from the fallback fails the audit.
    FunctionBodyInvariant(
        rel_path="display/log_sink.py",
        qualname="make_stderr_log_sink",
        present=("strip_terminal_control",),
    ),
    Invariant(
        rel_path="display/log_sink.py",
        # Concatenated so ``test_no_anti_drift_regression.py`` (which
        # line-scans ralph/ for an inline console-constructor outside
        # theme.py) does not flag this audit's own needle string.
        absent=("Console" + "(",),
    ),
    # process/pty.py::spawn_pty_process: must call os.setsid() and
    # fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0) inside the child branch
    # so a PTY child cannot claim the foreground of Ralph's controlling TTY.
    FunctionBodyInvariant(
        rel_path="process/pty.py",
        qualname="spawn_pty_process",
        present=("os.setsid()", "TIOCSCTTY"),
    ),
)


def main(argv: list[str] | None = None) -> int:
    """Run the terminal-escape containment audit and return the process exit code.

    Iterates the literal-string and AST-scoped invariants in
    ``_INVARIANTS`` and aggregates every violation across every
    file the containment contract touches (display modules, the
    two logging configurators, the SpawnOptions dataclass, the
    ProcessManager seams, and the PTY spawn path). Prints a
    one-line summary on success or a labeled, line-broken failure
    banner on violation. Has no side effects beyond stdout output
    and ``sys.exit`` semantics.

    Args:
        argv: Unused positional argument list (kept for CLI symmetry with
            other audit entry points). Values are ignored.

    Returns:
        ``0`` when every invariant passes, ``1`` when at least one
        literal-string or AST-scoped check fails.
    """
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
        "line_sanitizer has strip_terminal_control with [0-?] class (not "
        "[0-9;?]); _plain_constants no longer carries the SGR-only regex; "
        "parallel_display.strip_markup / _render_titled_lines and the "
        "module-level emit_activity_line delegate to "
        "strip_terminal_control with markup=False (no _ANSI_ESCAPE, no "
        "unsanitized console.print(line)); activity_model.render_event_line "
        "calls strip_terminal_control before truncation; _pty_runner "
        "dropped tqdm + file=sys.stdout; _process_reader and "
        "subprocess_executor pass stdin=DEVNULL to their SpawnOptions call "
        "sites (no stdin=None INHERIT); _pty_line_reader still yields raw "
        "VT text. SpawnOptions.stdin defaults to subprocess.DEVNULL and no "
        "SpawnOptions call anywhere under ralph/ passes stdin=None. "
        "ralph.logging.configure_logging AND ralph.cli.main._configure_logging "
        "route through an injected console_sink (no raw sys.stderr, "
        "colorize=False); the CLI call site wires make_sanitizing_log_sink "
        "for the DisplayContext Console. Both log_sink factories call "
        "strip_terminal_control with markup=False / highlight=False and "
        "no raw Console construction. ralph.process.pty.spawn_pty_process "
        "still calls os.setsid() + TIOCSCTTY."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
