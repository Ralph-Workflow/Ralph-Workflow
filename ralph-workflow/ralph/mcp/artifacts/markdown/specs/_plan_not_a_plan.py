"""Closed-list detector for the 'is this a plan?' gate.

The plan validator may raise an error only when the document is
recognizably not a plan. The closed list lives in this module so the
detection logic stays reviewable in one place and the rest of the
validator never has to re-derive what counts.

Closed-list classes:

- Empty or effectively empty: nothing, whitespace only, or nothing but
  markup (a bare heading, a horizontal rule, an empty code fence).
- Under 100 characters of actual content.
- Recognizably some other kind of text that arrived in the plan's
  place: a refusal or apology, a question back to the user, a bare
  status / progress message, raw tool output, a stack trace, or a
  placeholder such as ``TODO`` / ``plan goes here``.

Doubt resolves in favor of the plan: any document with plan-shape
evidence (a ``### [S-n]`` block, a ``- [ID] text`` item, a substantial
section body) bypasses the detector. A document that *might* be a plan
is a plan. The ``noop: true`` exemption covers the canonical
zero-content plan.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._parser import parse_markdown_document

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument

# Single rule_id so error gating, test assertions, and override audit all
# share one identifier.
RULE_ID = "PLAN001"

# The detector is crude by design. Doubt-in-favor is the load-bearing
# principle: a thin, unusual, or unconventionally shaped real plan is
# still a plan, and only obvious accidents surface here.

# 100-character floor below which a document cannot state an outcome,
# let alone how to reach one.
_MIN_CONTENT_CHARS = 100

# Plan-shape markers: any one of these is enough to declare the
# document a plan. The thresholds are deliberately loose so that a
# one-paragraph plan that omits the conventional outline is still a
# plan.
_STEP_BLOCK_PATTERN = re.compile(r"^#{3,6} \[[A-Za-z][A-Za-z0-9_-]*\]")
_STABLE_ID_ITEM_PATTERN = re.compile(r"^- (?: \[[ xX]\] )? \[[A-Za-z][A-Za-z0-9_-]*\]")
_SUBSTANTIAL_BODY_CHARS = 50

# Recognizably-other-text patterns. Each pattern is anchored to the
# start of a content line (case-folded) so a plan that happens to
# mention the word "TODO" inside a step body does not trip the
# placeholder check.
_REFUSAL_PREFIXES = (
    "i'm sorry",
    "im sorry",
    "i cannot",
    "i can not",
    "i can't",
    "as an ai",
    "i am an ai",
    "sorry, but",
    "sorry i",
)
_QUESTION_BACK_FIRST_WORDS = (
    "what ",
    "how ",
    "why ",
    "when ",
    "where ",
    "who ",
    "could you",
    "would you",
    "can you",
    "do you",
    "did you",
    "are you",
    "is there",
)
_PROGRESS_PREFIXES = (
    "working on ",
    "currently ",
    "progress:",
    "status:",
    "in progress:",
    "starting ",
    "now inspecting ",
    "drafting ",
    "writing ",
)
_PLACEHOLDER_KEYWORDS = (
    "plan goes here",
    "plan goes later",
    "todo: plan",
    "todo plan",
    "fixme: plan",
    "tbd: plan",
)
_STACK_TRACE_PATTERN = "traceback (most recent call last)"
_TOOL_OUTPUT_INDICATORS = (
    "traceback (most recent call last)",  # also catches stack traces
    "drwx",  # `ls -la` output
    "-rw-r--r--",
    "total ",
    "usage:",
    "error: ",  # generic tool error
)


def _consumer_rule_message(what: str, fix: str, consumer: str) -> str:
    """Compose the blocking convention message.

    Plan-severity findings use the same ``what; <consumer>; resolve by <fix>``
    convention every other blocking diagnostic uses, so the agent reads
    in linear order: what was observed, who cannot proceed, and what
    to do about it.
    """
    return f"{what}; {consumer}; resolve by {fix}"


_NOT_A_PLAN_CONSUMER = (
    "blocking because the analysis phase and the executor both have "
    "nothing to work with when the submitted text is not a plan"
)


def _empty_message() -> str:
    return _consumer_rule_message(
        "submitted text is empty or only whitespace / markup",
        "submitting a real plan with at least one '### [S-n]' step block "
        "(or frontmatter 'noop: true' for a no-op plan)",
        _NOT_A_PLAN_CONSUMER,
    )


def _too_short_message(content_chars: int) -> str:
    return _consumer_rule_message(
        f"submitted text has only {content_chars} characters of actual "
        f"content (under the {_MIN_CONTENT_CHARS}-character floor)",
        "submitting a real plan that states an outcome, the files in "
        "play, and a verification step the analysis phase can run",
        _NOT_A_PLAN_CONSUMER,
    )


def _other_text_message(reason: str) -> str:
    return _consumer_rule_message(
        f"submitted text is recognizably {reason} that arrived in the "
        f"plan's place",
        "submitting a real plan that states the outcome, the change, "
        "and the verification",
        _NOT_A_PLAN_CONSUMER,
    )


def _is_noop_only(document: ParsedDocument) -> bool:
    """True iff document is the canonical ``{"type": "plan", "noop": "true"}`` plan."""
    return (
        document.frontmatter == {"type": "plan", "noop": "true"}
        and not document.sections
    )


def _has_plan_shape(document: ParsedDocument) -> bool:
    """Return True iff document carries any plan-shape evidence.

    A ``### [S-n]`` step block (parsed as a :class:`ParsedBlock` inside a
    section), a ``- [ID] text`` stable-ID item, or a substantial
    ``## Heading`` body section is enough to declare the document a
    plan; doubt resolves in favor of the plan.
    """
    for section in document.sections:
        if any(
            _STEP_BLOCK_PATTERN.match(line.text) for line in section.lines
        ):
            return True
        if any(
            _STABLE_ID_ITEM_PATTERN.match(item.text) for item in section.items
        ):
            return True
        # ``### [S-n]`` step blocks are stored as ``ParsedBlock`` objects,
        # not section lines; the block's identifier matches the step
        # pattern independently of where it sits in the heading tree.
        if any(
            _STEP_BLOCK_PATTERN.match(f"### [{block.identifier}]")
            for block in section.blocks
        ):
            return True
        if any(
            _STABLE_ID_ITEM_PATTERN.match(item.text)
            for block in section.blocks
            for item in block.lines
        ):
            return True
        total_body = sum(len(line.text) for line in section.lines)
        total_body += sum(
            len(line.text) for block in section.blocks for line in block.lines
        )
        if total_body >= _SUBSTANTIAL_BODY_CHARS:
            return True
    return False


def _extract_body_text(text: str) -> str:
    """Return the text after the closing frontmatter ``---`` line.

    The detector reasons about *body* text only; the frontmatter fields
    (e.g. ``type: plan``) are not part of the plan's substance, so they
    must not be the first content line, the length floor, or the
    pattern that names the consumer. If the document has no frontmatter
    block at all, the whole text is the body.
    """
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return text
    for index in range(1, len(lines)):
        if lines[index] == "---":
            return "\n".join(lines[index + 1 :])
    return text  # unterminated frontmatter - let the parser flag it


def _content_char_count(text: str) -> int:
    """Count non-whitespace, non-markup characters in ``text``.

    Used to gate the 100-character floor. ``text`` should be the
    post-frontmatter body; the parser already strips frontmatter fences
    and code-fence delimiters, but a robust count needs to handle the
    same set of "not content" markers independently of parser state.
    """
    in_code_fence = False
    chars = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        chars += len(stripped.replace(" ", "").replace("\t", ""))
    return chars


def _first_content_line(text: str) -> str:
    """Return the first non-whitespace, non-markup line, lowercased.

    ``text`` should be the post-frontmatter body so the frontmatter
    fields (e.g. ``type: plan``) do not masquerade as the document's
    opening line.
    """
    in_code_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        return stripped.lower()
    return ""


def _is_refusal(first_line: str) -> bool:
    return any(first_line.startswith(prefix) for prefix in _REFUSAL_PREFIXES)


def _is_question_back(first_line: str, document: ParsedDocument) -> bool:
    """A first-line question AND no plan-shape evidence in the document.

    A question that lives inside a real plan body is not a question-back;
    only a document that is *just* a question (and nothing else shaped
    like a plan) trips this branch.
    """
    if not any(first_line.startswith(word) for word in _QUESTION_BACK_FIRST_WORDS):
        return False
    if "?" not in first_line:
        return False
    return not _has_plan_shape(document)


def _is_progress(first_line: str) -> bool:
    return any(first_line.startswith(prefix) for prefix in _PROGRESS_PREFIXES)


def _is_placeholder(text_lower: str) -> bool:
    return any(keyword in text_lower for keyword in _PLACEHOLDER_KEYWORDS)


def _is_stack_trace(text_lower: str) -> bool:
    return _STACK_TRACE_PATTERN in text_lower


def _is_tool_output(text_lower: str, first_line: str) -> bool:
    if any(indicator in text_lower for indicator in _TOOL_OUTPUT_INDICATORS):
        return True
    # Shell prompt or shell-prompt listing at the start of the content.
    return first_line.startswith(("$ ", "# ", "> "))


def detect_not_a_plan(text: str) -> list[Diagnostic]:
    """Return 0+ PLAN001 error diagnostics for the closed not-a-plan list.

    The detector fires only on the four classes named above; doubt
    resolves in favor of the plan. The ``noop: true`` exemption short-
    circuits before any other check so the canonical no-op plan always
    validates.
    """
    # Noop exemption short-circuit: the canonical zero-content plan.
    try:
        document, _parser_diagnostics = parse_markdown_document(
            text, allow_nested_headings=True
        )
    except Exception:  # pragma: no cover - parser never raises
        return []
    if _is_noop_only(document):
        return []
    # Plan-shape evidence bypasses every closed-list check.
    if _has_plan_shape(document):
        return []

    body_text = _extract_body_text(text)
    content_chars = _content_char_count(body_text)
    text_lower = body_text.lower()
    first_line = _first_content_line(body_text)

    # (why, message) — first match wins; doubt-in-favor means only one
    # closed-list reason can fire per document.
    reasons: tuple[tuple[bool, str], ...] = (
        (content_chars == 0, _empty_message()),
        (
            content_chars < _MIN_CONTENT_CHARS,
            _too_short_message(content_chars),
        ),
        (_is_refusal(first_line), _other_text_message("a refusal or apology")),
        (_is_stack_trace(text_lower), _other_text_message("a stack trace")),
        (
            _is_placeholder(text_lower),
            _other_text_message("a placeholder such as 'TODO' or 'plan goes here'"),
        ),
        (_is_progress(first_line), _other_text_message("a status or progress message")),
        (
            _is_tool_output(text_lower, first_line),
            _other_text_message("raw tool output"),
        ),
        (
            _is_question_back(first_line, document),
            _other_text_message("a question back to the user"),
        ),
    )
    for matched, message in reasons:
        if not matched:
            continue
        return [Diagnostic(1, None, RULE_ID, message, "error")]
    return []


def apply_plan_severity_policy(diagnostics: list[Diagnostic]) -> None:
    """In-place demote plan-scoped shape findings from error to warning.

    The policy is plan-scoped: only ``PLAN001`` plus the rule_ids a
    downstream pipeline consumer does not actually need (step IDs,
    consumed references, dependency graphs, work-unit markers) are
    demoted. Routing / transport classes (``SPEC002``, ``MD005``,
    ``MD006``, ``MD007``, ``SPEC001``, ``PLAN020`` type mismatch and
    schema_version, ``PLAN023``, ``PLAN025`` malformed override entry)
    stay blocking because a downstream consumer genuinely cannot
    proceed without them.

    SPEC010 emitted from the pydantic normalizer is also demoted: a
    pydantic schema rejection is content-shape, not routing, and the
    plan has the markdown-side routing check standing beside it.

    The function is intentionally narrow. Other artifact types' severities
    are byte-for-byte unchanged because the policy only runs for plans.
    """
    for index, diagnostic in enumerate(diagnostics):
        if diagnostic.severity != "error":
            continue
        if diagnostic.rule_id in _PLAN_DEMOTED_RULES:
            new_message = _reword_as_advisory(diagnostic)
            diagnostics[index] = replace(
                diagnostic, severity="warning", message=new_message
            )
            continue
        if diagnostic.rule_id == "SPEC010" and _is_pydantic_branch(diagnostic.message):
            new_message = _reword_as_advisory(diagnostic)
            diagnostics[index] = replace(
                diagnostic, severity="warning", message=new_message
            )


def _reword_as_advisory(diagnostic: Diagnostic) -> str:
    """Convert a blocking-severity message to advisory cost-named wording.

    The blocking convention is ``<what>; blocking because <consumer>; resolve by <fix>``.
    The advisory convention is ``<what>; the run cost is <cost>; resolve by <fix>``.
    A diagnostic that lacks the blocking phrase already follows some
    other convention; pass it through unchanged so the policy does not
    invent text where the original author left none.
    """
    message = diagnostic.message
    blocking_marker = "blocking because "
    resolve_marker = "; resolve by "
    blocking_index = message.find(blocking_marker)
    resolve_index = message.find(resolve_marker)
    if blocking_index < 0 or resolve_index < 0 or resolve_index < blocking_index:
        return message
    what = message[:blocking_index].rstrip(" ;")
    cost_and_fix = message[resolve_index + len("; "):]
    return f"{what}; the run cost is the finding is now advisory so the agent may proceed past it; {cost_and_fix}"


# Rule IDs that should demote from error to warning for plan-scoped
# flows. Anything not in this set stays blocking.
_PLAN_DEMOTED_RULES = frozenset(
    {
        # Markdown body grammar — parser-originated.
        "MD001",  # block under items-only section
        "MD002",  # top-level prose
        # Section shape mismatches that are not routing.
        "SPEC011",  # items in items-only section
        "SPEC012",  # blocks in blocks-only section
        # Reference / graph findings — content-shape, not routing.
        "REF001",  # malformed stable ID
        "REF002",  # duplicate stable ID
        "REF003",  # unknown reference
        "REF004",  # dependency cycle
        # Plan-shape findings — content-shape, not routing.
        "PLAN021",  # dangling step reference
        "PLAN022",  # malformed / duplicate step ID
        "PLAN024",  # malformed work-unit marker
    }
)


def _is_pydantic_branch(message: str) -> bool:
    """True iff the SPEC010 message is a content-shape pydantic rejection.

    The brief keeps the bounded-MCP-payload size bound blocking
    ("the plan-size transport bound" is a named consumer) and only
    demotes the content-shape pydantic rejections. Size violations
    show up two ways:

    - The plan-artifact size guard emits ``plan size violation: ...``.
    - Per-field pydantic ``max_length=...`` constraints emit
      ``String should have at most N characters; rejected value has M``.

    Both are transport bound, not content shape, so neither is
    demoted. The policy stays advisory-only on the remaining
    pydantic / schema rejections (the content-shape classes the
    brief lists as advisory).
    """
    lowered = message.lower()
    if lowered.startswith("plan size violation"):
        return False
    if "string should have at most" in lowered:
        # Per-field pydantic max_length rejection — transport bound.
        return False
    return "pydantic" in lowered or "schema" in lowered


__all__ = [
    "RULE_ID",
    "apply_plan_severity_policy",
    "detect_not_a_plan",
]
