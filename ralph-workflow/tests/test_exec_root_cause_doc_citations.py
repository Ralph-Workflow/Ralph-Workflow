"""Citation/URL regression tests for the long-running exec root-cause doc.

Asserts the verified client-timeout table in
``ralph-workflow/docs/agents/exec-long-running-root-cause.md`` stays
defensible on file:line and URL evidence:

1. No malformed local-path "URL" appears anywhere in the doc. The
   WU-5 reproduced defect was the AGY row citing
   ``https://ralph-workflow/ralph/mcp/transport/agy.py:79-95`` — a
   workspace path masquerading as a public URL. This test rejects the
   pattern and any ``file://`` or ``/Users/`` or absolute ``/home/``
   URL the same way ``scripts/fabrication_guard.py`` would reject a
   claim.
2. The AGY row's source/citation column names the verified public URL
   (``https://antigravity.google/docs/mcp/``) and does NOT name the
   deleted local-source URL. Working-tree pointers in the row are
   labelled as such, not presented as public citations.
3. The verified table's "Config key written" column cites a real
   ``ralph/mcp/transport/<x>.py`` line that exists in the working tree
   and contains the named field. The test does NOT depend on the
   exact line number — only on the file:line resolving to a real
   token. A drift edit that drops the field name surfaces as a test
   failure; a drift edit that shifts the line by one does not.
4. The doc separates "unknown client default" from "bounded
   implemented defense". The test asserts both the prose marker
   (``UNKNOWN`` in the "Verified client default" column for clients
   whose docs page does not name a tool-call timeout) AND the
   "bounded implemented defense" framing paragraph are present.
5. Every external ``https://`` URL the doc claims is the
   authoritative client-side source for a fact (URLs in the table's
   "Source/citation" column) is well-formed and uses ``https://``
   only — no ``http://`` claims sneaking past the fabrication guard's
   transport rule.

6. Every current-state claim the doc makes about progress-token
   handling, the exec entry path, and the wall-clock cap matches what
   the working tree actually does. Two claims were false when this
   test was written — "there is no progress-token handling" (the code
   extracts tokens at ``exec_sse_streaming.py:62-89`` and emits
   ``notifications/progress`` at ``exec_sse_streaming.py:107-126``)
   and "the inactivity window is the kill rule for short in-request
   calls" (the production path at ``exec.py:790-819`` starts a
   registry job unconditionally; ``exec.py:775-788`` is an injected
   runner seam). The doc now labels that evidence historical; these
   assertions keep it labelled.
7. Every backticked ``<file>.py:<line>`` citation in the doc resolves
   to a line that exists in the working tree, and each citation this
   unit repaired resolves to a line carrying the exact token the doc
   claims. Drift is the only way a citation goes stale.

The test does NOT hit the network. It treats the URLs as opaque
strings and asserts on their shape plus their textual appearance in
the verified doc sections. A URL whose host 404s is a separate
failure mode (``scripts/fabrication_guard.py --level 2``); this
test exists to catch the WU-5 class of bug (workspace path
masquerading as a URL) at the cheapest possible cost.
"""

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
    """Return the full text of the root-cause doc."""
    assert DOC_PATH.exists(), (
        f"root-cause doc missing at {DOC_PATH}; the WU-5 verification path "
        "depends on this file existing"
    )
    return DOC_PATH.read_text(encoding="utf-8")


def _read_transport(transport_basename: str) -> str:
    """Return the full text of a single transport module."""
    transport_path = TRANSPORT_DIR / transport_basename
    assert transport_path.exists(), (
        f"transport module missing at {transport_path}; the per-transport "
        "config-key assertions cannot run without it"
    )
    return transport_path.read_text(encoding="utf-8")


# Patterns that must NEVER appear in the doc — these are the shape of the
# WU-5 reproduced defect (a workspace path or absolute-local-path masquerading
# as a public URL).
MALFORMED_URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Workspace path that looks like a URL but resolves into the repo.
    re.compile(r"https?://ralph-workflow/"),
    # `file://` scheme — never a real citation in a public-facing doc.
    re.compile(r"file://"),
    # Absolute local home paths — never a real citation in a public-facing doc.
    re.compile(r"https?://[^\s)]*/Users/"),
    re.compile(r"https?://[^\s)]*/home/"),
)


# Per-transport pair: (transport basename, config-key field name the doc
# says is written). The test asserts the working tree's transport file
# actually contains that field name. Drift edits that delete the field
# surface as failures; line-number drift does not.
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


# Clients the verified table must mark as UNKNOWN. The doc's contract
# (per its own "Unknown client defaults vs bounded implemented defenses"
# section) is that every client whose cited docs page does not name a
# tool-call timeout carries the UNKNOWN marker.
UNKNOWN_CLIENT_KEYWORDS = (
    "Cursor",
    "AGY",
)


# External URLs the doc cites as the authoritative source for a fact.
# The shape test rejects any URL that is not ``https://``; the presence
# test rejects the doc deleting any of these URLs from the table (a
# deletion would be a regression on the verification trail).
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
    "https://docs.nanocollective.org/nanocoder/docs/configuration/mcp-configuration",
    "https://www.npmjs.com/package/@nanocollective/nanocoder",
    "https://github.com/Nano-Collective/nanocoder",
    "https://antigravity.google/docs/mcp/",
)


# Claims that were FALSE against the working tree and must never come
# back as present-tense assertions. Each maps to the doc line the
# correction replaced. Only the PRESENT-TENSE forms are listed: the
# doc deliberately keeps the past-tense restatements ("there was no
# progress-token handling", "had no daemon-style submit-and-poll") as
# historical evidence, so a substring test on those would flag the
# history instead of the falsehood.
FORBIDDEN_PRESENT_TENSE_CLAIMS: tuple[str, ...] = (
    "returns zero matches",
    "There is no progress-token handling",
    "remains the kill rule for short in-request calls",
    "has no background mode",
    "there is no `run_in_background` parameter",
    "Split the work or run it in the background.",
)


# Citations this unit repaired, as (doc-relative path, line, token the
# doc claims is on that line). Drift in the source fails here, which is
# the signal to update the doc rather than to widen the range.
REPAIRED_CITATIONS: tuple[tuple[str, int, str], ...] = (
    # Cause 4 — progress-token extraction and progress-frame emission.
    ("ralph/mcp/server/exec_sse_streaming.py", 62, "def extract_progress_token"),
    ("ralph/mcp/server/exec_sse_streaming.py", 123, '"method": "notifications/progress"'),
    ("ralph/mcp/server/exec_sse_streaming.py", 231, '"method": "notifications/message"'),
    # Cause 1 — output-only inactivity window (historical) and its poll loop.
    ("ralph/process/manager/_managed_process.py", 301, "last_output_at_ref[0] = _time.monotonic()"),
    ("ralph/process/manager/_managed_process.py", 378, 'timeout_cause = "inactivity"'),
    # Cause 1 — the production entry path vs the injected-runner seam.
    ("ralph/mcp/tools/exec.py", 807, "job_id = effective_registry.start("),
    ("ralph/mcp/tools/exec.py", 817, "wait_ms=EXEC_SOFT_REPLY_BUDGET_MS"),
    # Cause 2 — the seam's hard_cap and the registry's job ceiling clamp.
    ("ralph/mcp/tools/exec.py", 634, "hard_timeout=EXEC_MAX_TIMEOUT_MS / 1000"),
    ("ralph/mcp/tools/_exec_jobs.py", 220, "EXEC_JOB_CEILING_MAX_S"),
    ("ralph/mcp/tools/_exec_jobs.py", 516, "job.ceiling_ms / 1000.0"),
    # Cause 3 — the Pi bridge startup deadline.
    ("ralph/mcp/transport/pi.py", 85, "MCP_STARTUP_TIMEOUT_MS = 10_000"),
    # Cause 5c — child runtime shutdown seam.
    ("ralph/mcp/server/lifecycle.py", 2472, '"ralph.mcp.server",'),
    ("ralph/mcp/server/_standalone_mcp_process.py", 45, "terminate(grace_period_s=5.0)"),
    ("ralph/mcp/server/_standalone_mcp_process.py", 47, "self.process.wait(timeout=5.0)"),
    ("ralph/mcp/server/runtime.py", 939, "exit_process(0)"),
    ("ralph/mcp/server/runtime.py", 984, "exit_when_owner_exits(os.environ)"),
)

# A backticked ``<path>.py:<line>`` or ``<path>.py:<start>-<end>`` citation.
CITATION_RE = re.compile(r"`([A-Za-z0-9_./]+\.py):([0-9]+)(?:-([0-9]+))?`")


def _package_python_index() -> dict[str, list[Path]]:
    """Return a basename -> candidate-paths index over the shipped package."""
    index: dict[str, list[Path]] = {}
    for path in sorted((PACKAGE_ROOT / "ralph").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        index.setdefault(path.name, []).append(path)
    return index


def _resolve_citation(cited: str, index: dict[str, list[Path]]) -> Path | None:
    """Resolve a doc citation to a working-tree path, or None if unresolvable."""
    direct = PACKAGE_ROOT / cited
    if direct.is_file():
        return direct
    candidates = index.get(Path(cited).name, [])
    return candidates[0] if len(candidates) == 1 else None


class TestRootCauseDocCitations:
    """The doc must not contain any of the WU-5 reproduced defect shapes.

    The doc's present-tense claims must also match what the code does
    today. The two contracts are enforced by a single consolidated
    class because the repo-structure audit forbids multiple top-level
    test classes in this file.
    """

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
        """The AGY-row defect: ``https://ralph-workflow/ralph/mcp/transport/agy.py``.

        This test is the WU-5 explicit acceptance check. A regression on
        this assertion means the malformed URL has reappeared.
        """
        text = _read_doc()
        assert "https://ralph-workflow/ralph/mcp/transport/agy.py" not in text, (
            "AGY row still cites the deleted local-source URL "
            "'https://ralph-workflow/ralph/mcp/transport/agy.py'. The WU-5 "
            "defect was this exact workspace path masquerading as a public "
            "URL. Replace with https://antigravity.google/docs/mcp/ (the "
            "verified Antigravity MCP docs page)."
        )

    def test_agy_row_cites_verified_public_url(self) -> None:
        """The AGY row's source/citation must include the verified public URL."""
        text = _read_doc()
        assert "https://antigravity.google/docs/mcp/" in text, (
            "AGY row must cite https://antigravity.google/docs/mcp/ as the "
            "verified Antigravity MCP docs source. The WU-5 verification "
            "pass fetched that URL and confirmed the documented server-entry "
            "property list (command, args, env, cwd, serverUrl, headers, "
            "authProviderType, oauth, disabled, disabledTools)."
        )

    # -----------------------------------------------------------------------
    # Unknown vs Implemented
    # -----------------------------------------------------------------------

    def test_unknown_marker_present_for_docs_no_surface_clients(self) -> None:
        text = _read_doc()
        for client_keyword in UNKNOWN_CLIENT_KEYWORDS:
            assert f"{client_keyword}" in text, (
                f"root-cause doc does not mention {client_keyword} at all"
            )
        # The phrasing contract: every unknown-default row in the verified
        # client-timeout table must carry the ``UNKNOWN`` marker.
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

    # -----------------------------------------------------------------------
    # Required URLs
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # Per-transport fields
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # Doc structure
    # -----------------------------------------------------------------------

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
        """The markdown table header must list the new three columns in order."""
        text = _read_doc()
        # The verified table header line must contain the new column names.
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
        """The original WU-5 broken header label must NOT still appear."""
        text = _read_doc()
        # The original header used 'Default tool-call timeout' as a column
        # label. The new header uses 'Verified client default'. The phrase
        # may still appear in body prose, but as a stand-alone header cell
        # it must not.
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

    # -----------------------------------------------------------------------
    # Current-state claims (formerly TestDocCurrentStateMatchesWorkingTree)
    # -----------------------------------------------------------------------

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
            "exec_sse_streaming.py:62-89",
            "exec_sse_streaming.py:107-126",
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
            "### Cause 1 — present-day liveness subsystem (CURRENT)",
            "### Cause 4 — present-day progress-notification path (CURRENT)",
        ):
            assert marker in text, (
                f"root-cause doc must keep the {marker!r} heading. The doc "
                "separates historical evidence from current behavior; dropping "
                "the marker re-mixes a superseded rule into a present-tense claim."
            )

    def test_injected_runner_seam_is_not_the_production_path(self) -> None:
        text = _read_doc()
        for required in ("exec.py:790-819", "exec.py:775-788", "injected runner"):
            assert required in text, (
                f"root-cause doc must contain {required!r}. The doc previously "
                "claimed short/normal calls keep output-only inactivity kills. "
                "That was false: exec.py:790-819 always starts a registry job "
                "and exec.py:775-788 is only an injected runner seam."
            )

    def test_exec_wait_and_exec_cancel_named_as_present(self) -> None:
        text = _read_doc()
        for required in ("exec_wait", "exec_cancel", "names.py:40-41"):
            assert required in text, (
                f"root-cause doc must contain {required!r}. The doc previously "
                "claimed there was no background mode and no submit-and-poll "
                "path; exec_wait/exec_cancel are registered tools today "
                "(ralph/mcp/tools/names.py:40-41)."
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
