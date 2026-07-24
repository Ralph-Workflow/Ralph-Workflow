"""Validate fenced artifact examples in prompt templates and format docs.

The audit covers two ways prompt authors teach artifact Markdown:

* literal fenced blocks in every packaged ``.jinja``/``.j2`` template and
  bundled format document; and
* complete example documents passed to the shared
  ``render_artifact_submission`` macro, which creates the fence at render time.

Artifact types are resolved fail-closed from an ``artifact=<registered_type>``
fence declaration, the format document's declared type, the submission macro's
literal type argument, or concrete ``type:`` frontmatter. The commit
frontmatter variants ``commit`` and ``skip`` resolve to the registered
``commit_message`` spec. Non-artifact code fences and the generic
``type: <artifact_type>`` grammar schematic are intentionally ignored.

Every resolved example is passed to
``ralph.mcp.artifacts.markdown.parse_and_validate`` with the registered spec.
Warnings are permitted because the artifact grammar explicitly uses warnings
for tolerated descriptive vocabulary; any error diagnostic fails the audit.
The plan format reference must additionally retain complete examples tagged
``example-size=tiny``, ``example-size=medium``, and ``example-size=large``. The
large example must model a four- or five-way work-unit/subplan fan-out followed
by one fan-in verification unit.

Usage:
    python -m ralph.testing.audit_fenced_artifact_examples

Exit 0 means every fenced artifact example validates; exit 1 means at least
one example is unresolved or invalid.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from importlib import import_module
from typing import cast

from jinja2 import Environment, StrictUndefined, TemplateError, Undefined, meta

from ralph.mcp.artifacts.format_docs import (
    FORMAT_DOC_ARTIFACT_TYPES,
    load_bundled_format_doc,
    load_bundled_format_index,
)
from ralph.mcp.artifacts.markdown import Diagnostic, parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec, registered_specs
from ralph.prompts.template_registry import packaged_template_root

_FENCE_OPEN_RE: re.Pattern[str] = re.compile(
    r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})(?P<info>[^\n]*)$"
)
_FENCE_ARTIFACT_TYPE_RE: re.Pattern[str] = re.compile(
    r"(?:^|\s)artifact(?:-type|_type)?=(?P<artifact_type>[a-z][a-z0-9_]*)"
)
_FENCE_EXAMPLE_SIZE_RE: re.Pattern[str] = re.compile(
    r"(?:^|\s)example-size=(?P<example_size>tiny|medium|large)(?=\s|$)"
)
_FRONTMATTER_TYPE_RE: re.Pattern[str] = re.compile(
    r"^type:\s*(?P<artifact_type>[^\n]+?)\s*$",
    re.MULTILINE,
)
_CONCRETE_ARTIFACT_TYPE_RE: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*$")
_PLAN_STEP_HEADING_RE: re.Pattern[str] = re.compile(
    r"^### \[S-[1-9][0-9]*\] .+$",
    re.MULTILINE,
)
_PLAN_UNIT_ITEM_RE: re.Pattern[str] = re.compile(
    r"^- \[(?P<unit_id>[A-Za-z0-9][A-Za-z0-9_-]*)\] "
    r"(?P<description>.+)$"
)
_SET_BLOCK_RE: re.Pattern[str] = re.compile(
    r"{%\s*set\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*%}"
    r"(?P<body>.*?)"
    r"{%\s*endset\s*%}",
    re.DOTALL,
)
_SUBMISSION_CALL_RE: re.Pattern[str] = re.compile(
    r"render_artifact_submission\(\s*"
    r"(?P<quote>['\"])(?P<artifact_type>[a-z][a-z0-9_]*)(?P=quote)\s*,"
    r"\s*[^,]+,\s*(?P<example_name>[A-Za-z_][A-Za-z0-9_]*)",
    re.DOTALL,
)
_TEMPLATE_GLOBS: tuple[str, ...] = ("*.jinja", "*.j2", "*.txt")
_MAX_FENCE_INDENT = 3


@dataclass(frozen=True)
class _ArtifactExample:
    source_name: str
    first_content_line: int
    markdown: str
    declared_artifact_type: str | None = None
    fence_info: str = ""


@dataclass(frozen=True)
class _SetBlock:
    name: str
    body: str
    first_content_line: int
    start: int


@dataclass(frozen=True)
class _PlanUnit:
    unit_id: str
    description: str
    dependencies: tuple[str, ...]


def _registered_artifact_types() -> frozenset[str]:
    import_module("ralph.mcp.artifacts.markdown.specs")
    return frozenset(spec.artifact_type for spec in registered_specs())


def _is_closing_fence(line: str, marker: str) -> bool:
    indentation = len(line) - len(line.lstrip(" "))
    if indentation > _MAX_FENCE_INDENT:
        return False
    stripped = line.lstrip(" ")
    marker_character = marker[0]
    marker_length = len(stripped) - len(stripped.lstrip(marker_character))
    return marker_length >= len(marker) and not stripped[marker_length:].strip()


def _literal_fenced_examples(
    source_name: str,
    source: str,
) -> tuple[list[_ArtifactExample], list[str]]:
    lines = source.splitlines()
    examples: list[_ArtifactExample] = []
    violations: list[str] = []
    index = 0
    while index < len(lines):
        opening = _FENCE_OPEN_RE.match(lines[index])
        if opening is None:
            index += 1
            continue
        marker = cast("str", opening.group("fence"))
        closing_index = index + 1
        while closing_index < len(lines) and not _is_closing_fence(
            lines[closing_index], marker
        ):
            closing_index += 1
        if closing_index == len(lines):
            info = cast("str", opening.group("info")).strip()
            if info.startswith(("markdown", "md")):
                violations.append(
                    f"{source_name}:{index + 1} unterminated markdown fence"
                )
            break
        examples.append(
            _ArtifactExample(
                source_name=source_name,
                first_content_line=index + 2,
                markdown="\n".join(lines[index + 1 : closing_index]) + "\n",
                fence_info=cast("str", opening.group("info")).strip(),
            )
        )
        index = closing_index + 1
    return examples, violations


def _set_blocks(source: str) -> tuple[_SetBlock, ...]:
    blocks: list[_SetBlock] = []
    for match in _SET_BLOCK_RE.finditer(source):
        body = cast("str", match.group("body"))
        leading_newlines = len(body) - len(body.lstrip("\n"))
        blocks.append(
            _SetBlock(
                name=cast("str", match.group("name")),
                body=body,
                first_content_line=(
                    source.count("\n", 0, match.start("body")) + leading_newlines + 1
                ),
                start=match.start(),
            )
        )
    return tuple(blocks)


def _render_example_body(body: str) -> str:
    strict_undefined = cast("type[Undefined]", StrictUndefined)
    environment = Environment(
        autoescape=False,
        undefined=strict_undefined,
        keep_trailing_newline=True,
    )
    parsed = environment.parse(body)
    variables = dict.fromkeys(
        sorted(meta.find_undeclared_variables(parsed)),
        "EX-1",
    )
    return environment.from_string(body).render(**variables).strip() + "\n"


def _macro_generated_examples(
    source_name: str,
    source: str,
) -> tuple[list[_ArtifactExample], list[str]]:
    blocks = _set_blocks(source)
    examples: list[_ArtifactExample] = []
    violations: list[str] = []
    for call in _SUBMISSION_CALL_RE.finditer(source):
        name = call.group("example_name")
        eligible = [block for block in blocks if block.name == name and block.start < call.start()]
        if not eligible:
            violations.append(
                f"{source_name}:{source.count(chr(10), 0, call.start()) + 1} "
                f"submission macro example {name!r} has no preceding set block"
            )
            continue
        block = eligible[-1]
        try:
            rendered = _render_example_body(block.body)
        except TemplateError as exc:
            violations.append(
                f"{source_name}:{block.first_content_line} could not render "
                f"submission macro example {name!r}: {exc}"
            )
            continue
        examples.append(
            _ArtifactExample(
                source_name=source_name,
                first_content_line=block.first_content_line,
                markdown=rendered,
                declared_artifact_type=call.group("artifact_type"),
            )
        )
    return examples, violations


def _frontmatter_type(markdown: str) -> tuple[bool, str | None]:
    lines = markdown.splitlines()
    first_nonempty = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_nonempty is None or lines[first_nonempty].strip() != "---":
        return False, None
    closing = next(
        (
            index
            for index in range(first_nonempty + 1, len(lines))
            if lines[index].strip() == "---"
        ),
        None,
    )
    if closing is None:
        return True, None
    frontmatter = "\n".join(lines[first_nonempty + 1 : closing])
    match = _FRONTMATTER_TYPE_RE.search(frontmatter)
    return (
        True,
        None
        if match is None
        else cast("str", match.group("artifact_type")).strip(),
    )


def _declared_fence_type(info: str) -> str | None:
    match = _FENCE_ARTIFACT_TYPE_RE.search(info)
    return None if match is None else match.group("artifact_type")


def _declared_example_size(info: str) -> str | None:
    match = _FENCE_EXAMPLE_SIZE_RE.search(info)
    return None if match is None else match.group("example_size")


def _inferred_spec_type(frontmatter_type: str, registered: frozenset[str]) -> str | None:
    if frontmatter_type in registered:
        return frontmatter_type
    if frontmatter_type in {"commit", "skip"}:
        return "commit_message"
    return None


def _resolve_declared_type(
    declared_type: str,
    registered: frozenset[str],
) -> tuple[str | None, str | None]:
    if declared_type not in registered:
        return (
            None,
            f"no registered artifact spec for declared type {declared_type!r}",
        )
    return declared_type, None


def _resolve_inferred_type(
    frontmatter_type: str | None,
    registered: frozenset[str],
) -> tuple[str | None, str | None]:
    if frontmatter_type is None:
        return None, "artifact-like fence has no concrete frontmatter 'type'"
    inferred = _inferred_spec_type(frontmatter_type, registered)
    if inferred is not None:
        return inferred, None
    if not _CONCRETE_ARTIFACT_TYPE_RE.fullmatch(frontmatter_type):
        return None, None
    return (
        None,
        f"no registered artifact spec for frontmatter type {frontmatter_type!r}; "
        "declare the spec with artifact=<type> on the opening fence",
    )


def _resolve_example_type(
    example: _ArtifactExample,
    default_artifact_type: str | None,
    registered: frozenset[str],
) -> tuple[str | None, str | None]:
    artifact_like, frontmatter_type = _frontmatter_type(example.markdown)
    fence_type = _declared_fence_type(example.fence_info)
    declared_type = fence_type or example.declared_artifact_type or default_artifact_type
    if not artifact_like and declared_type is None:
        return None, None
    if declared_type is None and frontmatter_type is not None and (
        "<" in frontmatter_type
        or ">" in frontmatter_type
        or "{{" in frontmatter_type
        or "{%" in frontmatter_type
    ):
        return None, None
    if declared_type is not None:
        return _resolve_declared_type(declared_type, registered)
    return _resolve_inferred_type(frontmatter_type, registered)


def _format_diagnostic(
    example: _ArtifactExample,
    diagnostic: Diagnostic,
    artifact_type: str,
) -> str:
    source_line = example.first_content_line + max(diagnostic.line - 1, 0)
    section = "" if diagnostic.section is None else f" section={diagnostic.section!r}"
    return (
        f"{example.source_name}:{source_line} [{diagnostic.rule_id}] "
        f"{diagnostic.message}{section} (artifact_type={artifact_type!r})"
    )


def _validate_example(
    example: _ArtifactExample,
    default_artifact_type: str | None,
    registered: frozenset[str],
) -> list[str]:
    artifact_type, resolution_error = _resolve_example_type(
        example,
        default_artifact_type,
        registered,
    )
    if resolution_error is not None:
        return [
            f"{example.source_name}:{example.first_content_line} {resolution_error}"
        ]
    if artifact_type is None:
        return []
    _, diagnostics = parse_and_validate(example.markdown, get_spec(artifact_type))
    return [
        _format_diagnostic(example, diagnostic, artifact_type)
        for diagnostic in diagnostics
        if diagnostic.severity == "error"
    ]


def check_source_examples(
    source_name: str,
    source: str,
    *,
    declared_artifact_type: str | None = None,
) -> list[str]:
    """Validate every artifact example present in one source document.

    Args:
        source_name: Stable path-like label used in diagnostics.
        source: Prompt-template or format-document source text.
        declared_artifact_type: Registered spec declared by the surrounding
            format document, when the frontmatter ``type`` is a variant such
            as ``commit`` or ``skip``.

    Returns:
        One source-anchored string per extraction, type-resolution, or
        validation error. Non-artifact fences and validator warnings are
        omitted.
    """
    registered = _registered_artifact_types()
    literal_examples, violations = _literal_fenced_examples(source_name, source)
    macro_examples, macro_violations = _macro_generated_examples(source_name, source)
    violations.extend(macro_violations)
    for example in (*literal_examples, *macro_examples):
        violations.extend(
            _validate_example(example, declared_artifact_type, registered)
        )
    return violations


def _section_body(markdown: str, title: str) -> str | None:
    heading = re.search(rf"^## {re.escape(title)}\s*$", markdown, re.MULTILINE)
    if heading is None:
        return None
    next_heading = re.search(r"^## .+$", markdown[heading.end() :], re.MULTILINE)
    end = (
        len(markdown)
        if next_heading is None
        else heading.end() + next_heading.start()
    )
    return markdown[heading.end() : end]


def _plan_units(markdown: str) -> tuple[_PlanUnit, ...]:
    section = _section_body(markdown, "Work Units")
    if section is None:
        section = _section_body(markdown, "Parallel Plan")
    if section is None:
        return ()
    units: list[_PlanUnit] = []
    current_id: str | None = None
    current_description = ""
    current_dependencies: tuple[str, ...] = ()
    for line in section.splitlines():
        item_match = _PLAN_UNIT_ITEM_RE.match(line)
        if item_match is not None:
            if current_id is not None:
                units.append(
                    _PlanUnit(
                        current_id,
                        current_description,
                        current_dependencies,
                    )
                )
            current_id = item_match.group("unit_id")
            current_description = item_match.group("description")
            current_dependencies = ()
            continue
        if current_id is not None and line.startswith("  Depends on:"):
            current_dependencies = tuple(
                dependency.strip()
                for dependency in line.removeprefix("  Depends on:").split(",")
                if dependency.strip()
            )
    if current_id is not None:
        units.append(
            _PlanUnit(
                current_id,
                current_description,
                current_dependencies,
            )
        )
    return tuple(units)


def _large_plan_shape_violation(example: _ArtifactExample) -> str | None:
    units = _plan_units(example.markdown)
    fan_out_count = len(units) - 1
    if fan_out_count not in {4, 5}:
        return (
            "example-size=large must contain four or five independent "
            "fan-out units plus one fan-in verification unit"
        )
    fan_out = units[:-1]
    if any(unit.dependencies for unit in fan_out):
        return "example-size=large fan-out units must be mutually independent"
    fan_in = units[-1]
    if "verif" not in fan_in.description.lower():
        return "example-size=large final fan-in unit must describe verification"
    if set(fan_in.dependencies) != {unit.unit_id for unit in fan_out}:
        return (
            "example-size=large final verification unit must depend on every "
            "fan-out unit"
        )
    return None


def check_plan_example_coverage(source_name: str, source: str) -> list[str]:
    """Check the plan format reference's task-size example coverage.

    Complete grammar validation remains the responsibility of
    :func:`check_source_examples`; this companion check locks the teaching
    corpus to tiny, medium, and large plan shapes and verifies that the large
    shape demonstrates bounded parallel fan-out followed by fan-in
    verification.

    Args:
        source_name: Stable path-like label used in diagnostics.
        source: Plan format-document source text.

    Returns:
        Source-anchored coverage and shape failures.
    """
    examples, _ = _literal_fenced_examples(source_name, source)
    by_size: dict[str, list[_ArtifactExample]] = {
        "tiny": [],
        "medium": [],
        "large": [],
    }
    for example in examples:
        artifact_like, frontmatter_type = _frontmatter_type(example.markdown)
        if not artifact_like:
            continue
        if _declared_fence_type(example.fence_info) != "plan" and frontmatter_type != "plan":
            continue
        example_size = _declared_example_size(example.fence_info)
        if example_size is not None:
            by_size[example_size].append(example)

    violations: list[str] = []
    for example_size, sized_examples in by_size.items():
        if not sized_examples:
            violations.append(
                f"{source_name}:1 plan examples must include "
                f"example-size={example_size}"
            )
    for example in by_size["tiny"]:
        step_count = len(cast("list[str]", _PLAN_STEP_HEADING_RE.findall(example.markdown)))
        if step_count not in {1, 2}:
            violations.append(
                f"{source_name}:{example.first_content_line} "
                "example-size=tiny must contain one or two plan steps"
            )
        if _plan_units(example.markdown):
            violations.append(
                f"{source_name}:{example.first_content_line} "
                "example-size=tiny must not declare parallel work units"
            )
    for example in by_size["medium"]:
        step_count = len(cast("list[str]", _PLAN_STEP_HEADING_RE.findall(example.markdown)))
        if step_count not in {3, 4}:
            violations.append(
                f"{source_name}:{example.first_content_line} "
                "example-size=medium must contain three or four plan steps"
            )
    for example in by_size["large"]:
        shape_violation = _large_plan_shape_violation(example)
        if shape_violation is not None:
            violations.append(
                f"{source_name}:{example.first_content_line} {shape_violation}"
            )
    return violations


def collect_violations() -> list[str]:
    """Validate all packaged prompt-template and format-doc examples.

    Returns:
        A sorted list of source-anchored failures. An empty list means every
        resolved example passed ``parse_and_validate`` with its registered
        spec.
    """
    violations: list[str] = []
    template_root = packaged_template_root()
    template_paths = sorted(
        {
            path
            for pattern in _TEMPLATE_GLOBS
            for path in template_root.rglob(pattern)
        }
    )
    for path in template_paths:
        relative = path.relative_to(template_root).as_posix()
        source_name = f"ralph/prompts/templates/{relative}"
        violations.extend(
            check_source_examples(
                source_name,
                path.read_text(encoding="utf-8"),
            )
        )
    for artifact_type in FORMAT_DOC_ARTIFACT_TYPES:
        document = load_bundled_format_doc(artifact_type)
        source_name = f"ralph/mcp/artifacts/format_docs/{artifact_type}.md"
        if document is None:
            violations.append(f"{source_name}:1 bundled format doc is missing")
            continue
        violations.extend(
            check_source_examples(
                source_name,
                document,
                declared_artifact_type=artifact_type,
            )
        )
        if artifact_type == "plan":
            violations.extend(check_plan_example_coverage(source_name, document))
    violations.extend(
        check_source_examples(
            "ralph/mcp/artifacts/format_docs/artifact_formats_index.md",
            load_bundled_format_index(),
        )
    )
    return sorted(violations)


def main(argv: list[str] | None = None) -> int:
    """Run the fenced artifact example audit.

    Args:
        argv: Unused argument list, accepted for consistency with other audit
            entry points.

    Returns:
        ``0`` when every example validates against its registered spec,
        otherwise ``1`` after printing actionable diagnostics.
    """
    del argv
    violations = collect_violations()
    if violations:
        print(
            "FENCED ARTIFACT EXAMPLE AUDIT FAILED: "
            f"{len(violations)} violation(s)"
        )
        print("=" * 72)
        for violation in violations:
            print(f"  {violation}")
        print()
        print(
            "Every artifact example in prompt templates and format docs must "
            "resolve to a registered artifact type and pass parse_and_validate. "
            "Fix the example or declare its registered spec with "
            "'artifact=<type>' on the opening markdown fence."
        )
        return 1
    print(
        "Fenced artifact example audit OK: every prompt-template and "
        "format-doc artifact example passed its registered markdown spec."
    )
    return 0


__all__ = [
    "check_plan_example_coverage",
    "check_source_examples",
    "collect_violations",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
