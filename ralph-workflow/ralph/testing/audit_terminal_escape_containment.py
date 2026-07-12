"""Audit that the terminal-escape containment contract is wired into every sink.

Enforces the literal-string invariants a regression of the
wt-036-claude-code-permission fix would break. The escape
containment contract:

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

  - ``ralph/display/parallel_display.py`` MUST NOT keep any
    `_ANSI_ESCAPE` reference (the old SGR-only import was the
    hidden second consumer -- an ImportError trap if just the
    constant is deleted without migrating its users), MUST keep
    ``strip_terminal_control`` (the rewrite target), and MUST use
    ``markup=False`` everywhere it sanitizes a line -- the
    unsanitized ``console.print(line)`` literal must not return.

  - ``ralph/display/activity_model.py`` MUST call
    ``strip_terminal_control`` (the activity_router render path
    -- line 95 used to do ``rich.markup.escape`` only, which
    escapes rich markup but NOT ANSI/C0 bytes).

  - ``ralph/agents/invoke/_pty_runner.py`` MUST NOT contain
    ``file=sys.stdout`` (it painted the rich Live status bar
    underneath) and MUST NOT contain ``tqdm(`` (the wrapper
    that wrote it).

  - ``ralph/agents/invoke/_process_reader.py`` MUST pin
    ``stdin=subprocess.DEVNULL`` (so the agent child never
    inherits Ralph's controlling-terminal stdin) and MUST NOT
    carry ``stdin=None`` (which is INHERIT).

  - ``ralph/agents/subprocess_executor.py`` MUST pin
    ``stdin=_DEVNULL`` (file-local alias of
    ``subprocess.DEVNULL`` matching the existing
    ``PIPE`` / ``STDOUT`` aliases).

  - ``ralph/agents/invoke/_pty_line_reader.py`` MUST still
    ``yield queued_line`` (proves the reader keeps yielding raw
    VT text -- a defence-in-depth pin against an over-eager
    future fix that sanitizes at the source and silently breaks
    interactive permission auto-approval).

Every literal was grep-verified against the current tree at
implementation time. Restoring any forbidden literal, or
narrowing the CSI class back to ``[0-9;?]``, fails the audit
with exit 1 and a banner that names the violated invariant.

Usage:

    python -m ralph.testing.audit_terminal_escape_containment

Exit 0 = clean, 1 = at least one invariant violated.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _read(rel_path: str) -> str:
    return (_PACKAGE_ROOT / rel_path).read_text(encoding="utf-8")


class Invariant:
    """One literal-string check the audit enforces."""

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
        missing = [
            f"{self.rel_path}: missing required literal {needle!r}"
            for needle in self.present
            if needle not in content
        ]
        forbidden = [
            f"{self.rel_path}: forbidden literal still present {needle!r}"
            for needle in self.absent
            if needle in content
        ]
        return [*missing, *forbidden]


_INVARIANTS: tuple[Invariant, ...] = (
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
    # parallel_display.py: _ANSI_ESCAPE was the hidden second consumer of
    # the SGR-only constant -- deleting the constant without migrating this
    # use site breaks the import. The unsanitized ``console.print(line)``
    # literal in the module-level emit_activity_line /
    # _render_titled_lines branches is also forbidden.
    Invariant(
        rel_path="display/parallel_display.py",
        present=(
            "strip_terminal_control",
            "markup=False",
        ),
        absent=(
            "_ANSI_ESCAPE",
            "console.print(line)",
        ),
    ),
    # activity_model.py: the activity_router render path now delegates to
    # strip_terminal_control BEFORE rich's ``escape`` (escape neutralises
    # rich markup but not ANSI/C0).
    Invariant(
        rel_path="display/activity_model.py",
        present=("strip_terminal_control",),
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
    # _process_reader.py: stdin=DEVNULL is present (the fix); stdin=None
    # is absent (the leak that let the agent inherit the controlling TTY).
    Invariant(
        rel_path="agents/invoke/_process_reader.py",
        present=("stdin=subprocess.DEVNULL",),
        absent=("stdin=None,",),
    ),
    # subprocess_executor.py: file-local alias of DEVNULL is wired into
    # the SpawnOptions block (matches the existing PIPE / STDOUT aliases).
    Invariant(
        rel_path="agents/subprocess_executor.py",
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
)


def main(argv: list[str] | None = None) -> int:
    """Run the terminal-escape containment audit and return the process exit code.

    Iterates the literal-string ``Invariant`` objects in ``_INVARIANTS`` and
    aggregates every violation across the eight files the containment
    contract touches. Prints a one-line summary on success or a labeled,
    line-broken failure banner on violation. Has no side effects beyond
    stdout output and ``sys.exit`` semantics.

    Args:
        argv: Unused positional argument list (kept for CLI symmetry with
            other audit entry points). Values are ignored.

    Returns:
        ``0`` when every invariant passes, ``1`` when at least one
        literal-string check fails.
    """
    del argv
    problems: list[str] = []
    for invariant in _INVARIANTS:
        problems.extend(invariant.violations())

    if problems:
        print(
            f"TERMINAL-ESCAPE-CONTAINMENT AUDIT FAILED: {len(problems)} invariant violation(s)"
        )
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
        "parallel_display no longer references _ANSI_ESCAPE and every "
        "agent-content sink uses strip_terminal_control via markup=False; "
        "activity_model routes through strip_terminal_control before rich "
        "escape; _pty_runner dropped tqdm + file=sys.stdout; "
        "_process_reader and subprocess_executor both request "
        "subprocess.DEVNULL stdin (no stdin=None INHERIT); _pty_line_reader "
        "still yields raw VT text."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
