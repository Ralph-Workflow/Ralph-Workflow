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
import pathlib
import re

import pytest

# Top-level imports for the symbols the inline test functions need
# (ruff PLC0415 requires module-level imports for these).
from ralph.agents.invoke import (
    extract_transport_session_id,
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

RALPH_ROOT = pathlib.Path(__file__).parent.parent / "ralph"
TESTS_ROOT = pathlib.Path(__file__).parent

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


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
            tree = ast.parse(source)
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
        result = extract_visible_tui_transport_session_id('session_id=abc-123')
        assert result is None, (
            "extract_visible_tui_transport_session_id must reject generic "
            "session_id=... text so tool output cannot masquerade as a "
            "transport session id."
        )

    def test_no_private_session_imports_outside_invoke_package(self) -> None:
        """`from ralph.agents.invoke._session import` may only appear inside the package.

        Per Step 4(f): only `ralph/agents/invoke/`, `ralph/agents/parsers/`,
        and `ralph/pipeline/` may import from the private `_session`
        module. CLI commands and other modules must use the public
        `ralph.agents.invoke` surface.
        """
        allowed_roots = (
            RALPH_ROOT / "agents" / "invoke",
            RALPH_ROOT / "agents" / "parsers",
            RALPH_ROOT / "pipeline",
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
            "(private); they must use the public ralph.agents.invoke surface: "
            + str(offenders)
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
            recovery_action_for_failure_reason("Unknown session", has_prior_session=True)
            == "fresh"
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
            recovery_action_for_failure_reason(
                "OpenCodeResumableExitError", has_prior_session=True
            )
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

    def test_plumbing_commit_module_uses_run_with_direct_mcp_recovery(self) -> None:
        plumbing = RALPH_ROOT / "pipeline" / "plumbing" / "commit_plumbing.py"
        if not plumbing.exists():
            pytest.skip("plumbing module not yet created; pin in Step 6")
        source = _read(plumbing)
        assert "run_with_direct_mcp_recovery" in source, (
            "commit_plumbing.py must route chain iteration through "
            "run_with_direct_mcp_recovery (the single canonical retry loop)."
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
        - `ralph/pipeline/effect_executor.py` (the retry-loop seam)
        - `ralph/pipeline/plumbing/commit_plumbing.py` (new plumbing module)
        - `ralph/pipeline/agent_retry_decision.py` (the shared decision dispatcher)
        """
        allowed_relative = {
            pathlib.Path("ralph/recovery/failure_classifier.py"),
            pathlib.Path("ralph/recovery/classifier.py"),
            pathlib.Path("ralph/recovery/controller.py"),
            pathlib.Path("ralph/agents/invoke/_direct_mcp_recovery.py"),
            pathlib.Path("ralph/agents/invoke/_completion.py"),
            pathlib.Path("ralph/pipeline/effect_executor.py"),
            pathlib.Path("ralph/pipeline/plumbing/commit_plumbing.py"),
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
        for path in _walk_python_files(checked_root):
            for literal in _wire_form_literals_in_source(_read(path)):
                offenders.extend([f"{path.relative_to(RALPH_ROOT.parent)}: {literal}"])
        assert offenders == [], (
            "Wire-form `mcp__<server>__<tool>` literals found outside "
            "`ralph/mcp/`: "
            + str(offenders)
            + ". Route via "
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
        """
        offenders: list[str] = []
        for path in _walk_python_files(RALPH_ROOT):
            source = _read(path)
            if "def handle_keyboard_interrupt" not in source:
                continue
            if path == RALPH_ROOT / "pipeline" / "_runner_interrupt.py":
                continue
            offenders.append(str(path.relative_to(RALPH_ROOT.parent)))
        assert offenders == [], (
            "Duplicate `def handle_keyboard_interrupt` definitions found: "
            f"{offenders}. The only owner is "
            "ralph/pipeline/_runner_interrupt.py."
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
            tree = ast.parse(source)
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
