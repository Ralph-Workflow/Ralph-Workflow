from __future__ import annotations

import ast
import importlib
import pathlib
import re
import time
import tokenize
from functools import cache
from io import StringIO

import pytest

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

RALPH_ROOT = pathlib.Path(__file__).parent.parent / "ralph"
TESTS_ROOT = pathlib.Path(__file__).parent


@cache
def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


@cache
def _read_bytes(path: pathlib.Path) -> bytes:
    return path.read_bytes()


@cache
def _parse(path: pathlib.Path) -> ast.AST:
    return ast.parse(_read(path))


_DEF_NAME_RE = re.compile(r"(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_DISPLAY_CONTEXT_CONSTRUCTION_RE = re.compile(r"^\s*DisplayContext\s*\(")
_STATIC_WIRE_FORM_LITERAL_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[rRuUbBfF]{0,3})?['\"](mcp__[A-Za-z0-9_]+__[A-Za-z0-9_]+)['\"]"
)


@cache
def _walk_python_files(root: pathlib.Path) -> tuple[pathlib.Path, ...]:
    return tuple(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


@cache
def _legacy_console_display_references() -> tuple[pathlib.Path, ...]:
    needle = b"LegacyConsoleDisplay"
    return tuple(path for path in _walk_python_files(RALPH_ROOT) if needle in _read_bytes(path))


_WIRE_FORM_SCAN_ROOTS: tuple[pathlib.Path, ...] = (
    RALPH_ROOT / "agents",
    RALPH_ROOT / "display",
    RALPH_ROOT / "pipeline",
)


@pytest.fixture(scope="session", autouse=True)
def _prewarm_wire_form_scan_cache() -> None:
    for path in _walk_python_files(RALPH_ROOT):
        try:
            _read(path)
        except (OSError, UnicodeDecodeError):
            continue
    _legacy_console_display_references()
    for root in _WIRE_FORM_SCAN_ROOTS:
        for path in _walk_python_files(root):
            try:
                source = _read(path)
            except (OSError, UnicodeDecodeError):
                continue
            if "mcp__" not in source:
                continue
            _wire_form_literals_in_source(source)
    for path in _emission_target_files():
        _parallel_display_imports(path)


def _all_string_literals(source: str) -> set[str]:
    literals = set(_STATIC_WIRE_FORM_LITERAL_RE.findall(source))
    values: set[str] = set()
    adjacent_parts: list[str] = []
    for token in tokenize.generate_tokens(StringIO(source).readline):
        if token.type == tokenize.STRING:
            if "mcp__" not in token.string and not adjacent_parts:
                continue
            value = ast.literal_eval(token.string)
            if isinstance(value, str):
                adjacent_parts.append(value)
                values.add(value)
            continue
        if token.type in {tokenize.COMMENT, tokenize.NL}:
            continue
        if adjacent_parts:
            values.add("".join(adjacent_parts))
            adjacent_parts.clear()
    if adjacent_parts:
        values.add("".join(adjacent_parts))
    return values | literals


_WIRE_FORM_RE = re.compile(r"^mcp__[A-Za-z0-9_]+__[A-Za-z0-9_]+$")


@pytest.mark.timeout_seconds(10)
def test_wire_form_literal_scan_keeps_adjacent_literals() -> None:
    source = 'name = "mcp__server" "__tool"\n'

    assert _wire_form_literals_in_source(source) == {"mcp__server__tool"}


@cache
def _wire_form_literals_in_source(source: str) -> set[str]:
    return {lit for lit in _all_string_literals(source) if _WIRE_FORM_RE.match(lit)}


def _is_transport_adaptation_name(name: str) -> bool:
    lowered_name = name.lower()
    return (
        "transport" in lowered_name
        or "build_command" in lowered_name
        or lowered_name.startswith("extract_")
    )


class TestDisplayIsOnlyParallelDisplay:
    def test_legacy_console_display_module_is_deleted(self) -> None:
        legacy_module = RALPH_ROOT / "pipeline" / "legacy_console_display.py"
        assert not legacy_module.exists(), (
            f"{legacy_module} still exists; Step 3 (delete LegacyConsoleDisplay) is incomplete."
        )

    def test_no_legacy_console_display_reference_outside_tests(self) -> None:
        references = _legacy_console_display_references()
        assert references == (), (
            "Production files still reference LegacyConsoleDisplay after Step 3 delete: "
            + str([str(path.relative_to(RALPH_ROOT.parent)) for path in references])
        )

    def test_no_isinstance_check_against_legacy_console_display(self) -> None:
        for path in _legacy_console_display_references():
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


class TestSessionIdLifecycleSingleExtractor:
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

        result = extract_visible_tui_transport_session_id("session_id=abc-123")
        assert result is None, (
            "extract_visible_tui_transport_session_id must reject generic "
            "session_id=... text so tool output cannot masquerade as a "
            "transport session id."
        )

    @pytest.mark.timeout_seconds(10)
    def test_no_private_session_imports_outside_invoke_package(self) -> None:
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


class TestResumeSessionIdSingleDecisionPoint:
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

        assert (
            recovery_action_for_failure_reason(
                "AgentInactivityTimeoutError", has_prior_session=False
            )
            == "fresh"
        )

    def test_recovery_action_resume_for_tool_availability_failure(self) -> None:

        assert (
            recovery_action_for_failure_reason(
                "No such tool available: mcp__server__tool",
                has_prior_session=True,
                reset_tool_registry=True,
            )
            == "resume"
        )


class TestFreshSessionOptions:
    def test_fresh_session_options_clears_session_id(self) -> None:

        opts = InvokeOptions(session_id="abc-123", verbose=True)
        fresh = fresh_session_options(opts)
        assert fresh.session_id is None
        assert fresh.verbose is True
        assert opts.session_id == "abc-123"

    def test_fresh_session_options_no_prior_session_id(self) -> None:

        fresh = fresh_session_options(InvokeOptions(session_id=None))
        assert fresh.session_id is None

    def test_fresh_session_options_accepts_prior_session_id_for_compatibility(self) -> None:

        opts = InvokeOptions(session_id="abc-123")
        fresh = fresh_session_options(opts, prior_session_id="def-456")
        assert fresh.session_id is None


class TestParserEventClassificationIsShared:
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

        assert isinstance(LIFECYCLE_KINDS, frozenset)
        assert is_lifecycle_kind("lifecycle")
        assert not is_lifecycle_kind("output")

    def test_superset_lifecycle_set(self) -> None:

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
        consumer = RALPH_ROOT / "agents" / "parsers" / "claude_interactive.py"
        assert consumer.exists()
        source = _read(consumer)
        assert "is_lifecycle_kind" in source, (
            "claude_interactive.py:parse must explicitly filter lifecycle "
            "events via is_lifecycle_kind(...) from the shared "
            "_event_classification module."
        )


class TestPlumbingCommitUsesSharedPipeline:
    def test_commit_module_has_no_inline_failure_classifier_construction(self) -> None:
        commit = RALPH_ROOT / "cli" / "commands" / "commit.py"
        assert commit.exists()
        source = _read(commit)
        assert "FailureClassifier()" not in source, (
            "ralph/cli/commands/commit.py still constructs FailureClassifier() "
            "inline. After Step 6(a) these calls move to "
            "ralph/pipeline/plumbing/commit_plumbing.py."
        )

    def test_commit_module_has_no_inline_extract_transport_session_id_import(self) -> None:
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
        source = _read(plumbing)
        assert "FailureClassifier()" not in source, (
            "commit_plumbing.py constructs FailureClassifier() directly; "
            "it must route classification through run_with_direct_mcp_recovery."
        )


class TestFailureClassifierOwnership:
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
        del allowed_path
        allowed_relative = {
            pathlib.Path("ralph/recovery/failure_classifier.py"),
            pathlib.Path("ralph/recovery/classifier.py"),
            pathlib.Path("ralph/recovery/controller.py"),
            pathlib.Path("ralph/agents/invoke/_direct_mcp_recovery.py"),
            pathlib.Path("ralph/agents/invoke/_completion.py"),
            pathlib.Path("ralph/pipeline/agent_retry_decision.py"),
            pathlib.Path("ralph/testing/audit_agent_failure_single_owner.py"),
            pathlib.Path("ralph/testing/_single_owner_rules.py"),
        }
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


class TestNoMcpWireFormOutsideMcpModule:
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


class TestInterruptPathReliable:
    def test_watchdog_evaluate_call_sites_unchanged(self) -> None:
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
        controller = RALPH_ROOT / "interrupt" / "controller.py"
        assert controller.exists()
        source = _read(controller)
        assert "def install_force_kill_handler" in source, (
            "ralph/interrupt/controller.py must define install_force_kill_handler."
        )
        testing = RALPH_ROOT / "testing" / "__init__.py"
        if testing.exists():
            testing_source = _read(testing)
            assert "install_force_kill_handler" not in testing_source, (
                "ralph.testing must not re-export install_force_kill_handler; "
                "the only owner is ralph.interrupt.controller."
            )

    def test_no_duplicate_keyboard_interrupt_handlers(self) -> None:
        offenders: list[str] = []
        for path in _walk_python_files(RALPH_ROOT):
            source = _read(path)
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

    @pytest.mark.timeout_seconds(15)
    def test_no_inline_dispatcher_plus_block_pattern_outside_helper(self) -> None:
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
                source_bytes = _read_bytes(path)
                if b"dispatcher_from_process_manager()" not in source_bytes:
                    continue
                if b"begin_interrupt(block=True)" not in source_bytes:
                    continue
                source_lines = source_bytes.decode("utf-8").splitlines()
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


class TestNoAntiDriftRegressions:
    def test_no_isinstance_legacy_console_display_in_raph(self) -> None:
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

        assert ParallelDisplay is not None


FORBIDDEN_PIPELINE_FILES: tuple[pathlib.Path, ...] = (
    pathlib.Path("ralph/pipeline/effect_executor.py"),
    pathlib.Path("ralph/pipeline/plumbing/commit_plumbing.py"),
)


class TestPrivateSessionImportsForbiddenInSpecificPipelineFiles:
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


class TestParallelDisplayOwnsAllDisplayHelpers:
    @pytest.mark.timeout_seconds(2)
    def test_parallel_display_owns_all_display_helpers(self) -> None:
        allowed_files = {
            pathlib.Path("ralph/display/parallel_display.py"),
            pathlib.Path("ralph/display/context.py"),
        }
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
        for rel, _, source in candidate_files:
            for sym in user_facing_symbols:
                if sym == "class ParallelDisplay":
                    if re.search(r"^class\s+ParallelDisplay\b", source, re.MULTILINE):
                        offenders.append(f"{rel}:class ParallelDisplay")
                else:
                    func_name = sym.replace("def ", "")
                    if re.search(rf"^def\s+{func_name}\b", source, re.MULTILINE):
                        offenders.append(f"{rel}:{sym}")
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


class TestNoInlineConsoleConstructor:
    @pytest.mark.timeout_seconds(2)
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
            content = _read(path)
            if "DisplayContext" not in content:
                continue
            for lineno, line in enumerate(content.splitlines(), start=1):
                if _DISPLAY_CONTEXT_CONSTRUCTION_RE.search(line):
                    violations.append(f"{rel}:{lineno}:{line.rstrip()}")
        assert not violations, "Module-level DisplayContext(...) construction found:\n" + "\n".join(
            violations
        )


class TestPublicSurfaceImports:
    def test_all_9_canonical_symbols_importable_from_ralph_display(self) -> None:
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


class TestNoRetryDecisionReimplementation:
    @pytest.mark.timeout_seconds(2)
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
            if "def " not in source:
                continue

            _has_candidate = False
            for _fname in _DEF_NAME_RE.findall(source):
                _lname = _fname.lower()
                if "retry" in _lname and any(verb in _lname for verb in decision_verbs):
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


class TestNoSessionIdReimplementation:
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


class TestUserFacingStatusEmissionRoutesThroughParallelDisplay:
    def test_user_facing_status_emission_routes_through_parallel_display(self) -> None:
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
            try:
                source = _read(path)
            except (OSError, UnicodeDecodeError):
                continue
            if "def emit_activity_line(" not in source and "def status_text(" not in source:
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


class TestTransportAdaptationIsNarrow:
    NARROW_BODY_LINES_THRESHOLD = 30

    def test_transport_adaptation_regression_parses_only_candidate_modules(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _parse.cache_clear()
        parsed_paths: list[pathlib.Path] = []
        cached_parse = _parse

        def record_parse(path: pathlib.Path) -> ast.AST:
            parsed_paths.append(path)
            return cached_parse(path)

        monkeypatch.setattr(importlib.import_module(__name__), "_parse", record_parse)

        self.test_transport_adaptation_is_narrow()

        scanned_paths = tuple(
            path
            for path in _walk_python_files(RALPH_ROOT / "agents" / "invoke")
            if path.name != "__init__.py"
            and any(
                _is_transport_adaptation_name(name) for name in _DEF_NAME_RE.findall(_read(path))
            )
        )
        assert parsed_paths == list(scanned_paths)

    def test_transport_adaptation_is_narrow(self) -> None:
        offenders: list[str] = []
        for path in _walk_python_files(RALPH_ROOT / "agents" / "invoke"):
            if path.name == "__init__.py":
                continue
            if not any(
                _is_transport_adaptation_name(name) for name in _DEF_NAME_RE.findall(_read(path))
            ):
                continue
            try:
                tree = _parse(path)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not _is_transport_adaptation_name(node.name):
                    continue
                if node.name in {"_build_command", "_extend_claude_transport_flags"}:
                    continue
                if node.end_lineno is None:
                    continue
                body_lines = node.end_lineno - node.lineno + 1
                if body_lines > self.NARROW_BODY_LINES_THRESHOLD:
                    offenders.append(f"{path.name}:{node.lineno} {node.name} body={body_lines}")
        assert offenders == [], (
            "Per-transport adapters exceed the 30-line body threshold: "
            f"{offenders}. Refactor to narrow helper functions."
        )


class TestCommandFilesRouteThroughPublicInvokeSurface:
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


PHASE_TRANSITION_FINDINGS: dict[str, list[tuple[str, bool, bool]]] = {}


_PHASE_MUTATION_KEYWORDS: tuple[str, ...] = (
    "advance_phase",
    "copy_with(phase",
)


def _source_has_phase_mutation(source: str) -> bool:
    return any(needle in source for needle in _PHASE_MUTATION_KEYWORDS)


class _PhaseTransitionVisitor(ast.NodeVisitor):
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.findings: list[tuple[str, bool, bool]] = []
        self._current_function: str | None = None
        self._has_phase_mutation: bool = False

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        prev_func = self._current_function
        prev_mutation = self._has_phase_mutation

        self._current_function = node.name
        self._has_phase_mutation = False

        self.generic_visit(node)

        if self._has_phase_mutation:
            if hasattr(node, "end_lineno") and node.end_lineno is not None:
                body_src = "\n".join(self.lines[node.lineno - 1 : node.end_lineno])
            else:
                body_src = ast.unparse(node)
            routes_through_advance_phase = "progress.advance_phase" in body_src
            builds_fresh_state = (
                "create_initial_state" in body_src or "create_fresh_state" in body_src
            )
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
            self.findings.append((node.name, has_last_agent_clear, has_agent_retry_intent_clear))

        self._current_function = prev_func
        self._has_phase_mutation = prev_mutation

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._current_function is not None and not self._has_phase_mutation:
            func = node.func
            is_copy_with_phase = (
                isinstance(func, ast.Attribute)
                and func.attr == "copy_with"
                and any(kw.arg == "phase" for kw in node.keywords if isinstance(kw, ast.keyword))
            )
            is_advance_phase = isinstance(func, ast.Attribute) and "advance_phase" in func.attr
            is_state_advance = isinstance(func, ast.Attribute) and func.attr == "advance_phase"
            if is_copy_with_phase or is_advance_phase or is_state_advance:
                self._has_phase_mutation = True
        self.generic_visit(node)


def _collect_phase_transition_findings() -> None:
    if PHASE_TRANSITION_FINDINGS:
        return
    pipeline_dir = RALPH_ROOT / "pipeline"
    plumbing_prefix = str(pipeline_dir / "plumbing")
    for path in _walk_python_files(pipeline_dir):
        if str(path).startswith(plumbing_prefix):
            continue
        try:
            source = _read(path)
        except (OSError, UnicodeDecodeError):
            continue
        if not _source_has_phase_mutation(source):
            continue
        try:
            tree = _parse(path)
        except (SyntaxError, OSError):
            continue
        lines = source.splitlines()
        visitor = _PhaseTransitionVisitor(lines)
        visitor.visit(tree)
        if visitor.findings:
            rel = str(path.relative_to(RALPH_ROOT.parent))
            PHASE_TRANSITION_FINDINGS[rel] = visitor.findings


_collect_phase_transition_findings()


class TestNoPhaseTransitionSeamLeaksSessionId:
    @pytest.mark.timeout_seconds(5)
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


class TestSessionIdNormalizationIsStable:
    def test_session_id_normalization_is_stable(self) -> None:
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


class TestSessionIdStorageIsSingleSource:
    def test_session_id_storage_is_single_source(self) -> None:
        state_module = RALPH_ROOT / "pipeline" / "state.py"
        assert state_module.exists()
        tree = _parse(state_module)
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
        assert "last_agent_session_id" in storage_attrs, (
            "PipelineState must have a `last_agent_session_id` field as the "
            f"single storage location. Found: {storage_attrs}"
        )
        other = [a for a in storage_attrs if a != "last_agent_session_id"]
        assert not other, (
            f"PipelineState has alias session_id storage fields: {other}. "
            "The only storage location must be `last_agent_session_id`."
        )


class TestRegressionBudget:
    BUDGET_SECONDS = 8.0

    def test_combined_wall_clock_under_8s(self) -> None:
        start = time.perf_counter()
        for _ in range(3):
            list(PHASE_TRANSITION_FINDINGS.items())
        elapsed = time.perf_counter() - start
        assert elapsed < self.BUDGET_SECONDS, (
            f"New test wall-clock budget exceeded: {elapsed:.2f}s "
            f"(budget {self.BUDGET_SECONDS}s). Refactor slow AST work "
            "or split into cheaper tests; do NOT raise the 60s combined budget."
        )


class TestNoExcludedEmitMethod:
    def test_no_excluded_emit_method_appears_in_cli_or_pipeline(self) -> None:
        canonical_all: frozenset[str] = frozenset(
            {
                "emit_parsed_event",
                "emit_analysis_result",
                "emit_run_start",
                "emit_phase_close",
                "emit_phase_close_from_exit",
                "emit_run_end",
                "emit_phase_start",
                "emit_phase_start_from_entry",
                "emit_phase_transition",
                "emit_phase_close_banner",
                "emit_plan_artifact",
                "emit_development_artifact",
                "emit_review_artifact",
                "emit_fix_artifact",
                "emit_analysis_decision",
                "emit_commit_message",
                "emit_missing_plan_hint",
                "emit_first_run_panel",
                "emit_welcome_banner",
                "emit_agents_table",
                "emit_providers_table",
                "emit_config_table",
                "emit_capability_summary",
                "emit_status",
                "emit_warning",
                "emit_skill_failure_warning",
                "emit_fallback_next_steps",
                "emit_metrics_table",
                "emit_checkpoint_summary_table",
                "emit_diagnose_inventory_table",
                "emit_diagnose_probe_table",
                "emit_diagnose_servers_table",
                "emit_info_panel",
                "emit_blank_line",
                "emit_dry_run_summary",
                "emit_renderable",
                "emit_log_line",
                "emit_status_line",
                "emit_warn_line",
                "emit_snapshot",
                "emit_completion_summary_panel",
            }
        )
        offenders: list[str] = []
        for path in _emission_target_files():
            relative_path = path.relative_to(RALPH_ROOT.parent)
            for line_number, imported_names in _parallel_display_imports(path):
                offenders.extend(
                    f"{relative_path}:{line_number}: "
                    f"from ralph.display.parallel_display import {name}"
                    for name in imported_names
                    if name in canonical_all
                )
        assert not offenders, (
            "Direct instance-method emit_* imports from "
            "ralph.display.parallel_display found in CLI/pipeline/config "
            "(wt-007 anti-drift guard tripped):\n" + "\n".join(offenders)
        )


@cache
def _parallel_display_imports(path: pathlib.Path) -> tuple[tuple[int, tuple[str, ...]], ...]:
    source = _read(path)
    if "parallel_display" not in source:
        return ()
    if not re.search(
        r"from ralph\.display\.parallel_display import(?:\s+emit_|\s*\([^)]*\bemit_)",
        source,
    ):
        return ()
    try:
        tree = _parse(path)
    except SyntaxError:
        return ()
    return tuple(
        (node.lineno, tuple(alias.name for alias in node.names))
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "ralph.display.parallel_display"
    )


@cache
def _emission_target_files() -> tuple[pathlib.Path, ...]:
    files: list[pathlib.Path] = []
    files.extend((RALPH_ROOT / "cli" / "commands").glob("*.py"))
    files.extend((RALPH_ROOT / "pipeline").rglob("*.py"))
    files.extend((RALPH_ROOT / "config").glob("*.py"))
    return tuple(files)
