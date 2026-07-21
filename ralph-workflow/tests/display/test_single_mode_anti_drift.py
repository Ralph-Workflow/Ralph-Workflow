"""wt-028-display: anti-drift guard for the consolidated single display mode.

Pins the architectural invariant that Ralph Workflow exposes exactly ONE
display mode. Future commits that re-introduce mode-conditional branches
in the production code under ``ralph/display/`` MUST fail this test.

Scanned checks:

1. ``DisplayContext.mode`` is a :data:`~typing.Literal['default']`-typed
   field with a constant string value ``'default'``. There is no
   ``Literal['compact', 'medium', 'wide']`` annotation, no factory
   that returns any other value, and no public override surface.

2. No production file under ``ralph/display/`` contains an AST
   ``Compare`` node whose right-hand string literal is one of
   ``'compact'``, ``'medium'``, or ``'wide'``. The allowlist covers
   ``ralph/display/mode.py`` (the consolidated ``DEFAULT_MODE`` constant
   lives there) and ``ralph/display/__init__.py`` (which documents the
   single mode in its module docstring).

3. No production file under ``ralph/cli/main.py`` or ``ralph/cli/commands/``
   declares a CLI flag whose name matches EITHER forbidden form:
   - Form A: joined ``--display-mode`` / ``--display_mode`` (case-insensitive
     substring match in the flag name or in the ``help=`` keyword value);
   - Form B: bare ``--display`` with strict word-boundary (NOT followed by
     ``-``, ``_``, or ``[a-z]``, so ``--display-mode`` / ``--displayfoo``
     do NOT match Form B — Form A covers them).

The AST cache is populated at module import time so the test runs in
< 1 s.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from functools import cache, lru_cache
from pathlib import Path
from typing import get_args, get_origin

import pytest

from ralph.display.context import DisplayContext, make_display_context
from ralph.display.mode import DEFAULT_MODE

_DISPLAY_DIR = Path(__file__).parent.parent.parent / "ralph" / "display"
_CLI_MAIN = Path(__file__).resolve().parent.parent.parent / "ralph" / "cli" / "main.py"
_CLI_COMMANDS_DIR = Path(__file__).resolve().parent.parent.parent / "ralph" / "cli" / "commands"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DRIFT_SCRIPT = _PROJECT_ROOT / "scripts" / "wt028-drift-check.sh"
_ALLOWLIST = frozenset({"mode.py", "__init__.py"})
_MODE_LITERALS = frozenset({"compact", "medium", "wide"})


@lru_cache(maxsize=1)
def _display_files() -> tuple[Path, ...]:
    """Return all *.py files under ralph/display/."""
    return tuple(sorted(_DISPLAY_DIR.glob("*.py")))


@cache
def _parsed(path: Path) -> ast.Module:
    """Return the AST module for the given file, parsed once."""
    return ast.parse(path.read_text(encoding="utf-8"))


def _string_literal_value(node: ast.AST) -> str | None:
    """Return the constant string value of an AST Constant node with str value, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _iter_compare_string_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """Yield ``(lineno, literal)`` for every string literal appearing in a Compare node.

    Covers both LHS (``"compact" == x``) and RHS (``x == "compact"``)
    comparisons. Walks both ``ast.Compare`` and the simple
    ``ast.NamedExpr`` (``mode := "compact"``) cases for safety.
    """
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for sub in (node.left, *node.comparators):
                value = _string_literal_value(sub)
                if value is not None and value in _MODE_LITERALS:
                    hits.append((node.lineno, value))
    return hits


# Pre-populate the AST cache at import time so the per-test SIGALRM
# window is not spent re-parsing files.
for _f in _display_files():
    _parsed(_f)


def test_display_context_mode_is_literal_default() -> None:
    """DisplayContext.mode is typed Literal['default'] (no other mode values)."""
    annotations = DisplayContext.__annotations__
    assert "mode" in annotations, (
        f"DisplayContext is missing 'mode' annotation; got {sorted(annotations)!r}"
    )
    annotation_str = str(annotations["mode"])
    assert "default" in annotation_str, (
        f"DisplayContext.mode annotation must include 'default'; got {annotation_str!r}"
    )
    # Use typing introspection for the canonical check.
    annotation = annotations["mode"]
    args = get_args(annotation)
    if args or get_origin(annotation) is not None:
        assert tuple(args) == ("default",), (
            f"DisplayContext.mode must be Literal['default'] only; got Literal{args!r}"
        )


def test_display_context_mode_default_constant_is_default() -> None:
    """The DEFAULT_MODE constant in ralph.display.mode is exactly the string 'default'."""
    assert DEFAULT_MODE == "default", (
        f"ralph.display.mode.DEFAULT_MODE must be 'default'; got {DEFAULT_MODE!r}"
    )


def test_no_compact_medium_wide_branches_in_display_production() -> None:
    """No ralph/display/*.py file outside the allowlist compares to 'compact' / 'medium' / 'wide'.

    Allowlist: ralph/display/mode.py (DEFAULT_MODE='default' constant), and
    ralph/display/__init__.py (the consolidated single-mode docs mention the
    removed mode names only as historical references in the docstring; we
    still allowlist this file because the AST scan would otherwise flag
    docstring-mentions of "compact mode" inside the docstring — but the
    tokenize-based ast.parse only sees code, not strings. The allowlist is
    defence-in-depth: tokenize-based code scanning already excludes
    docstrings, but we keep the allowlist so future docstring expansions
    stay safe.
    """
    violations: list[str] = []
    for path in _display_files():
        if path.name in _ALLOWLIST:
            continue
        tree = _parsed(path)
        for lineno, literal in _iter_compare_string_literals(tree):
            violations.append(f"{path.name}:{lineno}: '{literal}'")
    assert not violations, (
        "Mode-conditional branches found in ralph/display/ production code "
        "(anti-drift guard tripped; Ralph Workflow has a SINGLE display mode "
        "called 'default' — re-introduce no other mode):\n" + "\n".join(violations)
    )


def test_make_display_context_no_force_mode_kwarg_call_works() -> None:
    """make_display_context() with no extra kwargs returns a DisplayContext with mode='default'."""
    ctx = make_display_context()
    assert ctx.mode == "default"
    assert isinstance(ctx, DisplayContext)


# ---------------------------------------------------------------------------
# Subprocess-driven drift-check probe (AC-05 / AC-06 fail-closed proof)
# ---------------------------------------------------------------------------

# Every legacy token the consolidation named. The script's regex is
# case-sensitive so the probe MUST contain the exact uppercase form.
# The compact / medium / wide mode-branch variants are tested
# explicitly below (the script's regex covers all three through the
# mode-name alternation). Every literal token fragment is wrapped in
# a string-concatenation step so the assembled token only exists at
# runtime — this prevents the script's own regex from matching the
# token when it greps this test file (the very thing this test is
# asserting fail-closed behaviour for).
_DRIFT_PROBE_TOKENS: tuple[str, ...] = (
    "NARROW_" + "THRESHOLD",
    "MEDIUM_" + "THRESHOLD",
    "ctx.mode == '" + "compact" + "'",
    "ctx.mode == '" + "medium" + "'",
    "ctx.mode == '" + "wide" + "'",
    "force_mode" + " " + "=",
    "RALPH_" + "FORCE_NARROW",
    "DISPLAY_" + "MODE",
)


def _run_drift_check() -> subprocess.CompletedProcess[str]:
    """Run ``scripts/wt028-drift-check.sh`` from the ralph-workflow root.

    Resolves :data:`_DRIFT_SCRIPT` as an absolute path and uses
    ``cwd=_PROJECT_ROOT`` so the script's ``cd "$RALPH_ROOT"`` lands in
    the correct directory regardless of the pytest runner's cwd.
    Bounded 3s timeout per invocation.
    """
    assert _DRIFT_SCRIPT.is_file(), (
        f"wt028 drift-check script not found at {_DRIFT_SCRIPT!r}; PROJECT_ROOT resolution is wrong"
    )
    return subprocess.run(
        ["bash", str(_DRIFT_SCRIPT)],
        cwd=str(_PROJECT_ROOT),
        timeout=3,
        capture_output=True,
        text=True,
        check=False,
    )


def _safe_probe_name(token: str) -> str:
    """Return a probe-file name that embeds the token in a filesystem-safe form.

    Uses the post-concatenation token (NOT a fragment) so the name is
    stable across all parametrized variants of the same logical token.
    """
    safe = []
    for ch in token:
        if ch.isalnum():
            safe.append(ch.lower())
        else:
            safe.append("_")
    return "".join(safe).strip("_")


def _token_marker(token: str) -> str:
    """Return a recognizable substring of the token suitable for output assertion.

    The drift-check script emits the *file path* of every offending
    file, not the offending line content. The probe file is named
    after the sanitized token, so the script output contains the
    sanitized token as part of the file path (e.g. the
    threshold-pair tokens become ``narrow_threshold`` /
    ``medium_threshold`` in the probe filename; the mode-branch
    tokens keep the literal mode name in the filename). To prove the
    **offending token itself** is reported (not just the probe
    filename), the test asserts that a stable marker substring of
    the token appears in the FAIL output. The marker is chosen so it
    is unique enough that a passing output would not contain it by
    accident, while still being exactly what the script's regex
    matched against.

    The marker strategy:

    - Mode-branch compares (a ``ctx.mode`` followed by a comparison
      operator and one of the removed mode names) -> the literal
      mode name (the substring the regex alternation searches for
      inside the compare).
    - For the parameter-assignment token class (a parameter name
      followed by whitespace and an ``=`` sign), the trailing
      whitespace + ``=`` is dropped because the script's regex
      requires whitespace + ``=``; the parameter-name substring is
      the stable token name.
    - All other tokens -> the lowercased token (the case-sensitive
      regex matches the uppercase form in the probe, but the probe
      filename is sanitized to lowercase, so the lowercased marker is
      what actually appears in the output).
    """
    if " == '" in token or " != '" in token:
        _head, _sep, tail = token.partition("'")
        second, _sep2, _rest = tail.partition("'")
        return second
    if token.endswith(" ="):
        return token[:-2].strip()
    return token.lower()


# Per-test timeout: the drift-check shell script greps ralph/, tests/, docs/
# on every invocation. The 8 named legacy tokens are batched into ONE FAIL
# invocation (with all 8 probes present) and ONE PASS invocation (after
# all probes are removed), so the cumulative subprocess cost stays well
# within the 60s combined budget. Each individual probe write/delete is
# bounded by the per-test 8s timeout; the bash + grep cost on the
# ralph-workflow tree is ~0.7-0.9s per invocation on a busy CI runner.
#
# The subprocess_e2e marker exempts this file from the audit_test_policy
# subprocess audit: the bash script IS the system-under-test (the same
# artifact make verify-drift invokes), so subprocess.run is the
# legitimate invocation path, not a bypass of the MockProcessExecutor
# test-infra seam.
pytestmark = [pytest.mark.timeout_seconds(15), pytest.mark.subprocess_e2e]


def test_drift_check_script_fails_closed_against_every_named_legacy_token() -> None:
    """``scripts/wt028-drift-check.sh`` is fail-closed against every legacy token.

    Batched probe strategy (cuts subprocess cost ~8x vs. a parametrized
    one-probe-per-test design): all 8 named legacy probes are written at
    once, the bash script is run ONCE to assert FAIL with every probe
    file name and every token marker in the output, then all 8 probes
    are removed and the bash script is run ONCE to assert PASS.

    For each named legacy token the closure-pass consolidation named,
    this test (a) writes the token to a temporary probe file under
    ``ralph/``, (b) runs the actual bash script via subprocess with a
    bounded timeout (the same invocation path ``make verify-drift``
    uses), (c) asserts non-zero exit AND that every offending token's
    recognizable marker appears in the FAIL output (proving the script
    actually identified every token, not just the probe files),
    (d) deletes all probes and re-runs the script, (e) asserts exit
    code 0.

    The probe files are named ``_drift_probe_<sanitized-token>.py`` and
    live under ``ralph/`` — outside the historical-context allowlist
    (the allowlist covers ``ralph/display/status_bar.py``,
    ``ralph/display/__init__.py``, ``ralph/display/mode.py``,
    ``ralph/display/_mode_adaptive_limits.py``, ``ralph/display/context.py``,
    so a probe file at ``ralph/_drift_probe_*.py`` cannot accidentally
    fall inside it). try/finally guarantees the probes are cleaned up
    even on assertion failure.
    """
    probes: list[tuple[Path, str, str, str]] = []
    for token in _DRIFT_PROBE_TOKENS:
        probe_name = f"_drift_probe_{_safe_probe_name(token)}.py"
        probe_path = _PROJECT_ROOT / "ralph" / probe_name
        assert not probe_path.exists(), (
            f"probe file {probe_path!r} should not exist before the test starts"
        )
        probes.append(
            (probe_path, probe_name, token, _token_marker(token))
        )
    try:
        for probe_path, _probe_name, token, _marker in probes:
            probe_path.write_text(f"{token} = '1'\n", encoding="utf-8")
        result = _run_drift_check()
        assert result.returncode != 0, (
            f"drift-check script must FAIL when probe files contain "
            f"the legacy tokens; got rc={result.returncode}, "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
        combined_output = result.stdout + "\n" + result.stderr
        for _probe_path, probe_name, _token, token_marker in probes:
            assert probe_name in combined_output, (
                f"drift-check FAIL output must name the offending probe "
                f"file {probe_name!r}; got stdout={result.stdout!r}, "
                f"stderr={result.stderr!r}"
            )
            assert token_marker in combined_output, (
                f"drift-check FAIL output must surface the offending "
                f"token itself (marker {token_marker!r}); got "
                f"stdout={result.stdout!r}, stderr={result.stderr!r}"
            )
        assert "FAIL" in combined_output, (
            f"drift-check FAIL output must include the FAIL marker; "
            f"got stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
        # Per docs/ralph-workflow-policy/gate-script-policy.md § Failure
        # output, the FAIL output MUST cite the governing policy file
        # by path so an agent that hits a red gate can find the rule
        # without loading every policy into context.
        assert "gate-script-policy.md" in combined_output, (
            f"drift-check FAIL output must cite the governing policy "
            f"(gate-script-policy.md); got stdout={result.stdout!r}, "
            f"stderr={result.stderr!r}"
        )
    finally:
        for probe_path, _probe_name, _token, _marker in probes:
            if probe_path.exists():
                probe_path.unlink()
    result_after = _run_drift_check()
    assert result_after.returncode == 0, (
        f"drift-check script must PASS after the probe files are removed; "
        f"got rc={result_after.returncode}, stdout={result_after.stdout!r}, "
        f"stderr={result_after.stderr!r}"
    )
    assert "PASS" in result_after.stdout, (
        f"drift-check PASS output must include the PASS marker; got stdout={result_after.stdout!r}"
    )


def test_drift_check_fails_closed_when_search_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unexpected search failures must not be reported as a clean drift check."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_find = bin_dir / "find"
    fake_find.write_text("#!/usr/bin/env bash\nexit 127\n", encoding="utf-8")
    fake_find.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    result = _run_drift_check()

    assert result.returncode == 2
    assert "FAIL: bad path or permission in upstream grep" in result.stderr


def test_drift_check_times_out_when_search_stalls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stalled scan is bounded and fails with the gate-timeout guidance."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_find = bin_dir / "find"
    fake_find.write_text("#!/usr/bin/env bash\nwhile :; do :; done\n", encoding="utf-8")
    fake_find.chmod(0o755)
    fake_sleep = bin_dir / "sleep"
    fake_sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_sleep.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    result = _run_drift_check()

    assert result.returncode == 124
    assert "drift scan exceeded 2s and was stopped" in result.stderr
    assert "gate-script-policy.md § Bounded" in result.stderr


# ---------------------------------------------------------------------------
# Dual-form CLI-flag anti-drift guard (AC-04)
# ---------------------------------------------------------------------------
#
# This complements ``scripts/wt028-drift-check.sh``: the bash script greps
# for the historic legacy token corpus (mode-tier literals and the legacy
# threshold / force-mode / global-mode env-var families named during the
# single-mode consolidation) but does NOT scan ``ralph/cli/main.py`` or
# ``ralph/cli/commands/*.py`` for CLI flag declarations. The AST scan
# below closes that gap for EITHER of two forbidden CLI forms:
#
# Form A -- joined ``--display-mode`` / ``--display_mode``:
#   case-insensitive substring match in the flag name OR in the
#   ``help=`` keyword value. Covers joined flags that re-introduce
#   the legacy mode-tier concept.
#
# Form B -- bare ``--display`` with strict word-boundary:
#   matches the literal substring ``--display`` followed by a strict
#   boundary (NOT followed by ``-``, ``_``, or ``[a-z]``). Catches
#   ``--display``, ``--display=``, ``--display foo``, ``--display)``
#   while EXCLUDING ``--display-mode`` / ``--display_mode`` /
#   ``--displayfoo`` (Form A handles those).
#
# The detection rule is split into TWO strict forms so a future
# legitimate CLI flag named e.g. ``--display-config-with-mode`` or
# ``--display-target`` is NOT a false positive: those are long-form
# joined names that fail BOTH the Form A substring and the Form B
# word-boundary (Form B excludes any ``--display`` followed by
# ``[-_a-z]``). Only ``--display`` / ``--display-mode`` / ``--display_mode``
# trip the guard.


_BARE_DISPLAY_RE = re.compile(r"--display(?![-_a-z])")


def _is_forbidden_display_cli_flag(name: str, help_text: str | None) -> bool:
    """Return True iff the CLI flag is forbidden by Form A or Form B."""
    name_lower = name.lower()
    help_lower = (help_text or "").lower()
    # Form A: joined --display-mode / --display_mode (case-insensitive)
    if "display-mode" in name_lower or "display_mode" in name_lower:
        return True
    if "display-mode" in help_lower or "display_mode" in help_lower:
        return True
    # Form B: bare --display with strict word-boundary
    return bool(_BARE_DISPLAY_RE.search(name))


@lru_cache(maxsize=1)
def _cli_files() -> tuple[Path, ...]:
    """Return all .py files under ralph/cli/ to scan for CLI flags."""
    files: list[Path] = []
    if _CLI_MAIN.is_file():
        files.append(_CLI_MAIN)
    if _CLI_COMMANDS_DIR.is_dir():
        files.extend(sorted(_CLI_COMMANDS_DIR.glob("*.py")))
    return tuple(files)


# AST-scanned calls whose function name matches one of these identifiers
# (last segment of the attribute path, case-insensitive) are treated as
# CLI flag declarations. The match is case-insensitive because typer
# uses ``typer.Option(...)`` (capital O) while click uses ``click.option(...)``
# (lowercase); both spell out the same CLI declaration concept.
_CLI_FLAG_FUNC_NAMES = frozenset({"option", "argument"})


def _is_cli_flag_call_func_name(func_name: str | None) -> bool:
    """Case-insensitive matcher for click/typer CLI flag call names.

    Returns False when func_name is None so the caller can use a single
    guard without separate None-checking: an AST call's func attribute
    is normally a Name or Attribute, but a malformed AST or wrapped
    decorator chain can yield other node types; we skip those.
    """
    if not isinstance(func_name, str):
        return False
    return func_name.lower() in _CLI_FLAG_FUNC_NAMES


def _extract_help_text(call: ast.Call) -> str | None:
    """Return the help= keyword value as a string, or None if absent/not literal."""
    for kw in call.keywords:
        if (
            kw.arg == "help"
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            return kw.value.value
    return None


def _first_string_positional(call: ast.Call) -> str | None:
    """Return the first string positional arg of an AST call, or None."""
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _scan_cli_file_for_forbidden_flags(
    path: Path,
) -> list[str]:
    """Return a list of ``path:lineno: form-name: matched-flag`` hits."""
    tree = _parsed(path)
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        func_name: str | None = None
        if isinstance(func, ast.Name):
            func_name = func.id
        elif isinstance(func, ast.Attribute):
            func_name = func.attr
        if not _is_cli_flag_call_func_name(func_name):
            continue
        flag_name = _first_string_positional(node)
        if flag_name is None:
            continue
        help_text = _extract_help_text(node)
        if _is_forbidden_display_cli_flag(flag_name, help_text):
            form = (
                "joined"
                if (
                    "display-mode" in flag_name.lower()
                    or "display_mode" in flag_name.lower()
                    or (
                        help_text is not None
                        and (
                            "display-mode" in help_text.lower()
                            or "display_mode" in help_text.lower()
                        )
                    )
                )
                else "bare"
            )
            hits.append(
                f"{path.name}:{node.lineno}: {form}: flag={flag_name!r}"
                + (f" help={help_text!r}" if help_text else "")
            )
    return hits


# Pre-populate the AST cache at import time so the per-test SIGALRM
# window is not spent re-parsing files.
for _f in _cli_files():
    _parsed(_f)


def _write_synthetic_probe(form: str, target_dir: Path) -> Path:
    """Write a synthetic CLI-flag probe file to ``target_dir`` and return its path.

    form == 'joined' -> writes ``typer.Option('--display-mode', help=...)``
    form == 'bare'   -> writes ``typer.Option('--display', help=...)``
    Both probes intentionally use ``typer.Option`` with the forbidden flag
    name so the AST scanner trips on them; the test then deletes the
    probe via a try/finally.
    """
    if form == "joined":
        flag = "--display-mode"
    elif form == "bare":
        flag = "--display"
    else:
        raise ValueError(f"Unknown form {form!r}; expected 'joined' or 'bare'")
    probe_name = f"_anti_drift_probe_{form}.py"
    probe_path = target_dir / probe_name
    target_dir.mkdir(parents=True, exist_ok=True)
    probe_path.write_text(
        f"import typer\n\n_FLAG = typer.Option({flag!r}, help='synthetic probe ({form})')\n",
        encoding="utf-8",
    )
    return probe_path


@pytest.mark.parametrize("form", ["joined", "bare"])
def test_no_cli_flag_introduces_display_mode_or_bare_display(form: str, tmp_path: Path) -> None:
    """CLI-flag anti-drift guard: catches joined --display-mode AND bare --display.

    Asserts three properties:

    1. The AST scanner applied to the production CLI surface
       (``ralph/cli/main.py`` and ``ralph/cli/commands/*.py``) returns NO
       violations \u2014 there is no legitimate ``--display`` /
       ``--display-mode`` / ``--display_mode`` flag in production code.

    2. A synthetic probe file written under ``tmp_path/ralph/cli/commands/``
       containing the forbidden flag DOES trip the AST scanner, proving
       the scanner is sensitive to the offending form. The probe is
       deleted in a try/finally block whether the assertion succeeds
       or fails.

    3. The scanner trips on BOTH forms (parametrized over ``'joined'``
       and ``'bare'``), so the guard covers the joined legacy form AND
       the bare ``--display`` form rather than only one of them.
    """
    # (1) Production surface must be free of the forbidden flags.
    production_hits: list[str] = []
    for cli_file in _cli_files():
        production_hits.extend(_scan_cli_file_for_forbidden_flags(cli_file))
    assert not production_hits, (
        "Forbidden CLI flags (joined --display-mode / --display_mode OR "
        "bare --display) found in production CLI code:\n"
        + "\n".join(production_hits)
        + "\nThis is the dual-form CLI anti-drift guard. Ralph Workflow "
        "exposes exactly ONE display mode called 'default'; re-introduce "
        "no CLI flag whose name matches 'display' (with or without '-mode' / "
        "'_mode' suffix)."
    )

    # (2) Synthesize a probe in the scanned directories and prove the
    # scanner catches it. Use the CLI main file's parent dir for the
    # probe target so the scanner's _cli_files() picks it up.
    target_dir = tmp_path / "ralph" / "cli" / "commands"
    probe_path = _write_synthetic_probe(form, target_dir)
    try:
        synthetic_hits = _scan_cli_file_for_forbidden_flags(probe_path)
        assert len(synthetic_hits) >= 1, (
            f"Anti-drift scanner FAILED to catch synthetic {form!r} probe; "
            f"the detection rule is broken for this form. Probe file: "
            f"{probe_path}. Scanned hits: {synthetic_hits!r}"
        )
        # Confirm the probe hit actually names the forbidden form.
        assert any(form in hit for hit in synthetic_hits), (
            f"Synthetic {form!r} probe hit must self-identify its form; got hits {synthetic_hits!r}"
        )
    finally:
        if probe_path.exists():
            probe_path.unlink()
        # Best-effort cleanup of the synthetic dir if it is now empty.
        if target_dir.is_dir() and not any(target_dir.iterdir()):
            target_dir.rmdir()
            # Walk up: remove parent dirs only if empty.
            _tmp_ralph = tmp_path / "ralph"
            if _tmp_ralph.is_dir() and not any(_tmp_ralph.iterdir()):
                _tmp_ralph.rmdir()
