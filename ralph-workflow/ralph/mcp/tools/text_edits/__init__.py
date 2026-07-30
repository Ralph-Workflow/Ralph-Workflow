"""Shared oldText/newText replacement engine for Ralph's editing tools.

``edit_file`` (workspace files) and ``ralph_edit_md_artifact`` (persisted
markdown artifact drafts) offer the same editing contract, so the semantics
live here once rather than in each handler:

* edits apply sequentially against the running content, each replacing the
  **first** occurrence of its ``oldText``;
* a single miss rejects the **whole batch** — callers write nothing, so an
  edit call is all-or-nothing;
* rejections carry a unified diff of the work completed before the miss, so
  the caller can see how far the batch got.

The engine is pure: it neither reads nor writes storage. Callers supply the
current text and persist the result, which keeps the semantics testable
independently of the workspace and draft backends.

Line-anchored matching (``anchor``) exists for ``edit_file``'s indexed
evidence/span/symbol targets. Draft editing passes ``anchor=None``: a draft
is not in the explore index, so there is no span to anchor to.
"""

from __future__ import annotations

from ralph.mcp.tools.text_edits._anchor import MATCH_STRATEGIES, TextEditAnchor
from ralph.mcp.tools.text_edits._applied import AppliedTextEdits
from ralph.mcp.tools.text_edits._diff import sha256_text, unified_text_diff
from ralph.mcp.tools.text_edits._engine import (
    apply_text_edits,
    line_range_to_byte_offsets,
)
from ralph.mcp.tools.text_edits._parse import parse_text_edits
from ralph.mcp.tools.text_edits._rejected import RejectedTextEdits
from ralph.mcp.tools.text_edits._text_edit import TextEdit

__all__ = [
    "MATCH_STRATEGIES",
    "AppliedTextEdits",
    "RejectedTextEdits",
    "TextEdit",
    "TextEditAnchor",
    "apply_text_edits",
    "line_range_to_byte_offsets",
    "parse_text_edits",
    "sha256_text",
    "unified_text_diff",
]
