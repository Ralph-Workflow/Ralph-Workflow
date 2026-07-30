"""Closed-list detector for the 'is this a plan?' gate.

The plan validator may raise an error only when the document is
recognizably not a plan. The closed list lives in this module so the
detection logic stays reviewable in one place and the rest of the
validator never has to re-derive what counts.

Closed-list classes:

- Empty or effectively empty: nothing, whitespace only, or nothing but
  markup (a bare heading, a horizontal rule, an empty code fence).
- Under 100 characters of actual content.
- Recognizably truncated at EOF: an unclosed code fence or a dangling
  plan field label with no value.
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
_DANGLING_FIELD_PATTERN = re.compile(
    r"^(?:Files|Depends on|Satisfies|Verify|Expect|Location|Evidence|Rationale|Mitigation|Type|Priority):$",
    re.IGNORECASE,
)
_DANGLING_FUNCTION_WORD_PATTERN = re.compile(
    r"\b(?:the|a|an|and|or|but|to|of|in|on|for|with|then|so|that|which|is|are|be|by|at|from|as)$",
    re.IGNORECASE,
)
_EMPTY_BULLET_PATTERN = re.compile(r"^[-*+]\s*$")
_FENCE_OPENING_PATTERN = re.compile(r"^(?P<fence>`{3,}|~{3,})(?:[^`~].*)?$")

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


def _fence_scanner(text: str) -> tuple[list[str], bool]:
    """Return non-fenced content lines and whether a fence remains open at EOF."""
    content_lines: list[str] = []
    open_fence: tuple[str, int] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if open_fence is not None:
            character, minimum = open_fence
            if re.fullmatch(rf"{re.escape(character)}{{{minimum},}}", stripped):
                open_fence = None
            continue
        opening = _FENCE_OPENING_PATTERN.fullmatch(stripped)
        if opening is not None:
            fence = opening.group("fence")
            if not isinstance(fence, str):  # pragma: no cover - named regex group is always str
                continue
            open_fence = (fence[0], len(fence))
            continue
        content_lines.append(stripped)
    return content_lines, open_fence is not None


def _content_char_count(text: str) -> int:
    """Count non-whitespace, non-markup characters in ``text``.

    Used to gate the 100-character floor. ``text`` should be the
    post-frontmatter body; the parser already strips frontmatter fences
    and code-fence delimiters, but a robust count needs to handle the
    same set of "not content" markers independently of parser state.
    """
    chars = 0
    for stripped in _fence_scanner(text)[0]:
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
    for stripped in _fence_scanner(text)[0]:
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        return stripped.lower()
    return ""


def _is_recognizably_truncated(text: str) -> str | None:
    """Return the unambiguous EOF truncation reason, if present."""
    content_lines, fence_open = _fence_scanner(text)
    if fence_open:
        return "an unclosed code fence at end of file"
    final_line = next((line for line in reversed(content_lines) if line), "")
    signals: tuple[tuple[bool, str], ...] = (
        (_DANGLING_FIELD_PATTERN.fullmatch(final_line) is not None, "a dangling plan field label at end of file"),
        (final_line.endswith(","), "a final content line ending with a comma"),
        (_DANGLING_FUNCTION_WORD_PATTERN.search(final_line) is not None, "a final content line ending with a dangling function word"),
        (_has_unclosed_inline_delimiter(final_line), "an unclosed inline delimiter on the final content line"),
        (_EMPTY_BULLET_PATTERN.fullmatch(final_line) is not None, "a list bullet with no text"),
    )
    return next((reason for matched, reason in signals if matched), None)


def _has_unclosed_inline_delimiter(line: str) -> bool:
    """Return whether a final prose line leaves a simple inline delimiter open."""
    pairs = (("[", "]"), ("(", ")"), ("`", "`"))
    return any(
        line.count(opener) > line.count(closer)
        if opener != closer
        else line.count(opener) % 2
        for opener, closer in pairs
    )


def _truncation_message(reason: str) -> str:
    return _consumer_rule_message(
        f"submitted text is recognizably truncated: {reason}",
        "completing the interrupted fence or adding the missing field value before submitting",
        _NOT_A_PLAN_CONSUMER,
    )


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

    The actual-content length floor runs *before* the plan-shape bypass
    so a truncated agent response that happens to include a single step
    block cannot bypass the 100-character floor on a shape coincidence.
    The plan-shape bypass only protects the recognizably-other-text
    checks (``_is_refusal``, ``_is_tool_output``, etc.) where a real
    plan body can incidentally match a crude prefix while still being a
    plan.
    """
    # Noop exemption short-circuit: the canonical zero-content plan.
    try:
        document, _parser_diagnostics = parse_markdown_document(
            text, allow_nested_headings=True
        )
    except Exception:  # pragma: no cover - parser never raises
        return []
    body_text = _extract_body_text(text)
    content_chars = _content_char_count(body_text)
    text_lower = body_text.lower()
    first_line = _first_content_line(body_text)
    message: str | None = None

    # Length floor runs before the plan-shape bypass. A document that
    # happens to include ``### [S-1] Do`` and nothing else cannot be a plan.
    if _is_noop_only(document):
        pass
    elif content_chars == 0:
        message = _empty_message()
    elif content_chars < _MIN_CONTENT_CHARS:
        message = _too_short_message(content_chars)
    elif truncation_reason := _is_recognizably_truncated(body_text):
        message = _truncation_message(truncation_reason)
    # Plan-shape evidence bypasses recognizably-other-text checks: doubt
    # resolves in favor of a real plan that incidentally matches a prefix.
    elif not _has_plan_shape(document):
        reasons: tuple[tuple[bool, str], ...] = (
            (_is_refusal(first_line), _other_text_message("a refusal or apology")),
            (_is_stack_trace(text_lower), _other_text_message("a stack trace")),
            (
                _is_placeholder(text_lower),
                _other_text_message("a placeholder such as 'TODO' or 'plan goes here'"),
            ),
            (_is_progress(first_line), _other_text_message("a status or progress message")),
            (_is_tool_output(text_lower, first_line), _other_text_message("raw tool output")),
            (
                _is_question_back(first_line, document),
                _other_text_message("a question back to the user"),
            ),
        )
        message = next((reason for matched, reason in reasons if matched), None)

    return [Diagnostic(1, None, RULE_ID, message, "error")] if message else []


def apply_plan_severity_policy(diagnostics: list[Diagnostic]) -> None:
    """Make ``PLAN001`` the plan spec's sole blocking diagnostic.

    A downstream consumer can recover from malformed markdown, unknown
    fields, and incomplete structure by reading the submitted text; it cannot
    recover when no plan exists. Therefore every other plan finding is advice.
    This policy is invoked only by ``PLAN_SPEC`` and cannot affect other
    artifact types.
    """
    for index, diagnostic in enumerate(diagnostics):
        if diagnostic.severity == "error" and diagnostic.rule_id != RULE_ID:
            diagnostics[index] = replace(
                diagnostic,
                severity="warning",
                message=_reword_as_advisory(diagnostic),
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
    if blocking_index >= 0 and resolve_index >= blocking_index:
        what = message[:blocking_index].rstrip(" ;")
        fix = message[resolve_index + len(resolve_marker) :]
    else:
        what = message.rstrip(".")
        fix = "repairing the noted markdown or recording an override with the reason"
    return (
        f"{what}; the run cost is canonical plan mapping may omit or misread "
        f"this part of the plan; resolve by {fix}"
    )


__all__ = [
    "RULE_ID",
    "apply_plan_severity_policy",
    "detect_not_a_plan",
]
