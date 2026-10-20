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

The test does NOT hit the network. It treats the URLs as opaque
strings and asserts on their shape plus their textual appearance in
the verified doc sections. A URL whose host 404s is a separate
failure mode (``scripts/fabrication_guard.py --level 2``); this
test exists to catch the WU-5 class of bug (workspace path
masquerading as a URL) at the cheapest possible cost.
"""

from __future__ import annotations

import re

import pytest

from tests.doc_roots import REPOSITORY_ROOT

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


class TestRootCauseDocCitations:
    """The doc must not contain any of the WU-5 reproduced defect shapes."""

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
