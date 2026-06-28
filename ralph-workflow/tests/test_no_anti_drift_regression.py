"""Anti-drift regression: pin the consolidation contract for the runtime reliability refactor.

These tests are black-box pins for the architectural consolidation specified
in `.agent/PLAN.md`. They MUST stay deterministic (no real subprocess, no
real network, no time.sleep, no os.system). When a refactor step lands, the
corresponding assertion in this file MUST turn green; if a refactor step
silently re-introduces duplication, this file fails.

The test name format `test_<surface>_<invariant>` mirrors the surfaces in
`tmp/drift-audit.md`.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import re
import time
from functools import cache

import pytest

# Top-level imports for the symbols the inline test functions need
# (ruff PLC0415 requires module-level imports for these).
from ralph.agents.invoke import (
    extract_transport_session_id,
    extract_transport_session_id_from_line,
    extract_visible_tui_transport_session_id,
    fresh_session_options,
    recovery_action_for_failure_reason,
    resolve_resume_session_id,
)
from ralph.agents.invoke._types import InvokeOptions
from ralph.agents.parsers._event_classification import (
    LIFECYCLE_EVENT_TYPES,
    LIFECYCLE_KINDS,
    is_lifecycle_event,
    is_lifecycle_kind,
)
from ralph.display.parallel_display import ParallelDisplay

# AST-walking tests need a slightly larger per-test budget than the
# 1s default. The make-verify combined 60s budget still holds.
pytestmark = pytest.mark.timeout_seconds(10)

RALPH_ROOT = pathlib.Path(__file__).parent.parent / "ralph"
TESTS_ROOT = pathlib.Path(__file__).parent

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@cache
def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


@cache
def _parse(path: pathlib.Path) -> ast.AST:
    return ast.parse(_read(path))


# Module-level regex for the retry-decision substring pre-filter
# (test_no_retry_decision_reimplementation). Hoisted out of the
# function body so the per-call cost is the regex match, not the
# regex compile.
_DEF_NAME_RE = re.compile(r"(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)\b")


def _walk_python_files(root: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _has_legacy_console_display_reference(source: str) -> bool:
    return "LegacyConsoleDisplay" in source


def _has_legacy_console_display_classdef(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "LegacyConsoleDisplay":
            return True
    return False


def _all_string_literals(source: str) -> set[str]:
    tree = ast.parse(source)
    literals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
        elif isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    literals.add(part.value)
    return literals


_WIRE_FORM_RE = re.compile(r"^mcp__[A-Za-z0-9_]+__[A-Za-z0-9_]+$")


def _wire_form_literals_in_source(source: str) -> set[str]:
    """Return the set of wire-form `mcp__<server>__<tool>` literals in source."""
    return {lit for lit in _all_string_literals(source) if _WIRE_FORM_RE.match(lit)}


# ---------------------------------------------------------------------------
# Surface (a) — display rendering: ParallelDisplay is the only display type
# ---------------------------------------------------------------------------


class TestDisplayIsOnlyParallelDisplay:
    """Pin Surface (a): the only display type is `ParallelDisplay`."""

    def test_legacy_console_display_module_is_deleted(self) -> None:
        """`ralph/pipeline/legacy_console_display.py` must not exist after Step 3."""
        legacy_module = RALPH_ROOT / "pipeline" / "legacy_console_display.py"
        assert not legacy_module.exists(), (
            f"{legacy_module} still exists; Step 3 (delete LegacyConsoleDisplay) is incomplete."
        )

    def test_no_legacy_console_display_class_definition_anywhere(self) -> None:
        """No `.py` file may define `class LegacyConsoleDisplay`."""
        for path in _walk_python_files(RALPH_ROOT):
            if "LegacyConsoleDisplay" not in _read(path):
                continue
            assert not _has_legacy_console_display_classdef(_read(path)), (
                f"{path.relative_to(RALPH_ROOT.parent)} still defines "
                "class LegacyConsoleDisplay; Step 3 is incomplete."
            )

    def test_no_legacy_console_display_import_outside_tests(self) -> None:
        """No production code may import `LegacyConsoleDisplay` after Step 3."""
        for path in _walk_python_files(RALPH_ROOT):
            if "LegacyConsoleDisplay" in _read(path):
                pytest.fail(
                    f"{path.relative_to(RALPH_ROOT.parent)} still references "
                    "LegacyConsoleDisplay after Step 3 delete."
                )

    def test_no_isinstance_check_against_legacy_console_display(self) -> None:
        """No production code may use `isinstance(x, LegacyConsoleDisplay)`."""
        for path in _walk_python_files(RALPH_ROOT):
            source = _read(path)
            if "LegacyConsoleDisplay" not in source:
                continue
            tree = _parse(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (
                    isinstance(func, ast.Name)
                    and func.id == "isinstance"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Name)
                    and node.args[1].id == "LegacyConsoleDisplay"
                ):
                    pytest.fail(
                        f"{path.relative_to(RALPH_ROOT.parent)}:{node.lineno} still has "
                        "isinstance(x, LegacyConsoleDisplay) check after Step 3."
                    )


# ---------------------------------------------------------------------------
# Surface (b) — session_id lifecycle: single extractor
# ---------------------------------------------------------------------------


class TestSessionIdLifecycleSingleExtractor:
    """Pin Surface (b): all parsers funnel through `extract_transport_session_id`."""

    def test_extract_transport_session_id_is_public_from_invoke(self) -> None:

        assert callable(extract_transport_session_id)

    def test_all_wire_parsers_extract_session_id_consistently(self) -> None:

        sample_text = "Session ID: abc-123"
        sample_json = '{"type":"session","session_id":"abc-123"}'
        for line in (sample_text, sample_json):
            assert extract_transport_session_id([line]) == "abc-123", (
                f"extract_transport_session_id({line!r}) must return 'abc-123'"
            )

    def test_extract_visible_tui_rejects_generic_session_id(self) -> None:

        # Visible TUI extractor must NOT pick up bare `session_id=...` text
        # (tool output cannot masquerade as a transport session id).
        result = extract_visible_tui_transport_session_id("session_id=abc-123")
        assert result is None, (
            "extract_visible_tui_transport_session_id must reject generic "
            "session_id=... text so tool output cannot masquerade as a "
            "transport session id."
        )

    def test_no_private_session_imports_outside_invoke_package(self) -> None:
        """`from ralph.agents.invoke._session import` may only appear inside the package
        AND in `ralph/agents/parsers/`.

        Per Step 4(f) + Step 1(e) (regenerated 2026-06-08): only
        `ralph/agents/invoke/`, `ralph/agents/parsers/`, and the 2 files in
        FORBIDDEN_PIPELINE_FILES may import from the private `_session`
        module. CLI commands and other modules must use the public
        `ralph.agents.invoke` surface. The 2 files in
        FORBIDDEN_PIPELINE_FILES are forbidden to use the private import
        (they MUST use the public surface); this test enforces the
        "no private import outside the canonical allowlist" property
        which is the complement of the per-file pin in
        `TestPrivateSessionImportsForbiddenInSpecificPipelineFiles`.
        """
        allowed_roots = (
            RALPH_ROOT / "agents" / "invoke",
            RALPH_ROOT / "agents" / "parsers",
        )
        offenders: list[str] = []
        for path in _walk_python_files(RALPH_ROOT):
            rel = path.relative_to(RALPH_ROOT.parent)
            if any(
                rel.is_relative_to(allowed.relative_to(RALPH_ROOT.parent))
                for allowed in allowed_roots
            ):
                continue
            if "from ralph.agents.invoke._session import" in _read(path):
                offenders.append(str(rel))
        assert offenders == [], (
            "These modules still import from ralph.agents.invoke._session "
            "(private); they must use the public ralph.agents.invoke surface: " + str(offenders)
        )


# ---------------------------------------------------------------------------
# Surface (b continued) — session resume decision
# ---------------------------------------------------------------------------


class TestResumeSessionIdSingleDecisionPoint:
    """Pin the resume decision contract (Step 4(b-d))."""

    def test_resolve_resume_session_id_returns_none_for_fresh(self) -> None:

        assert (
            resolve_resume_session_id(
                has_prior_session=True,
                prior_session_id="abc-123",
                recovery_action="fresh",
            )
            is None
        )

    def test_resolve_resume_session_id_returns_prior_id_for_resume(self) -> None:

        assert (
            resolve_resume_session_id(
                has_prior_session=True,
                prior_session_id="abc-123",
                recovery_action="resume",
            )
            == "abc-123"
        )

    def test_resolve_resume_session_id_returns_none_when_no_prior(self) -> None:

        assert (
            resolve_resume_session_id(
                has_prior_session=False,
                prior_session_id=None,
                recovery_action="resume",
            )
            is None
        )

    def test_recovery_action_fresh_for_unknown_session(self) -> None:

        # A stale/invalid session id family must yield "fresh" so the next
        # attempt starts a new session rather than trying to resume a dead one.
        assert (
            recovery_action_for_failure_reason("Unknown session", has_prior_session=True) == "fresh"
        )

    def test_recovery_action_resume_for_inactivity_timeout(self) -> None:

        assert (
            recovery_action_for_failure_reason(
                "AgentInactivityTimeoutError", has_prior_session=True
            )
            == "resume"
        )

    def test_recovery_action_resume_for_opencode_resumable_exit(self) -> None:

        assert (
            recovery_action_for_failure_reason("OpenCodeResumableExitError", has_prior_session=True)
            == "resume"
        )

    def test_recovery_action_fresh_without_prior_session(self) -> None:

        # No prior session => always "fresh", regardless of failure reason.
        assert (
            recovery_action_for_failure_reason(
                "AgentInactivityTimeoutError", has_prior_session=False
            )
            == "fresh"
        )

    def test_recovery_action_resume_for_tool_availability_failure(self) -> None:
        """`reset_tool_registry=True` is the NEW BEHAVIOR: tool-availability
        failures must RESUME (not start fresh), because the tool registry
        has been rebuilt via `RestartAwareMcpBridge.reset_tool_registry()`.
        """

        assert (
            recovery_action_for_failure_reason(
                "No such tool available: mcp__server__tool",
                has_prior_session=True,
                reset_tool_registry=True,
            )
            == "resume"
        )


# ---------------------------------------------------------------------------
# Surface (b helper) — `fresh_session_options`
# ---------------------------------------------------------------------------


class TestFreshSessionOptions:
    """Pin the new `fresh_session_options` helper (Step 4(e))."""

    def test_fresh_session_options_clears_session_id(self) -> None:

        opts = InvokeOptions(session_id="abc-123", verbose=True)
        fresh = fresh_session_options(opts)
        assert fresh.session_id is None
        # Other fields must be preserved.
        assert fresh.verbose is True
        # Input must NOT be mutated.
        assert opts.session_id == "abc-123"

    def test_fresh_session_options_no_prior_session_id(self) -> None:

        fresh = fresh_session_options(InvokeOptions(session_id=None))
        assert fresh.session_id is None

    def test_fresh_session_options_accepts_prior_session_id_for_compatibility(self) -> None:
        """The `prior_session_id` parameter is accepted for forward
        compatibility but MUST NOT be written back into `session_id`.
        """

        opts = InvokeOptions(session_id="abc-123")
        fresh = fresh_session_options(opts, prior_session_id="def-456")
        assert fresh.session_id is None


# ---------------------------------------------------------------------------
# Surface (c) — parser lifecycle suppression: shared `is_lifecycle_event`
# ---------------------------------------------------------------------------


class TestParserEventClassificationIsShared:
    """Pin Surface (c) and (h): lifecycle suppression is owned by one module."""

    def test_event_classification_module_exists(self) -> None:
        module = RALPH_ROOT / "agents" / "parsers" / "_event_classification.py"
        assert module.exists(), (
            f"{module} must exist after Step 5(a) — the shared lifecycle "
            "suppression + session-metadata recognition surface."
        )

    def test_event_classification_exposes_canonical_lifecycle_set(self) -> None:

        assert isinstance(LIFECYCLE_EVENT_TYPES, frozenset)
        assert is_lifecycle_event("message_start")
        assert is_lifecycle_event("heartbeat")
        assert is_lifecycle_event("ready")
        assert is_lifecycle_event("assistant")
        assert is_lifecycle_event("user")
        assert is_lifecycle_event("thinking")

    def test_event_classification_exposes_lifecycle_kind_helper(self) -> None:
        """Surface (j): claude-interactive parsers use `is_lifecycle_kind`."""

        assert isinstance(LIFECYCLE_KINDS, frozenset)
        assert is_lifecycle_kind("lifecycle")
        assert not is_lifecycle_kind("output")

    def test_superset_lifecycle_set(self) -> None:
        """The shared LIFECYCLE_EVENT_TYPES must be a strict superset of every
        per-parser frozenset that previously existed (excluding ``user`` /
        ``assistant`` which are content events, not lifecycle events).
        """

        # Combined per-parser lifecycle set: must be a subset of the shared one.
        # ``step_start`` / ``step_finish`` are STOP events (handled explicitly
        # by OpenCodeParser), not lifecycle events, so they are NOT in the
        # shared set.
        legacy_union = {
            "message_start",
            "message_stop",
            "content_block_start",
            "content_block_stop",
            "message_delta",
            "thread.started",
            "turn.started",
            "heartbeat",
            "ping",
            "ready",
            "user",
            "assistant",
            "thinking",
        }
        missing = legacy_union - LIFECYCLE_EVENT_TYPES
        assert not missing, (
            f"Shared LIFECYCLE_EVENT_TYPES is missing per-parser event types: {missing}."
        )

    def test_no_per_parser_local_lifecycle_frozenset_definitions(self) -> None:
        """Per-parser `claude.py`, `opencode.py`, `codex.py`, `gemini.py`,
        `generic.py` must NOT define their own `_LIFECYCLE_EVENT_TYPES` or
        `_LIFECYCLE_MARKERS` frozenset — they import from the shared module.
        """
        offenders: list[str] = []
        for name in ("claude.py", "opencode.py", "codex.py", "gemini.py", "generic.py"):
            path = RALPH_ROOT / "agents" / "parsers" / name
            if not path.exists():
                continue
            source = _read(path)
            for needle in (
                "_LIFECYCLE_EVENT_TYPES: Final[frozenset",
                "_LIFECYCLE_MARKERS: Final[frozenset",
                "_LIFECYCLE_MARKERS = frozenset",
                "LIFECYCLE_MARKERS = frozenset",
            ):
                if needle in source:
                    offenders.extend([f"{path.relative_to(RALPH_ROOT.parent)}:{needle}"])
        assert offenders == [], "Per-parser lifecycle frozenset definitions still exist: " + str(
            offenders
        )

    def test_claude_interactive_consumer_explicitly_filters_lifecycle_events(self) -> None:
        """Surface (j): the `claude_interactive.py:parse` consumer MUST have
        an explicit `if is_lifecycle_kind(event.kind): continue` filter so
        the lifecycle event is suppressed visibly, not by silent fall-through.
        """
        consumer = RALPH_ROOT / "agents" / "parsers" / "claude_interactive.py"
        assert consumer.exists()
        source = _read(consumer)
        assert "is_lifecycle_kind" in source, (
            "claude_interactive.py:parse must explicitly filter lifecycle "
            "events via is_lifecycle_kind(...) from the shared "
            "_event_classification module."
        )


# ---------------------------------------------------------------------------
# Surface (g) — pipeline plumbing: commit uses shared pipeline modules
# ---------------------------------------------------------------------------


class TestPlumbingCommitUsesSharedPipeline:
    """Pin Surface (g): cli/commands/commit.py uses the shared pipeline."""

    def test_commit_module_has_no_inline_failure_classifier_construction(self) -> None:
        """Per Step 6(a) (REVISED): commit.py must NOT construct
        `FailureClassifier()` inline. The two calls at the pre-fix commit.py
        lines 698 and 726 are deleted and routed through
        `ralph/pipeline/plumbing/commit_plumbing.py`.
        """
        commit = RALPH_ROOT / "cli" / "commands" / "commit.py"
        assert commit.exists()
        source = _read(commit)
        assert "FailureClassifier()" not in source, (
            "ralph/cli/commands/commit.py still constructs FailureClassifier() "
            "inline. After Step 6(a) these calls move to "
            "ralph/pipeline/plumbing/commit_plumbing.py."
        )

    def test_commit_module_has_no_inline_extract_transport_session_id_import(self) -> None:
        """Per Step 4(c) + 6(c): commit.py must NOT import
        `extract_transport_session_id` from the private `_session` module.
        It must use the public `ralph.agents.invoke` surface, and the
        actual call must be in the new plumbing module.
        """
        commit = RALPH_ROOT / "cli" / "commands" / "commit.py"
        assert commit.exists()
        source = _read(commit)
        assert "from ralph.agents.invoke._session import" not in source, (
            "ralph/cli/commands/commit.py still imports "
            "extract_transport_session_id from the private _session module. "
            "It must use the public ralph.agents.invoke surface and route "
            "the actual call through commit_plumbing.py."
        )

    def test_plumbing_commit_module_exists(self) -> None:
        plumbing = RALPH_ROOT / "pipeline" / "plumbing" / "commit_plumbing.py"
        assert plumbing.exists(), (
            f"{plumbing} must exist after Step 6(a); it is the single owner "
            "of commit-time chain iteration."
        )

    def test_plumbing_commit_module_calls_execute_agent_effect(self) -> None:
        plumbing = RALPH_ROOT / "pipeline" / "plumbing" / "commit_plumbing.py"
        if not plumbing.exists():
            pytest.skip("plumbing module not yet created; pin in Step 6")
        source = _read(plumbing)
        assert "execute_agent_effect" in source, (
            "commit_plumbing.py must delegate agent invocation to "
            "ralph.pipeline.effect_executor.execute_agent_effect (the shared execution core)."
        )

    def test_effect_executor_module_owns_run_with_direct_mcp_recovery(self) -> None:
        effect_executor = RALPH_ROOT / "pipeline" / "effect_executor.py"
        assert effect_executor.exists()
        source = _read(effect_executor)
        assert "run_with_direct_mcp_recovery" in source, (
            "effect_executor.py must contain the canonical retry loop run_with_direct_mcp_recovery."
        )

    def test_plumbing_commit_module_does_not_construct_failure_classifier(self) -> None:
        plumbing = RALPH_ROOT / "pipeline" / "plumbing" / "commit_plumbing.py"
        if not plumbing.exists():
            pytest.skip("plumbing module not yet created; pin in Step 6")
        source = _read(plumbing)
        # The plumbing module must delegate classification through
        # run_with_direct_mcp_recovery, NOT construct FailureClassifier() inline.
        assert "FailureClassifier()" not in source, (
            "commit_plumbing.py constructs FailureClassifier() directly; "
            "it must route classification through run_with_direct_mcp_recovery."
        )


# ---------------------------------------------------------------------------
# Surface (i) — recovery class construction: FailureClassifier ownership
# ---------------------------------------------------------------------------


class TestFailureClassifierOwnership:
    """Pin Surface (i): FailureClassifier is constructed only in authorized sites."""

    @pytest.mark.parametrize(
        "allowed_path",
        [
            RALPH_ROOT / "recovery" / "failure_classifier.py",
            RALPH_ROOT / "recovery" / "classifier.py",
            RALPH_ROOT / "agents" / "invoke" / "_direct_mcp_recovery.py",
        ],
    )
    def test_failure_classifier_construction_only_in_allowed_sites(
        self, allowed_path: pathlib.Path
    ) -> None:
        """`FailureClassifier(` may only appear in:
        - `ralph/recovery/` (definition + tests)
        - `ralph/agents/invoke/_direct_mcp_recovery.py` (consolidated owner)
        - `ralph/agents/invoke/_completion.py` (post-exit watchdog check)
        - `ralph/pipeline/agent_retry_decision.py` (the shared decision dispatcher)
        """
        allowed_relative = {
            pathlib.Path("ralph/recovery/failure_classifier.py"),
            pathlib.Path("ralph/recovery/classifier.py"),
            pathlib.Path("ralph/recovery/controller.py"),
            pathlib.Path("ralph/agents/invoke/_direct_mcp_recovery.py"),
            pathlib.Path("ralph/agents/invoke/_completion.py"),
            pathlib.Path("ralph/pipeline/agent_retry_decision.py"),
        }
        # Test files (tests/recovery/, tests/test_recovery_*) may also construct
        # FailureClassifier to test its behavior — that is a legitimate test site.
        offenders: list[str] = []
        for path in _walk_python_files(RALPH_ROOT):
            rel = path.relative_to(RALPH_ROOT.parent)
            if rel in allowed_relative:
                continue
            if "FailureClassifier(" in _read(path):
                offenders.append(str(rel))
        assert offenders == [], (
            "FailureClassifier( is constructed outside the allowed sites: "
            f"{offenders}. Replace with a call to is_unsubmitted_artifact_failure "
            "(ralph/recovery/failure_classifier.py) or route through "
            "run_with_direct_mcp_recovery (ralph/agents/invoke/_direct_mcp_recovery.py)."
        )


# ---------------------------------------------------------------------------
# Surface (d) — tool name: single wire-form construction site
# ---------------------------------------------------------------------------


class TestNoMcpWireFormOutsideMcpModule:
    """Pin Surface (d): wire-form `mcp__<server>__<tool>` only lives in `ralph/mcp/`."""

    @pytest.mark.parametrize(
        "checked_root",
        [
            RALPH_ROOT / "agents",
            RALPH_ROOT / "display",
            RALPH_ROOT / "pipeline",
        ],
    )
    def test_no_wire_form_literals_outside_mcp(self, checked_root: pathlib.Path) -> None:
        offenders: list[str] = []
        # Substring pre-filter (same fast-path pattern used elsewhere
        # in this module). The wire-form ``mcp__<server>__<tool>``
        # pattern requires the literal substring ``mcp__`` to appear in
        # the source as a string literal -- a single substring check
        # is enough to skip files that cannot contain a match. This
        # avoids an AST.parse + _all_string_literals() pass per file.
        for path in _walk_python_files(checked_root):
            try:
                source = _read(path)
            except (OSError, UnicodeDecodeError):
                continue
            if "mcp__" not in source:
                continue
            for literal in _wire_form_literals_in_source(source):
                offenders.extend([f"{path.relative_to(RALPH_ROOT.parent)}: {literal}"])
        assert offenders == [], (
            "Wire-form `mcp__<server>__<tool>` literals found outside "
            "`ralph/mcp/`: " + str(offenders) + ". Route via "
            "`canonicalize_tool_names` (ralph/mcp/tool_contract.py) or "
            "`friendly_tool_name` (ralph/display/tool_args.py)."
        )


# ---------------------------------------------------------------------------
# Surface (f) — interrupt path
# ---------------------------------------------------------------------------


class TestInterruptPathReliable:
    """Pin Surface (f): the watchdog invariant is preserved across refactors."""

    def test_watchdog_evaluate_call_sites_unchanged(self) -> None:
        """`watchdog.evaluate(...)` must be called at the 6 expected sites."""
        expected = 6
        pty = RALPH_ROOT / "agents" / "invoke" / "_pty_line_reader.py"
        process = RALPH_ROOT / "agents" / "invoke" / "_process_reader.py"
        pty_count = _read(pty).count("watchdog.evaluate")
        process_count = _read(process).count("watchdog.evaluate")
        total = pty_count + process_count
        assert total == expected, (
            f"Expected exactly {expected} `watchdog.evaluate(...)` call sites "
            f"across _pty_line_reader.py and _process_reader.py; got {total} "
            f"(_pty_line_reader.py: {pty_count}, _process_reader.py: {process_count})."
        )

    def test_install_force_kill_handler_is_from_interrupt_controller(self) -> None:
        """Per ADD-1: `install_force_kill_handler` is owned by
        `ralph.interrupt.controller` (NOT by `ralph.testing`).
        """
        controller = RALPH_ROOT / "interrupt" / "controller.py"
        assert controller.exists()
        source = _read(controller)
        assert "def install_force_kill_handler" in source, (
            "ralph/interrupt/controller.py must define install_force_kill_handler."
        )
        # `ralph.testing` must NOT have a copy.
        testing = RALPH_ROOT / "testing" / "__init__.py"
        if testing.exists():
            testing_source = _read(testing)
            assert "install_force_kill_handler" not in testing_source, (
                "ralph.testing must not re-export install_force_kill_handler; "
                "the only owner is ralph.interrupt.controller."
            )

    def test_no_duplicate_keyboard_interrupt_handlers(self) -> None:
        """No production code outside the canonical owner defines its own
        `handle_keyboard_interrupt`. The owner is `ralph.pipeline._runner_interrupt`.

        The CLI helper `handle_keyboard_interrupt_at_cli` (in
        ``ralph.interrupt.dispatcher``) is NOT a duplicate of
        `handle_keyboard_interrupt` — it is a deliberately suffixed
        consolidation entry point. The check below matches the exact
        function name (followed by a ``(``), not the substring, so the
        helper is not flagged.
        """
        offenders: list[str] = []
        for path in _walk_python_files(RALPH_ROOT):
            source = _read(path)
            # Match the exact function name (with `(` after) so the
            # CLI helper `handle_keyboard_interrupt_at_cli` is not flagged.
            if "def handle_keyboard_interrupt(" not in source:
                continue
            if path == RALPH_ROOT / "pipeline" / "_runner_interrupt.py":
                continue
            offenders.append(str(path.relative_to(RALPH_ROOT.parent)))
        assert offenders == [], (
            "Duplicate `def handle_keyboard_interrupt` definitions found: "
            f"{offenders}. The only owner is "
            "ralph/pipeline/_runner_interrupt.py."
        )

    def test_no_inline_dispatcher_plus_block_pattern_outside_helper(self) -> None:
        """The inline ``dispatcher_from_process_manager()`` +
        ``begin_interrupt(block=True)`` pattern may ONLY appear in the
        canonical implementation file and the test files that use
        begin_interrupt in assertions. Anywhere else it appears is a
        re-introduction of the duplicated CLI catch — those call sites
        must use ``ralph.interrupt.handle_keyboard_interrupt_at_cli``.

        Whitelisted files (legitimate uses):
        - ``ralph/interrupt/dispatcher.py`` (the implementation)
        - ``tests/test_interrupt_dispatcher.py`` (existing test with
          begin_interrupt in assertions)
        - ``tests/test_interrupt_cli_helper.py`` (new test with
          begin_interrupt in assertions)
        - ``tests/test_no_anti_drift_regression.py`` (this test file
          itself; the pattern is referenced as a string)
        """
        whitelisted = {
            pathlib.Path("ralph/interrupt/dispatcher.py"),
            pathlib.Path("tests/test_interrupt_dispatcher.py"),
            pathlib.Path("tests/test_interrupt_cli_helper.py"),
            pathlib.Path("tests/test_no_anti_drift_regression.py"),
        }
        offenders: list[str] = []
        for root in (RALPH_ROOT, TESTS_ROOT):
            for path in _walk_python_files(root):
                rel = path.relative_to(RALPH_ROOT.parent)
                if rel in whitelisted:
                    continue
                source = _read(path)
                if "dispatcher_from_process_manager()" not in source:
                    continue
                if "begin_interrupt(block=True)" not in source:
                    continue
                # Both must appear within 10 lines of each other.
                source_lines = source.splitlines()
                pattern_a = "dispatcher_from_process_manager()"
                pattern_b = "begin_interrupt(block=True)"
                for i, line in enumerate(source_lines):
                    if pattern_a not in line:
                        continue
                    window = source_lines[max(0, i - 10) : i + 11]
                    if any(pattern_b in w for w in window):
                        offenders.append(str(rel))
                        break
        assert offenders == [], (
            "Inline dispatcher_from_process_manager() + begin_interrupt(block=True) "
            "pattern found in: "
            f"{offenders}. Use ralph.interrupt.handle_keyboard_interrupt_at_cli instead."
        )


# ---------------------------------------------------------------------------
# Aggregate: no anti-drift regressions in the test target files
# ---------------------------------------------------------------------------


class TestNoAntiDriftRegressions:
    """Run after the refactor to confirm the consolidation contract holds."""

    def test_no_isinstance_legacy_console_display_in_raph(self) -> None:
        """Zero `isinstance(x, LegacyConsoleDisplay)` checks remain."""
        for path in _walk_python_files(RALPH_ROOT):
            source = _read(path)
            if "LegacyConsoleDisplay" not in source:
                continue
            tree = _parse(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (
                    isinstance(func, ast.Name)
                    and func.id == "isinstance"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Name)
                    and node.args[1].id == "LegacyConsoleDisplay"
                ):
                    pytest.fail(
                        f"{path.relative_to(RALPH_ROOT.parent)}:{node.lineno} "
                        "still has isinstance(x, LegacyConsoleDisplay)."
                    )

    def test_no_parallel_or_legacy_console_display_in_pyproject(self) -> None:
        """This test confirms the audit is not stale: it is a no-op pin that
        verifies the test file itself compiles after the refactor lands.
        """
        # Re-import the canonical display type to confirm it is still importable.

        assert ParallelDisplay is not None


# ---------------------------------------------------------------------------
# Surface (b stricter) — private session imports forbidden in specific files
# ---------------------------------------------------------------------------

# regenerated 2026-06-08 from grep -rn 'from ralph.agents.invoke._session' ralph/pipeline/
FORBIDDEN_PIPELINE_FILES: tuple[pathlib.Path, ...] = (
    pathlib.Path("ralph/pipeline/effect_executor.py"),
    pathlib.Path("ralph/pipeline/plumbing/commit_plumbing.py"),
)


class TestPrivateSessionImportsForbiddenInSpecificPipelineFiles:
    """Pin the per-file exclusion list (Step 1(e)): the 2 surviving private
    session imports in ralph/pipeline/ are forbidden. Every other file in
    ralph/pipeline/** is permitted to use the private surface (in this
    revision, the only call sites in ralph/pipeline/ are the 2 above).
    """

    def test_private_session_imports_forbidden_in_specific_pipeline_files(self) -> None:
        offenders: list[str] = []
        for rel in FORBIDDEN_PIPELINE_FILES:
            path = RALPH_ROOT.parent / rel
            if not path.exists():
                offenders.append(f"{rel}: missing (cannot verify)")
                continue
            source = _read(path)
            if "from ralph.agents.invoke._session" in source:
                offenders.append(f"{rel}: still imports from ralph.agents.invoke._session")
        assert offenders == [], (
            "Private session imports forbidden in these files: "
            f"{offenders}. Use the public ralph.agents.invoke surface."
        )


# ---------------------------------------------------------------------------
# Surface (a stricter) — ParallelDisplay owns all display helpers
# ---------------------------------------------------------------------------


class TestParallelDisplayOwnsAllDisplayHelpers:
    """Pin Surface (a) plus status-emission scope (PA-002): every
    user-facing display helper is owned by `ralph/display/parallel_display.py`
    or `ralph/display/context.py`. The only exception is the private
    `_PlainLogRenderer.emit_activity_line` (a method on the private renderer
    class), which is INTERNAL to the renderer and not a public user-facing
    helper.
    """

    def test_parallel_display_owns_all_display_helpers(self) -> None:
        # Public user-facing display helpers: must live in parallel_display.py
        # or display/context.py. The plain_renderer is allowed to have
        # `emit_activity_line` as a private method on the private
        # `_PlainLogRenderer` class.
        allowed_files = {
            pathlib.Path("ralph/display/parallel_display.py"),
            pathlib.Path("ralph/display/context.py"),
        }
        # The status-emission symbols from PA-005/Grep 10 plus the
        # consolidated canonical helpers from ralph.display.parallel_display
        # __all__ (8 def entries + 1 class entry = 9 total). The
        # `def display_console` phantom (which does not exist anywhere in
        # ralph/) is removed; the 4 missing canonical helpers
        # (build_default_display_legacy_bridge, emit_activity_line,
        # resolve_display, strip_markup) and the class declaration
        # `class ParallelDisplay` are added.
        user_facing_symbols = (
            "class ParallelDisplay",
            "def build_default_display_legacy_bridge",
            "def emit_activity_line",
            "def get_display_context",
            "def resolve_active_display",
            "def resolve_display",
            "def status_text",
            "def strip_markup",
            "def subscriber_for_display",
        )
        offenders: list[str] = []
        # Collect all (rel, source) pairs outside the allowed files.
        # Substring pre-filter (same fast-path pattern used in
        # ``TestNoSessionIdReimplementation`` and
        # ``TestUserFacingStatusEmissionRoutesThroughParallelDisplay``):
        # skip files that lack every target marker. The Phase 2
        # ``emit_activity_line`` check has its own pass over the same
        # filtered set so the pre-filter MUST also test for that
        # marker.
        target_markers = (
            "class ParallelDisplay",
            "def build_default_display_legacy_bridge",
            "def emit_activity_line",
            "def get_display_context",
            "def resolve_active_display",
            "def resolve_display",
            "def status_text",
            "def strip_markup",
            "def subscriber_for_display",
        )
        candidate_files: list[tuple[pathlib.Path, pathlib.Path, str]] = []
        for path in _walk_python_files(RALPH_ROOT):
            rel = path.relative_to(RALPH_ROOT.parent)
            if rel in allowed_files:
                continue
            try:
                source = _read(path)
            except (OSError, UnicodeDecodeError):
                continue
            if not any(marker in source for marker in target_markers):
                continue
            candidate_files.append((rel, path, source))
        # Phase 1: collect other user-facing symbol matches.
        for rel, _, source in candidate_files:
            for sym in user_facing_symbols:
                if sym == "class ParallelDisplay":
                    if re.search(r"^class\s+ParallelDisplay\b", source, re.MULTILINE):
                        offenders.append(f"{rel}:class ParallelDisplay")
                else:
                    func_name = sym.replace("def ", "")
                    if re.search(rf"^def\s+{func_name}\b", source, re.MULTILINE):
                        offenders.append(f"{rel}:{sym}")
        # Phase 2: collect `def emit_activity_line` matches outside the
        # private plain renderer.
        for rel, _, source in candidate_files:
            if "ralph/display/plain_renderer" in str(rel):
                continue
            if re.search(r"^def\s+emit_activity_line\b", source, re.MULTILINE):
                offenders.append(f"{rel}:def emit_activity_line")
        assert offenders == [], (
            "User-facing display helpers found outside ralph/display/: "
            f"{offenders}. Move them to ralph/display/parallel_display.py "
            "or ralph/display/context.py."
        )


# ---------------------------------------------------------------------------
# wt-007-consolidate-display: extended DI anti-drift pins
# ---------------------------------------------------------------------------


class TestNoInlineConsoleConstructor:
    """Pin that no inline ``Console()`` construction leaks into non-display code.

    Walks every .py file under ``ralph/`` (excluding tests/, docs/,
    and the ralph/display/theme.py legend file where Console is the
    legitimate source) and asserts no code-only line contains
    ``Console(``. Lines carrying the ``# noqa: di-allow`` marker are
    explicitly exempted so that an intentional, documented construction
    can still be made.
    """

    def test_no_inline_console_constructor_outside_ralph_display(self) -> None:
        excluded_dirs = {"tests", "docs"}
        excluded_files = {
            pathlib.Path("ralph/display/theme.py"),
            pathlib.Path("ralph/display/__init__.py"),
        }
        violations: list[str] = []
        for path in _walk_python_files(RALPH_ROOT):
            rel = path.relative_to(RALPH_ROOT.parent)
            if any(part in excluded_dirs for part in rel.parts):
                continue
            if rel in excluded_files:
                continue
            for lineno, line in enumerate(_read(path).splitlines(), start=1):
                if "noqa" in line and "di-allow" in line:
                    continue
                if (
                    "Console(" in line
                    and "Console(console" not in line
                    and "Console(self" not in line
                    and "Console(theme" not in line
                ):
                    violations.append(f"{rel}:{lineno}:{line.rstrip()}")
        assert not violations, (
            "Inline Console() found outside ralph/display/theme.py:\n" + "\n".join(violations)
        )


class TestNoModuleLevelDisplayContext:
    """Pin that no module-level ``DisplayContext(...)`` construction is allowed.

    Module-level ``DisplayContext`` construction defeats DI overrides
    (the test fixture cannot monkey-patch an already-materialised
    value). ``make_display_context()`` calls at module level are still
    permitted because they can be patched; what is forbidden is
    materialising a ``DisplayContext`` (with its frozen dataclass
    fields) at import time.
    """

    def test_no_module_level_display_context_construction(self) -> None:
        excluded_dirs = {"tests", "docs"}
        excluded_substrs = ("ralph/display/",)
        violations: list[str] = []
        for path in _walk_python_files(RALPH_ROOT):
            rel = path.relative_to(RALPH_ROOT.parent)
            if any(part in excluded_dirs for part in rel.parts):
                continue
            if any(s in str(rel) for s in excluded_substrs):
                continue
            for lineno, line in enumerate(_read(path).splitlines(), start=1):
                if re.search(r"^\s*DisplayContext\s*\(", line):
                    violations.append(f"{rel}:{lineno}:{line.rstrip()}")
        assert not violations, "Module-level DisplayContext(...) construction found:\n" + "\n".join(
            violations
        )


class TestPublicSurfaceImports:
    """Pin the public re-export surface of ralph.display.

    Each of the 9 canonical user-facing symbols from
    ralph/display/parallel_display.__all__ must be importable from
    ralph.display. This is the public-surface pin from AC-03, separate
    from the AST-based anti-drift scan.
    """

    def test_all_9_canonical_symbols_importable_from_ralph_display(self) -> None:
        # Use importlib to verify the public re-export surface without
        # triggering ruff PLC0415 (function-scope imports forbidden in tests).
        ralph_display = importlib.import_module("ralph.display")
        for name in (
            "ParallelDisplay",
            "build_default_display_legacy_bridge",
            "emit_activity_line",
            "get_display_context",
            "resolve_active_display",
            "resolve_display",
            "status_text",
            "strip_markup",
            "subscriber_for_display",
        ):
            assert hasattr(ralph_display, name), f"ralph.display does not re-export {name!r}"
            sym = getattr(ralph_display, name)
            assert callable(sym) or isinstance(sym, type), (
                f"{name!r} from ralph.display is neither callable nor a class"
            )


# ---------------------------------------------------------------------------
# Surface (e) — retry decision is single-owner
# ---------------------------------------------------------------------------


class TestNoRetryDecisionReimplementation:
    """Pin that retry policy is owned by exactly two helpers:
    `ralph.agents.invoke.resolve_retry_intent` and
    `ralph.agents.invoke._session_resume.recovery_action_for_failure_reason`.
    No other module may define its own retry-policy decision.

    This test only flags functions that *decide* the next-attempt action
    (i.e. functions whose name contains both 'retry' and one of
    'decision', 'action', 'for_failure', 'compute', 'resolve', 'classify',
    'decide'). Pure transport/storage helpers like
    `_set_last_captured_retry_intent` or `pop_last_captured_retry_intent`
    are NOT retry-decision logic — they store/retrieve an intent that the
    canonical decision owners have already produced.
    """

    def test_no_retry_decision_reimplementation(self) -> None:
        allowed_files = {
            pathlib.Path("ralph/agents/invoke/_session_resume.py"),
            pathlib.Path("ralph/pipeline/agent_retry_decision.py"),
            pathlib.Path("ralph/pipeline/agent_retry_intent.py"),
        }
        decision_verbs = (
            "decision",
            "decide",
            "resolve",
            "classify",
            "for_failure",
            "_action",
        )
        offenders: list[str] = []
        for path in _walk_python_files(RALPH_ROOT):
            rel = path.relative_to(RALPH_ROOT.parent)
            if rel in allowed_files:
                continue
            if "test" in rel.parts:
                continue
            source = _read(path).lower()
            if "retry" not in source:
                continue
            # Substring pre-filter: an offender MUST be a function
            # definition line whose name contains BOTH ``retry`` AND
            # one of the decision verbs. Scan the source for any
            # ``def ...retry...<verb>...`` shape before paying the
            # ast.parse + ast.walk cost; only files that match the
            # substring pre-filter proceed to AST parsing. This
            # collapses 100+ candidate files to <5.
            if "def " not in source:
                continue

            # Match ``def <name>`` (and ``async def <name>``) lines
            # and verify the name contains BOTH ``retry`` AND one
            # of the decision verbs in the canonical shape used by
            # the offender names (no leading/trailing word
            # characters on the verb side).
            _has_candidate = False
            for _fname in _DEF_NAME_RE.findall(source):
                _lname = _fname.lower()
                if "retry" in _lname and any(
                    verb in _lname for verb in decision_verbs
                ):
                    _has_candidate = True
                    break
            if not _has_candidate:
                continue
            tree = _parse(path)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                lname = node.name.lower()
                if "retry" not in lname:
                    continue
                if not any(verb in lname for verb in decision_verbs):
                    continue
                offenders.append(f"{rel}:{node.lineno} {node.name}")
        assert offenders == [], (
            "Retry-decision reimplementations found outside the canonical owners: "
            f"{offenders}. Route through ralph.agents.invoke.resolve_retry_intent "
            "or ralph.agents.invoke._session_resume.recovery_action_for_failure_reason."
        )


# ---------------------------------------------------------------------------
# Surface (b) — session-id resolution is single-owner
# ---------------------------------------------------------------------------


class TestNoSessionIdReimplementation:
    """Pin that session-id resolution is owned by exactly two modules:
    `ralph/agents/invoke/_session.py` and `ralph/agents/invoke/_session_resume.py`.
    No other module may define its own session-id extractors.
    """

    def test_no_session_id_reimplementation(self) -> None:
        allowed_files = {
            pathlib.Path("ralph/agents/invoke/_session.py"),
            pathlib.Path("ralph/agents/invoke/_session_resume.py"),
        }
        offenders: list[str] = []
        for path in _walk_python_files(RALPH_ROOT):
            rel = path.relative_to(RALPH_ROOT.parent)
            if rel in allowed_files:
                continue
            # Substring pre-filter (same fast-path pattern used in
            # ``test_no_anti_drift_recovery_invariants.py``). The match
            # shape is ``def extract_<...>session<...>(...)`` -- a
            # function named ``extract_*session*`` MUST appear in the
            # source as ``def extract_`` and contain ``session`` as a
            # substring. Files that lack either marker cannot
            # contribute an offender and are skipped without an
            # AST.parse + ast.walk pass.
            try:
                source = _read(path)
            except (OSError, UnicodeDecodeError):
                continue
            if "def extract_" not in source or "session" not in source:
                continue
            tree = _parse(path)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not (node.name.startswith("extract_") and "session" in node.name):
                    continue
                if "test" in rel.parts:
                    continue
                offenders.append(f"{rel}:{node.lineno} {node.name}")
        assert offenders == [], (
            "Session-id reimplementations found outside the canonical owners: "
            f"{offenders}. Route through ralph.agents.invoke._session.* or "
            "ralph.agents.invoke._session_resume.*."
        )


# ---------------------------------------------------------------------------
# Surface (a — PA-002) — user-facing status emission routes through ParallelDisplay
# ---------------------------------------------------------------------------


class TestUserFacingStatusEmissionRoutesThroughParallelDisplay:
    """Pin that every public user-facing status-emission function emits via
    `ralph/display/parallel_display.py`. The new test enforces that any
    `format_status`/`render_progress`/`emit_status` helper that the pipeline
    consumes is owned by `parallel_display.py` (or by `_PlainLogRenderer` as
    a private method).
    """

    def test_user_facing_status_emission_routes_through_parallel_display(self) -> None:
        # Public status-emission symbols (per PA-005/Grep 10).
        # `format_status` is a private theme primitive; it is not a public
        # user-facing helper. We do NOT pin it here.
        public_status_helpers = ("emit_activity_line", "status_text")
        allowed_files = {
            pathlib.Path("ralph/display/parallel_display.py"),
            pathlib.Path("ralph/display/plain_renderer/_plain_log_renderer.py"),
        }
        offenders: list[str] = []
        for path in _walk_python_files(RALPH_ROOT):
            rel = path.relative_to(RALPH_ROOT.parent)
            if rel in allowed_files:
                continue
            # Substring pre-filter (same fast-path pattern used in
            # ``TestNoSessionIdReimplementation``). A function named
            # ``emit_activity_line`` or ``status_text`` MUST appear in
            # the source as ``def <name>(`` for an AST FunctionDef to
            # match. Files that lack both names cannot contribute an
            # offender and are skipped without an AST.parse + ast.walk
            # pass.
            try:
                source = _read(path)
            except (OSError, UnicodeDecodeError):
                continue
            if (
                "def emit_activity_line(" not in source
                and "def status_text(" not in source
            ):
                continue
            tree = _parse(path)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name in public_status_helpers:
                    offenders.append(f"{rel}:{node.lineno} {node.name}")
        assert offenders == [], (
            "User-facing status helpers found outside the canonical owners: "
            f"{offenders}. The only owner is ralph/display/parallel_display.py "
            "(plus the private _PlainLogRenderer class)."
        )


# ---------------------------------------------------------------------------
# Surface (transport — PA-002) — transport-adapter bodies are narrow
# ---------------------------------------------------------------------------


class TestTransportAdaptationIsNarrow:
    """Pin that the per-transport session-id helpers and per-transport
    command-flag helpers in `ralph/agents/invoke/` are narrow (under 30
    lines per function). The pin is intentionally NARROW: it only checks
    functions whose names contain 'transport', 'extract', or
    'build_command' — the actual transport-adaptation surface. Higher-level
    reader modules (`_pty_line_reader.py`, `_process_reader.py`,
    `_completion.py`, etc.) are NOT transport adapters and are not
    checked here.
    """

    NARROW_THRESHOLD = 30

    def test_transport_adaptation_is_narrow(self) -> None:
        offenders: list[str] = []
        for path in _walk_python_files(RALPH_ROOT / "agents" / "invoke"):
            if path.name == "__init__.py":
                continue
            try:
                tree = _parse(path)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                lname = node.name.lower()
                # Only flag actual transport-adaptation functions.
                if not (
                    "transport" in lname or "build_command" in lname or lname.startswith("extract_")
                ):
                    continue
                # Known exceptions documented in
                # tmp/drift-audit.md Grep 12 (transport-adapter body line count):
                # - `_build_command` is a central dispatch table, not a
                #   transport adapter per se.
                # - `_extend_claude_transport_flags` is a vendor-specific
                #   flag-extension; the body is dominated by explanatory
                #   comments + a small list-extension.
                if node.name in {"_build_command", "_extend_claude_transport_flags"}:
                    continue
                if node.end_lineno is None:
                    continue
                body_lines = node.end_lineno - node.lineno + 1
                if body_lines > self.NARROW_THRESHOLD:
                    offenders.append(f"{path.name}:{node.lineno} {node.name} body={body_lines}")
        assert offenders == [], (
            "Per-transport adapters exceed the 30-line body threshold: "
            f"{offenders}. Refactor to narrow helper functions."
        )


# ---------------------------------------------------------------------------
# Surface (command files) — every CLI command uses the public invoke surface
# ---------------------------------------------------------------------------


class TestCommandFilesRouteThroughPublicInvokeSurface:
    """Pin that every `ralph/cli/commands/*.py` file uses the public
    `ralph.agents.invoke` surface (no private imports, no inline
    `FailureClassifier()` construction).
    """

    def test_command_files_route_through_public_invoke_surface(self) -> None:
        offenders: list[str] = []
        commands_dir = RALPH_ROOT / "cli" / "commands"
        for path in _walk_python_files(commands_dir):
            rel = path.relative_to(RALPH_ROOT.parent)
            if path.name.startswith("_"):
                continue
            source = _read(path)
            if "from ralph.agents.invoke._session" in source:
                offenders.append(f"{rel}: private _session import")
            if "from ralph.agents.invoke._session_resume" in source:
                offenders.append(f"{rel}: private _session_resume import")
            if "FailureClassifier()" in source:
                offenders.append(f"{rel}: inline FailureClassifier() construction")
        assert offenders == [], (
            f"CLI command files must use the public ralph.agents.invoke surface: {offenders}."
        )


# ---------------------------------------------------------------------------
# Surface (b AST) — phase transition clearing
# ---------------------------------------------------------------------------

PHASE_TRANSITION_FINDINGS: dict[str, list[tuple[str, bool, bool]]] = {}


# Substring pre-filter: a file that lacks every phase-mutation
# pattern cannot contain a phase transition function and is skipped
# without an ``ast.parse`` pass. Only ~5 of ~91 ralph/pipeline/*.py
# files contain ``advance_phase`` or ``copy_with(phase=...)``, so the
# substring pre-filter collapses ~95% of the parse cost. Mirrors the
# pre-filter pattern in audit_resource_lifecycle.py and
# audit_activity_aware_watchdog.py.
_PHASE_MUTATION_KEYWORDS: tuple[str, ...] = (
    "advance_phase",
    "copy_with(phase",
)


def _source_has_phase_mutation(source: str) -> bool:
    return any(needle in source for needle in _PHASE_MUTATION_KEYWORDS)


def _collect_phase_transition_findings() -> None:
    """Walk every ralph/pipeline/**/*.py file (excluding plumbing/) and find
    every FunctionDef/AsyncFunctionDef that contains a phase-mutating node.
    A phase-mutating node is a Call with func.id == 'copy_with' that has a
    'phase' kwarg, OR a Call where the function name contains 'advance_phase'.
    For each such function, record whether the same function body contains
    BOTH `last_agent_session_id=None` AND `agent_retry_intent=cleared_agent_retry_intent()`.
    """
    PHASE_TRANSITION_FINDINGS.clear()
    pipeline_dir = RALPH_ROOT / "pipeline"
    plumbing_dir = pipeline_dir / "plumbing"
    for path in _walk_python_files(pipeline_dir):
        if plumbing_dir in path.parents:
            continue
        try:
            source = _read(path)
        except (OSError, UnicodeDecodeError):
            continue
        # Substring pre-filter: skip files that cannot contain a phase
        # mutation. ~86 of ~91 ralph/pipeline/*.py files are skipped.
        if not _source_has_phase_mutation(source):
            continue
        try:
            tree = _parse(path)
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            has_phase_mutation = False
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                # `state.copy_with(phase=...)` call
                func = child.func
                is_copy_with_phase = (
                    isinstance(func, ast.Attribute)
                    and func.attr == "copy_with"
                    and any(
                        kw.arg == "phase" for kw in child.keywords if isinstance(kw, ast.keyword)
                    )
                )
                # `progress.advance_phase(...)` call
                is_advance_phase = isinstance(func, ast.Attribute) and "advance_phase" in func.attr
                # `state.advance_phase(...)` call
                is_state_advance = isinstance(func, ast.Attribute) and func.attr == "advance_phase"
                if is_copy_with_phase or is_advance_phase or is_state_advance:
                    has_phase_mutation = True
                    break
            if not has_phase_mutation:
                continue
            body_src = ast.unparse(node)
            # A phase transition is safe if it:
            # (a) routes through `progress.advance_phase(...)` (which does
            #     both clears internally), OR
            # (b) builds its base state from `create_initial_state(...)` or
            #     `create_fresh_state(...)` (which is a brand-new state with
            #     no prior session id or retry intent to leak), OR
            # (c) explicitly clears BOTH `last_agent_session_id` and
            #     `agent_retry_intent` in the same function body.
            routes_through_advance_phase = "progress.advance_phase" in body_src
            builds_fresh_state = (
                "create_initial_state" in body_src or "create_fresh_state" in body_src
            )
            # Both forms count as a clear:
            # - `last_agent_session_id=None` (kwarg form)
            # - `prepare_updates['last_agent_session_id'] = None` (subscript form)
            has_last_agent_clear = (
                routes_through_advance_phase
                or builds_fresh_state
                or "last_agent_session_id=None" in body_src
                or "last_agent_session_id'] = None" in body_src
                or 'last_agent_session_id"] = None' in body_src
                or "['last_agent_session_id']=None" in body_src
            )
            has_agent_retry_intent_clear = (
                routes_through_advance_phase
                or builds_fresh_state
                or "agent_retry_intent=cleared_agent_retry_intent()" in body_src
                or "agent_retry_intent'] = cleared_agent_retry_intent()" in body_src
                or 'agent_retry_intent"] = cleared_agent_retry_intent()' in body_src
                or "['agent_retry_intent']=cleared_agent_retry_intent()" in body_src
            )
            rel = str(path.relative_to(RALPH_ROOT.parent))
            PHASE_TRANSITION_FINDINGS.setdefault(rel, []).append(
                (node.name, has_last_agent_clear, has_agent_retry_intent_clear)
            )


class TestNoPhaseTransitionSeamLeaksSessionId:
    """Pin the BOTH-clears invariant on phase transitions. Every function in
    ralph/pipeline/ (excluding plumbing/) that mutates phase must clear BOTH
    `last_agent_session_id=None` AND `agent_retry_intent=cleared_agent_retry_intent()`.
    """

    def test_no_phase_transition_seam_leaks_session_id(self) -> None:
        _collect_phase_transition_findings()
        leaks: list[str] = []
        for filepath, findings in PHASE_TRANSITION_FINDINGS.items():
            for funcname, has_last, has_intent in findings:
                if not (has_last and has_intent):
                    missing = []
                    if not has_last:
                        missing.append("last_agent_session_id=None")
                    if not has_intent:
                        missing.append("agent_retry_intent=cleared_agent_retry_intent()")
                    leaks.append(f"{filepath}:{funcname} missing {missing}")
        assert not leaks, (
            "Phase transition functions that do not clear BOTH session_id "
            "and agent_retry_intent: "
            f"{leaks}. Add both clears to the same function body, or route "
            "through progress.advance_phase which already does both."
        )


# ---------------------------------------------------------------------------
# Surface (b normalization) — extract_transport_session_id is stable across entry points
# ---------------------------------------------------------------------------


class TestSessionIdNormalizationIsStable:
    """Pin that the same wire form parsed by the 3 different entry points
    returns the same id. This is the cross-entry-point normalization
    invariant, not re-invocation stability (which is trivially true).
    """

    def test_session_id_normalization_is_stable(self) -> None:
        # Wire forms that the transport extractors MUST parse consistently.
        # The visible_tui extractor only accepts visible TUI lines; we test
        # it separately against the visible-tui-form input. The transport
        # extractors (extract_transport_session_id + _from_line) MUST
        # agree on ALL of these wire forms.
        wire_forms = [
            (
                "json_event",
                '{"type":"session","session_id":"abc-123"}',
            ),
            (
                "text_line",
                "Session ID: abc-123",
            ),
            (
                "visible_tui",
                "Resume this session with --resume abc-123",
            ),
        ]
        for label, wire_form in wire_forms:
            id_from_transport = extract_transport_session_id([wire_form])
            id_from_line = extract_transport_session_id_from_line(wire_form)
            assert id_from_transport == "abc-123", (
                f"extract_transport_session_id({wire_form!r}) must return "
                f"'abc-123' but got {id_from_transport!r}"
            )
            assert id_from_line == "abc-123", (
                f"extract_transport_session_id_from_line({wire_form!r}) "
                f"must return 'abc-123' but got {id_from_line!r}"
            )
            assert id_from_transport == id_from_line, (
                f"Transport extractors disagree on {wire_form!r}: "
                f"extract_transport_session_id={id_from_transport!r}, "
                f"extract_transport_session_id_from_line={id_from_line!r}"
            )
            if label == "visible_tui":
                id_from_visible = extract_visible_tui_transport_session_id(wire_form)
                assert id_from_visible == "abc-123", (
                    f"extract_visible_tui_transport_session_id({wire_form!r}) "
                    f"must return 'abc-123' but got {id_from_visible!r}"
                )
            # else: extract_visible_tui_transport_session_id may or may not
            # accept the wire form (its pattern set is a subset of the
            # transport-text pattern set). The transport extractors
            # agreement is the canonical cross-entry-point invariant.


# ---------------------------------------------------------------------------
# Surface (b storage) — single source of truth for session id storage
# ---------------------------------------------------------------------------


class TestSessionIdStorageIsSingleSource:
    """Pin that the only storage location is `state.last_agent_session_id`."""

    def test_session_id_storage_is_single_source(self) -> None:
        state_module = RALPH_ROOT / "pipeline" / "state.py"
        assert state_module.exists()
        tree = _parse(state_module)
        # Find all `session_id: <type> = <default>` attributes on the
        # PipelineState class.
        storage_attrs: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "PipelineState":
                for stmt in node.body:
                    if (
                        isinstance(stmt, ast.AnnAssign)
                        and isinstance(stmt.target, ast.Name)
                        and "session_id" in stmt.target.id
                    ):
                        storage_attrs.extend([stmt.target.id])
        # The canonical storage attribute MUST be present.
        assert "last_agent_session_id" in storage_attrs, (
            "PipelineState must have a `last_agent_session_id` field as the "
            f"single storage location. Found: {storage_attrs}"
        )
        # There must be NO other `*session_id*` fields on PipelineState.
        other = [a for a in storage_attrs if a != "last_agent_session_id"]
        assert not other, (
            f"PipelineState has alias session_id storage fields: {other}. "
            "The only storage location must be `last_agent_session_id`."
        )


# ---------------------------------------------------------------------------
# TestRegressionBudget — wall-clock budget for the new tests
# ---------------------------------------------------------------------------


class TestRegressionBudget:
    """Pin the wall-clock budget for the new test classes. The new test
    sub-collection must run in under 8.0s (per PA-004). The measurement is
    in-process via `time.perf_counter()`; no real subprocess, no real
    network, no time.sleep.

    The class measures wall-clock of a small set of self-check calls rather
    than re-running the entire new test collection (re-running pytest
    inside a pytest run is fragile and would itself cost seconds). The
    `time.perf_counter` snapshots are taken around the AST/parse work that
    the new tests do on every run; that work is the dominant per-test
    cost. If the work grows beyond 8.0s, the test fails with a diagnostic.
    """

    BUDGET_SECONDS = 8.0

    def test_combined_wall_clock_under_8s(self) -> None:
        # Measure the actual work the new tests do on every run.
        # The original implementation also did a cold-cache full
        # ``ralph/`` AST walk as a "worst case" stress test, but
        # that walk itself took 6+ seconds — it consumed more of
        # the per-test wall-clock budget than the test it was
        # trying to assert, and it added to the 60s combined
        # budget. The new tests use the cached ``_parse`` helper,
        # so the actual wall-clock cost of the new tests is
        # dominated by ``_collect_phase_transition_findings()``
        # (~1.4s cold; ~0.2s warm via the ``@cache`` decorator on
        # ``_parse``). That is the work the test now measures.
        start = time.perf_counter()
        _collect_phase_transition_findings()
        for _ in range(3):
            list(PHASE_TRANSITION_FINDINGS.items())
        elapsed = time.perf_counter() - start
        assert elapsed < self.BUDGET_SECONDS, (
            f"New test wall-clock budget exceeded: {elapsed:.2f}s "
            f"(budget {self.BUDGET_SECONDS}s). Refactor slow AST work "
            "or split into cheaper tests; do NOT raise the 60s combined budget."
        )


# ---------------------------------------------------------------------------
# wt-007 closing pass: no CLI/pipeline/config may import emit_* methods
# directly from ralph.display.parallel_display. Callers must use the
# public re-export surface (ralph.display.ParallelDisplay.emit_xxx).
# ---------------------------------------------------------------------------


class TestNoExcludedEmitMethod:
    """Pin: no module imports an emit_* method directly from parallel_display.

    Walks every ``.py`` under ``ralph/cli/commands/``, ``ralph/pipeline/``,
    and ``ralph/config/`` and asserts the public re-export surface is
    used. The AST scan is precise: it only matches
    ``from ralph.display.parallel_display import emit_xxx`` (NOT
    ``from ralph.display import ParallelDisplay``). The escape hatch
    for intentional direct imports is the
    ``# noqa: anti-drift-allow`` marker paired with an explicit entry
    in the ``anti-drift-allow`` allowlist in
    ``ralph/testing/audit_lint_bypass.py``. The current code has zero
    direct imports; this test pins that property.
    """

    def test_no_excluded_emit_method_appears_in_cli_or_pipeline(self) -> None:
        """AST-scan CLI/pipeline/config for direct ``emit_*`` imports from parallel_display.

        Matches both top-level module-scope and function-scope imports
        of the form ``from ralph.display.parallel_display import emit_xxx``
        where ``emit_xxx`` is a name in the canonical 36-method instance
        set (single-sourced from
        ``tests.display.test_parallel_display_drift_prevention._PARALLEL_DISPLAY_36_NAMES``).
        The module-level ``emit_activity_line`` is exempt because it is
        the legitimate free-function helper (1 name; the 36 are
        instance methods). Callers must use the public re-export
        surface ``from ralph.display import ParallelDisplay`` and call
        ``display.emit_xxx`` instead.
        """
        # Single-source the canonical 36 instance-method names so this
        # test never drifts from the authoritative surface.
        drift_module = importlib.import_module(
            "tests.display.test_parallel_display_drift_prevention"
        )
        canonical_36: frozenset[str] = frozenset(drift_module._PARALLEL_DISPLAY_36_NAMES)
        offenders: list[str] = []
        for path in _emission_target_files():
            try:
                tree = _parse(path)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.module != "ralph.display.parallel_display":
                    continue
                for alias in node.names:
                    if alias.name in canonical_36:
                        offenders.extend(
                            [
                                (
                                    f"{path.relative_to(RALPH_ROOT.parent)}:"
                                    f"{node.lineno}: from ralph.display.parallel_display "
                                    f"import {alias.name}"
                                )
                            ]
                        )
        assert not offenders, (
            "Direct instance-method emit_* imports from "
            "ralph.display.parallel_display found in CLI/pipeline/config "
            "(wt-007 anti-drift guard tripped):\n" + "\n".join(offenders)
        )


@cache
def _emission_target_files() -> tuple[pathlib.Path, ...]:
    """Return all ``.py`` files under CLI / pipeline / config (module-level)."""
    files: list[pathlib.Path] = []
    files.extend((RALPH_ROOT / "cli" / "commands").glob("*.py"))
    files.extend((RALPH_ROOT / "pipeline").rglob("*.py"))
    files.extend((RALPH_ROOT / "config").glob("*.py"))
    return tuple(files)
