"""Evidence-bullet mapping for the markdown ``plan`` spec.

Split out of :mod:`ralph.mcp.artifacts.markdown.specs.plan` so that module
stays under the 1000-line repo-structure cap. The dependency direction is
one-way: this module imports the shared markdown primitives only, and
``plan.py`` imports :func:`evidence_content` from here.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._parsed_line import ParsedLine
    from ralph.mcp.artifacts.markdown._spec import Content

_EVIDENCE_ENTRY = re.compile(r"^(?P<kind>[a-z_]+): (?P<ref>\S(?:.*\S)?)$")
#: Canonical ``EvidenceKind`` values. Parity with the model Literal is
#: pinned by ``tests/mcp/test_md_plan_spec.py``.
_EVIDENCE_KINDS: frozenset[str] = frozenset({"file", "command_output", "test_name"})


def evidence_content(entry: ParsedLine, context: str, diagnostics: list[Diagnostic]) -> Content:
    """Map one evidence bullet to a canonical ``EvidenceRef`` content dict.

    ``kind`` is a consumed discriminator that the canonical model keeps
    fail-closed, so a bullet opening with an unrecognised ``word:``
    prefix is descriptive rather than invalid: it is coerced to
    ``file`` with a warning instead of failing the document.
    """
    match = _EVIDENCE_ENTRY.fullmatch(entry.text)
    if match is None:
        return {"kind": "file", "ref": entry.text}
    kind = cast("str", match.group("kind"))
    ref = cast("str", match.group("ref"))
    if kind in _EVIDENCE_KINDS:
        return {"kind": kind, "ref": ref}
    diagnostics.append(
        Diagnostic(
            entry.line,
            "Steps",
            "PLAN006",
            f"{context}: unknown evidence kind coerced to 'file'",
            "warning",
        )
    )
    return {"kind": "file", "ref": ref}
