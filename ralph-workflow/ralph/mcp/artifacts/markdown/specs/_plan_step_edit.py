"""Document-wide ID-addressed step editing for Markdown plan artifacts."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.artifacts.markdown._artifact_error import MarkdownArtifactError
from ralph.mcp.artifacts.markdown._parser import parse_markdown_document
from ralph.mcp.artifacts.markdown._spec import MdArtifactSpec, parse_and_validate
from ralph.mcp.artifacts.markdown.specs._plan_steps import PLAN_STEP_ID_PATTERN


@dataclass(frozen=True)
class _StepSpan:
    """Source coordinates for one globally discovered plan step."""

    identifier: str
    start: int
    content_end: int


def _span_start(span: _StepSpan) -> int:
    """Return a step span's source-order key with strict typing."""
    return span.start


def edit_plan_step_markdown(
    text: str,
    action: str,
    step_id: str,
    replacement: str | None,
    index: int | None,
    *,
    spec: MdArtifactSpec,
) -> str:
    """Apply one ID-addressed step edit and validate the resulting document."""
    lines = text.splitlines()
    spans = _step_spans(text, lines)
    positions = {span.identifier: position for position, span in enumerate(spans)}
    if action == "insert":
        edited_lines = _insert(lines, spans, positions, step_id, replacement, index)
    else:
        position = positions.get(step_id)
        if position is None:
            raise ValueError(f"unknown step ID {step_id!r}")
        if action == "replace":
            if replacement is None:
                raise ValueError("replace requires a replacement block")
            span = spans[position]
            edited_lines = [
                *lines[: span.start],
                *_replacement_chunk(replacement, step_id),
                *lines[span.content_end :],
            ]
        elif action == "remove":
            span = spans[position]
            edited_lines = [*lines[: span.start], *lines[span.content_end :]]
        elif action == "move" and index is not None:
            edited_lines = _move(lines, spans, position, index)
        else:
            raise ValueError(
                "action must be replace, insert, remove, or move; move requires index"
            )
    edited = _join_lines(edited_lines, trailing_newline=text.endswith("\n"))
    _, validation = parse_and_validate(edited, spec)
    validation_errors = [diagnostic for diagnostic in validation if diagnostic.severity == "error"]
    if validation_errors:
        raise MarkdownArtifactError(validation_errors)
    return edited


def _step_spans(text: str, lines: list[str]) -> list[_StepSpan]:
    document, diagnostics = parse_markdown_document(text)
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    if errors:
        raise MarkdownArtifactError(errors)
    heading_lines = sorted(
        [section.line - 1 for section in document.sections]
        + [block.line - 1 for section in document.sections for block in section.blocks]
    )
    spans: list[_StepSpan] = []
    for section in document.sections:
        for block in section.blocks:
            if PLAN_STEP_ID_PATTERN.fullmatch(block.identifier) is None:
                continue
            start = block.line - 1
            boundary = next(
                (heading_line for heading_line in heading_lines if heading_line > start),
                len(lines),
            )
            content_end = boundary
            while content_end > start and not lines[content_end - 1].strip():
                content_end -= 1
            spans.append(_StepSpan(block.identifier, start, content_end))
    return sorted(spans, key=_span_start)


def _insert(
    lines: list[str],
    spans: list[_StepSpan],
    positions: dict[str, int],
    step_id: str,
    replacement: str | None,
    index: int | None,
) -> list[str]:
    if replacement is None or step_id in positions:
        raise ValueError("insert requires a new step ID and a replacement block")
    if PLAN_STEP_ID_PATTERN.fullmatch(step_id) is None:
        raise ValueError(f"step ID {step_id!r} must use the S-<positive-number> form")
    position = len(spans) if index is None else _edit_position(index, len(spans))
    chunk = _replacement_chunk(replacement, step_id)
    if position < len(spans):
        insertion = spans[position].start
        payload = [*chunk, ""]
    else:
        insertion = spans[-1].content_end if spans else len(lines)
        payload = ["", *chunk] if spans else chunk
    return [*lines[:insertion], *payload, *lines[insertion:]]


def _move(
    lines: list[str], spans: list[_StepSpan], position: int, index: int
) -> list[str]:
    desired = _edit_position(index, len(spans) - 1)
    if desired == position:
        return lines
    source = spans[position]
    chunk = lines[source.start : source.content_end]
    remaining = [*lines[: source.start], *lines[source.content_end :]]
    remaining_text = _join_lines(remaining, trailing_newline=False)
    remaining_spans = _step_spans(remaining_text, remaining)
    if desired < len(remaining_spans):
        insertion = remaining_spans[desired].start
        payload = [*chunk, ""]
    else:
        insertion = remaining_spans[-1].content_end
        payload = ["", *chunk]
    return [*remaining[:insertion], *payload, *remaining[insertion:]]


def _join_lines(lines: list[str], *, trailing_newline: bool) -> str:
    return "\n".join(lines) + ("\n" if trailing_newline else "")


def _replacement_chunk(replacement: str, step_id: str) -> list[str]:
    """Validate a replacement step block and return its normalized lines."""
    document, diagnostics = parse_markdown_document("## Steps\n" + replacement)
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    if errors:
        raise MarkdownArtifactError(errors)
    section = document.section("Steps")
    if (
        section is None
        or len(document.sections) != 1
        or len(section.blocks) != 1
        or section.items
        or section.lines
    ):
        raise ValueError("replacement must be a single '### [S-n] Title' step block")
    block = section.blocks[0]
    if block.identifier != step_id:
        raise ValueError(
            f"replacement block ID {block.identifier!r} must match step_id {step_id!r}"
        )
    chunk = replacement.splitlines()
    while chunk and not chunk[0].strip():
        chunk.pop(0)
    while chunk and not chunk[-1].strip():
        chunk.pop()
    return chunk


def _edit_position(index: int, length: int) -> int:
    if not 1 <= index <= length + 1:
        raise ValueError(f"index must be between 1 and {length + 1}")
    return index - 1
