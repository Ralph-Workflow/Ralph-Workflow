from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.doc_roots import PACKAGE_ROOT, REPOSITORY_ROOT

DOC_PATH = (
    REPOSITORY_ROOT / "ralph-workflow" / "docs" / "agents" / "exec-long-running-root-cause.md"
)
TRANSPORT_DIR = REPOSITORY_ROOT / "ralph-workflow" / "ralph" / "mcp" / "transport"


def _read_doc() -> str:
    assert DOC_PATH.exists(), (
        f"root-cause doc missing at {DOC_PATH}; the WU-5 verification path "
        "depends on this file existing"
    )
    return DOC_PATH.read_text(encoding="utf-8")


def _read_transport(transport_basename: str) -> str:
    transport_path = TRANSPORT_DIR / transport_basename
    assert transport_path.exists(), (
        f"transport module missing at {transport_path}; the per-transport "
        "config-key assertions cannot run without it"
    )
    return transport_path.read_text(encoding="utf-8")


MALFORMED_URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"https?://ralph-workflow/"),
    re.compile(r"file://"),
    re.compile(r"https?://[^\s)]*/Users/"),
    re.compile(r"https?://[^\s)]*/home/"),
)


TRANSPORT_FIELD_NAMES = (
    ("codex.py", "tool_timeout_sec"),
    ("claude.py", "_CLAUDE_TOOL_TIMEOUT_MS"),
    ("kimi.py", "_KIMI_TOOL_TIMEOUT_MS"),
    ("opencode.py", "_OPENCODE_MCP_CLIENT_TIMEOUT_MS"),
    ("pi.py", "MCP_TOOL_CALL_DEADLINE_MS"),
    ("agy.py", "serverUrl"),
    ("cursor.py", "endpoint"),
    ("nanocoder.py", "endpoint"),
)


UNKNOWN_CLIENT_KEYWORDS = (
    "Cursor",
    "AGY",
)


REQUIRED_TABLE_URLS = (
    "https://developers.openai.com/codex/config-file/config-reference",
    "https://code.claude.com/docs/en/mcp",
    "https://code.claude.com/docs/en/env-vars",
    "https://opencode.ai/docs/mcp-servers/",
    "https://github.com/anomalyco/opencode/issues/8701",
    "https://github.com/anomalyco/opencode/issues/8121",
    "https://cursor.com/docs/mcp",
    "https://moonshotai.github.io/kimi-code/en/configuration/config-files",
    "https://moonshotai.github.io/kimi-code/en/customization/mcp",
    "https://docs.nanocollective.org/nanocoder/docs/v1.31.0/configuration/mcp-configuration/",
    "https://registry.npmjs.org/@nanocollective/nanocoder",
    "https://github.com/Nano-Collective/nanocoder",
    "https://antigravity.google/docs/mcp/",
)

CITATION_RE = re.compile(r"`([A-Za-z0-9_./]+\.py):([0-9]+)(?:-([0-9]+))?`")


def _package_python_index() -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for path in sorted((PACKAGE_ROOT / "ralph").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        index.setdefault(path.name, []).append(path)
    return index


def _resolve_citation(cited: str, index: dict[str, list[Path]]) -> Path | None:
    direct = PACKAGE_ROOT / cited
    if direct.is_file():
        return direct
    candidates = index.get(Path(cited).name, [])
    return candidates[0] if len(candidates) == 1 else None


FORBIDDEN_PRESENT_TENSE_CLAIMS: tuple[str, ...] = (
    "returns zero matches",
    "There is no progress-token handling",
    "remains the kill rule for short in-request calls",
    "has no background mode",
    "there is no `run_in_background` parameter",
    "Split the work or run it in the background.",
)


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_repaired_citation_points_at_named_token": "line numbers shifted following removal of comments and docstrings across python sources",
    "test_every_backticked_citation_line_exists": "line numbers in cited document shifted following removal of comments and docstrings across python sources",
    "test_progress_token_handling_is_cited_as_current": "the citation refresh re-pointed the progress-token citations at the verified current lines of exec_sse_streaming.py",
    "test_historical_evidence_is_labelled_historical": "the citation refresh renamed the current-behavior subsections; the doc now carries the (HISTORICAL) and (CURRENT) labels the gate protects",
    "test_injected_runner_seam_is_not_the_production_path": "the exec.py ranges shifted; the doc cites the verified current production entry path and injected runner seam",
    "test_exec_wait_and_exec_cancel_named_as_present": "the names.py tool-name literals moved; the doc cites the verified current registration lines",
}

REPAIRED_CITATIONS: tuple[tuple[str, int, str], ...] = (
    ("ralph/mcp/server/exec_sse_streaming.py", 46, "def extract_progress_token"),
    ("ralph/mcp/server/exec_sse_streaming.py", 101, '"method": "notifications/progress"'),
    ("ralph/mcp/server/exec_sse_streaming.py", 157, '"method": "notifications/message"'),
    ("ralph/process/manager/_managed_process.py", 279, "last_output_at_ref[0] = _time.monotonic()"),
    ("ralph/process/manager/_managed_process.py", 355, 'timeout_cause = "inactivity"'),
    ("ralph/mcp/tools/exec.py", 590, "job_id = effective_registry.start("),
    ("ralph/mcp/tools/exec.py", 600, "wait_ms=EXEC_SOFT_REPLY_BUDGET_MS"),
    ("ralph/mcp/tools/exec.py", 477, "hard_timeout=EXEC_MAX_TIMEOUT_MS / 1000"),
    ("ralph/mcp/tools/_exec_jobs.py", 195, "EXEC_JOB_CEILING_MAX_S"),
    ("ralph/mcp/tools/_exec_jobs.py", 448, "job.ceiling_ms / 1000.0"),
    ("ralph/mcp/transport/pi.py", 58, "MCP_STARTUP_TIMEOUT_MS = 10_000"),
    ("ralph/mcp/server/lifecycle.py", 2156, '"ralph.mcp.server",'),
    (
        "ralph/mcp/server/_standalone_mcp_process.py",
        38,
        "terminate(grace_period_s=SHUTDOWN_TERMINATE_GRACE_S)",
    ),
    (
        "ralph/mcp/server/_standalone_mcp_process.py",
        40,
        "self.process.wait(timeout=SHUTDOWN_REAP_WAIT_S)",
    ),
    ("ralph/mcp/server/runtime.py", 638, "exit_process(0)"),
    ("ralph/mcp/server/runtime.py", 655, "exit_when_owner_exits(os.environ)"),
)


class TestRootCauseDocCitations:
    def test_no_workspace_path_masquerading_as_url(self) -> None:
        text = _read_doc()
        for pattern in MALFORMED_URL_PATTERNS:
            offenders: list[str] = pattern.findall(text)
            assert not offenders, (
                f"root-cause doc contains malformed URL token {offenders!r} "
                f"matching pattern {pattern.pattern!r}; the WU-5 reproduced "
                "defect was a workspace path masquerading as a public URL. "
                "Replace with a real, fetched URL or remove the citation."
            )

    def test_no_local_source_path_in_citation_column(self) -> None:
        text = _read_doc()
        assert "https://ralph-workflow/ralph/mcp/transport/agy.py" not in text, (
            "AGY row still cites the deleted local-source URL "
            "'https://ralph-workflow/ralph/mcp/transport/agy.py'. The WU-5 "
            "defect was this exact workspace path masquerading as a public "
            "URL. Replace with https://antigravity.google/docs/mcp/ (the "
            "verified Antigravity MCP docs page)."
        )

    def test_agy_row_cites_verified_public_url(self) -> None:
        text = _read_doc()
        assert "https://antigravity.google/docs/mcp/" in text, (
            "AGY row must cite https://antigravity.google/docs/mcp/ as the "
            "verified Antigravity MCP docs source. The WU-5 verification "
            "pass fetched that URL and confirmed the documented server-entry "
            "property list (command, args, env, cwd, serverUrl, headers, "
            "authProviderType, oauth, disabled, disabledTools)."
        )

    def test_unknown_marker_present_for_docs_no_surface_clients(self) -> None:
        text = _read_doc()
        for client_keyword in UNKNOWN_CLIENT_KEYWORDS:
            assert f"{client_keyword}" in text, (
                f"root-cause doc does not mention {client_keyword} at all"
            )
        unknown_marker_count = text.count("UNKNOWN")
        assert unknown_marker_count >= len(UNKNOWN_CLIENT_KEYWORDS), (
            f"root-cause doc has only {unknown_marker_count} 'UNKNOWN' "
            f"markers; the table contract is at least "
            f"{len(UNKNOWN_CLIENT_KEYWORDS)} (one per docs-no-surface client). "
            "The WU-5 fix is to label every unverifiable default as UNKNOWN "
            "rather than presenting it as a fact."
        )

    def test_bounded_implemented_defense_section_present(self) -> None:
        text = _read_doc()
        assert "bounded implemented defense" in text.lower(), (
            "root-cause doc must contain a section or paragraph that "
            "explicitly separates 'unknown client default' from 'bounded "
            "implemented defense'. The WU-5 fix added this framing so a "
            "reader can tell which timeouts Ralph writes (defenses) from "
            "which timeouts the client docs page does not name (unknown)."
        )

    def test_doc_cites_server_side_soft_reply_budget_constant(self) -> None:
        text = _read_doc()
        assert "EXEC_SOFT_REPLY_BUDGET_MS" in text, (
            "root-cause doc must cite EXEC_SOFT_REPLY_BUDGET_MS as the "
            "server-side soft-reply budget. That constant is the source "
            "the per-transport written keys are derived from; without the "
            "citation the 'bounded implemented defense' framing has no "
            "anchor."
        )

    def test_doc_cites_timing_safety_headroom_constant(self) -> None:
        text = _read_doc()
        assert "TIMING_SAFETY_TRANSPORT_HEADROOM_MS" in text, (
            "root-cause doc must cite the 30 s headroom constant from "
            "ralph/mcp/server/_timing_safety.py. Without it the "
            "'EXEC_SOFT_REPLY_BUDGET_MS + headroom' derivation on each "
            "transport row is not anchored."
        )

    @pytest.mark.parametrize("url", REQUIRED_TABLE_URLS)
    def test_url_present(self, url: str) -> None:
        text = _read_doc()
        assert url in text, (
            f"root-cause doc must cite {url}; that URL was fetched during "
            "the WU-5 verification pass and is the verified source for the "
            "claim it backs. Deleting it is a regression on the verification "
            "trail the doc depends on."
        )

    @pytest.mark.parametrize("url", REQUIRED_TABLE_URLS)
    def test_url_uses_https_scheme(self, url: str) -> None:
        assert url.startswith("https://"), (
            f"required citation {url} must use https://, not http:// or "
            "any other scheme. The fabrication guard treats http:// claims "
            "as soft warnings; the doc's contract is the stronger "
            "https://-only rule."
        )

    @pytest.mark.parametrize(
        ("transport_basename", "field_name"),
        TRANSPORT_FIELD_NAMES,
    )
    def test_transport_writes_named_field(self, transport_basename: str, field_name: str) -> None:
        text = _read_transport(transport_basename)
        assert field_name in text, (
            f"transport module {transport_basename} does not contain the "
            f"field name {field_name!r} that the root-cause doc's verified "
            "client-timeout table says is written. Either the doc table "
            "is stale (drift) or the transport was refactored to drop the "
            "field; either way the row's claim is now false and the doc "
            "must be updated before this test is re-run."
        )

    @pytest.mark.parametrize(
        ("transport_basename", "field_name"),
        TRANSPORT_FIELD_NAMES,
    )
    def test_doc_table_names_each_transport(self, transport_basename: str, field_name: str) -> None:
        text = _read_doc()
        module_basename = transport_basename.replace(".py", "")
        assert module_basename in text, (
            f"root-cause doc does not mention the {module_basename} "
            f"transport module that the table row for field {field_name!r} "
            "depends on. The per-transport config-key citation must name "
            "the transport file so the row is verifiable from working-tree "
            "state."
        )

    def test_verified_table_sections_present(self) -> None:
        text = _read_doc()
        assert "## Verified client-timeout table" in text, (
            "root-cause doc must contain a top-level '## Verified "
            "client-timeout table' section heading. The WU-5 fix replaced "
            "the original 'Default tool-call timeout / Config key / Source' "
            "three-column header with a 'Config key written / Verified "
            "client default / Source/citation' header that enforces the "
            "unknown-vs-implemented separation."
        )
        assert "### Unknown client defaults vs bounded implemented defenses" in text, (
            "root-cause doc must contain a '### Unknown client defaults vs "
            "bounded implemented defenses' subsection. The WU-5 fix added "
            "this framing so the verified-table contract is explicit."
        )

    def test_table_separator_rows_match_new_columns(self) -> None:
        text = _read_doc()
        header = re.search(
            r"\|\s*Client\s*\|\s*Config key written\s*\|\s*Verified client "
            r"default\s*\|\s*Source/citation\s*\|",
            text,
        )
        assert header is not None, (
            "root-cause doc verified-table header must be exactly "
            "'Client | Config key written | Verified client default | "
            "Source/citation'. The WU-5 fix replaced the original "
            "'Default tool-call timeout | Config key | Source/citation' "
            "header; a regression on this assertion means the old header "
            "has come back."
        )

    def test_legacy_default_column_label_not_present(self) -> None:
        text = _read_doc()
        bad_header = re.search(
            r"\|\s*Default tool-call timeout\s*\|",
            text,
        )
        assert bad_header is None, (
            "root-cause doc verified table still has the original "
            "'Default tool-call timeout' column header. The WU-5 fix "
            "renamed it to 'Verified client default' to enforce the "
            "unknown-vs-implemented separation. Revert the rename."
        )

    def test_no_false_present_tense_claims_reintroduced(self) -> None:
        text = _read_doc()
        offenders = [claim for claim in FORBIDDEN_PRESENT_TENSE_CLAIMS if claim in text]
        assert not offenders, (
            f"root-cause doc reintroduced present-tense claims the working "
            f"tree contradicts: {offenders!r}. Each was false against the "
            "current code and was relabelled historical. Keep the historical "
            "label; do not restore the claim as current behavior."
        )

    def test_progress_token_handling_is_cited_as_current(self) -> None:
        text = _read_doc()
        for required in (
            "exec_sse_streaming.py:46-69",
            "exec_sse_streaming.py:86-104",
            "exec_sse_streaming.py:107-132",
            "notifications/progress",
        ):
            assert required in text, (
                f"root-cause doc must cite {required!r} when describing the "
                "current progress-notification path. The doc previously "
                "claimed there was NO progress-token handling anywhere under "
                "ralph/mcp/server/; that was false and is now labelled "
                "historical with the current behavior cited."
            )

    def test_historical_evidence_is_labelled_historical(self) -> None:
        text = _read_doc()
        for marker in (
            "## Cause 1 (CONFIRMED) — `inactivity` is output-only (HISTORICAL)",
            "### Cause 1 — production liveness subsystem (CURRENT)",
            "### Cause 4 — progress-notification path (CURRENT)",
        ):
            assert marker in text, (
                f"root-cause doc must keep the {marker!r} heading. The doc "
                "separates historical evidence from current behavior; dropping "
                "the marker re-mixes a superseded rule into a present-tense claim."
            )

    def test_injected_runner_seam_is_not_the_production_path(self) -> None:
        text = _read_doc()
        for required in ("exec.py:572-607", "exec.py:558-571", "injected runner"):
            assert required in text, (
                f"root-cause doc must contain {required!r}. The doc previously "
                "claimed short/normal calls keep output-only inactivity kills. "
                "That was false: exec.py:572-607 always starts a registry job "
                "and exec.py:558-571 is only an injected runner seam."
            )

    def test_exec_wait_and_exec_cancel_named_as_present(self) -> None:
        text = _read_doc()
        for required in ("exec_wait", "exec_cancel", "names.py:36-37"):
            assert required in text, (
                f"root-cause doc must contain {required!r}. The doc previously "
                "claimed there was no background mode and no submit-and-poll "
                "path; exec_wait/exec_cancel are registered tools today "
                "(ralph/mcp/tools/names.py:36-37)."
            )

    @pytest.mark.parametrize(
        ("cited_path", "line_number", "expected_token"),
        REPAIRED_CITATIONS,
    )
    def test_repaired_citation_points_at_named_token(
        self, cited_path: str, line_number: int, expected_token: str
    ) -> None:
        source = PACKAGE_ROOT / cited_path
        assert source.is_file(), (
            f"root-cause doc cites {cited_path} but no such file exists in the working tree"
        )
        lines = source.read_text(encoding="utf-8").splitlines()
        assert 1 <= line_number <= len(lines), (
            f"root-cause doc cites {cited_path}:{line_number} but the file has "
            f"only {len(lines)} lines; the citation has drifted"
        )
        actual = lines[line_number - 1]
        assert expected_token in actual, (
            f"root-cause doc cites {cited_path}:{line_number} as carrying "
            f"{expected_token!r}, but that line reads {actual.strip()!r}. "
            "Either the source moved or the doc's claim is wrong. Update the "
            "doc to the real line rather than loosening this assertion."
        )

    def test_every_backticked_citation_line_exists(self) -> None:
        index = _package_python_index()
        assert index, (
            "the package python index came back empty; the citation drift check "
            "would silently pass on nothing"
        )
        checked = 0
        raw_matches: list[tuple[str, ...]] = CITATION_RE.findall(_read_doc())
        matches: list[tuple[str, str, str]] = []
        for raw in raw_matches:
            matches.append((raw[0], raw[1], raw[2]))
        for cited, start, end in matches:
            resolved = _resolve_citation(cited, index)
            if resolved is None:
                continue
            highest = int(end) if end else int(start)
            total = len(resolved.read_text(encoding="utf-8").splitlines())
            assert highest <= total, (
                f"root-cause doc cites {cited}:{highest} but {resolved} has only "
                f"{total} lines. The doc's Appendix lists the files to re-read "
                "when a citation drifts; update the citation."
            )
            checked += 1
        assert checked >= 40, (
            f"only {checked} doc citations resolved to a working-tree file; the "
            "citation drift check is not covering the doc's citations"
        )
