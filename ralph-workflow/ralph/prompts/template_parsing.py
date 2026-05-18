"""Template parsing helpers ported from the Rust prompt templates module."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.prompts._conditional_node import ConditionalNode
from ralph.prompts._loop_node import LoopNode
from ralph.prompts._partial_node import PartialNode
from ralph.prompts._template_node import TemplateNode
from ralph.prompts._text_node import TextNode
from ralph.prompts._token import _Token
from ralph.prompts._variable_node import VariableNode

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from ralph.prompts._ast_frame import _AstFrame

# Minimum length for a metadata comment like "{# V #}"
METADATA_COMMENT_MIN_LENGTH = 4

type TemplateAST = list[TemplateNode]


__all__ = [
    "ConditionalNode",
    "LoopNode",
    "PartialNode",
    "TemplateNode",
    "TextNode",
    "VariableNode",
    "eval_conditional",
    "is_metadata_comment",
    "parse_metadata_line",
    "parse_template",
    "parse_variable_spec",
    "split_loop_items",
    "strip_comments",
]


def parse_template(content: str) -> TemplateAST:
    """Parse a template into a list of AST nodes."""

    cleaned = strip_comments(content)
    tokens = list(_tokenize(cleaned))
    return _build_ast(tokens)


def strip_comments(content: str) -> str:
    """Remove `{# ... #}` comment blocks from a template."""

    parts: list[str] = []
    cursor = 0
    length = len(content)
    while cursor < length:
        start = content.find("{#", cursor)
        if start == -1:
            parts.append(content[cursor:])
            break
        parts.append(content[cursor:start])
        end = content.find("#}", start + 2)
        if end == -1:
            parts.append(content[start:])
            break
        cursor = end + 2
        if cursor < length and content[cursor] == "\n":
            cursor += 1
    return "".join(parts)


def _tokenize(content: str) -> Iterable[_Token]:
    i = 0
    length = len(content)
    while i < length:
        if content.startswith("{{", i):
            end = content.find("}}", i + 2)
            if end == -1:
                yield _Token("text", content[i:])
                return
            inner = content[i + 2 : end].strip()
            if inner.startswith(">"):
                yield _Token("partial", inner[1:].strip())
            else:
                yield _Token("variable", inner)
            i = end + 2
            continue
        if content.startswith("{%", i):
            end = content.find("%}", i + 2)
            if end == -1:
                yield _Token("text", content[i:])
                return
            inner = content[i + 2 : end].strip()
            yield _Token("tag", inner)
            i = end + 2
            continue
        next_positions = [content.find("{{", i), content.find("{%", i)]
        next_pos = length
        for pos in next_positions:
            if pos != -1 and pos < next_pos:
                next_pos = pos
        if next_pos == length:
            yield _Token("text", content[i:])
            return
        yield _Token("text", content[i:next_pos])
        i = next_pos


def _build_ast(tokens: Sequence[_Token]) -> TemplateAST:
    root_nodes: list[TemplateNode] = []
    stack: list[_AstFrame] = [{"type": "root", "nodes": root_nodes, "node": None}]

    for token in tokens:
        context = stack[-1]
        if token.kind == "text":
            if token.value:
                context["nodes"].append(TextNode(token.value))
            continue

        if token.kind == "variable":
            _handle_variable(token, context)
            continue

        if token.kind == "partial":
            name = token.value.strip()
            if name:
                context["nodes"].append(PartialNode(name=name))
            continue

        if token.kind == "tag":
            _handle_tag(token, context, stack)
            continue

    return root_nodes


def _handle_variable(token: _Token, context: _AstFrame) -> None:
    """Handle a variable token."""
    parsed = parse_variable_spec(token.value)
    if parsed is None:
        context["nodes"].append(TextNode(f"{{{{{token.value}}}}}"))
    else:
        name, default = parsed
        context["nodes"].append(VariableNode(name=name, default=default, placeholder=token.value))


def _handle_tag(
    token: _Token, context: _AstFrame, stack: list[_AstFrame]
) -> None:
    """Handle a tag token."""
    parts = token.value.split(None, 1)
    if not parts:
        return
    keyword = parts[0].lower()
    remainder = parts[1] if len(parts) > 1 else ""

    if keyword == "for":
        variable, iterable = _parse_for_header(remainder)
        loop = LoopNode(variable=variable, iterable=iterable, body=[])
        context["nodes"].append(loop)
        stack.append({"type": "loop", "nodes": loop.body, "node": loop})
        return

    if keyword == "endfor":
        if len(stack) > 1 and stack[-1]["type"] == "loop":
            stack.pop()
        return

    if keyword == "if":
        condition = remainder.strip()
        conditional = ConditionalNode(condition=condition, truthy=[], falsy=[])
        context["nodes"].append(conditional)
        stack.append({"type": "if_truthy", "nodes": conditional.truthy, "node": conditional})
        return

    if keyword == "else":
        if len(stack) > 1 and stack[-1]["type"] == "if_truthy":
            frame = stack.pop()
            frame_node = frame["node"]
            if isinstance(frame_node, ConditionalNode):
                stack.append({"type": "if_falsy", "nodes": frame_node.falsy, "node": frame_node})
        return

    if keyword == "endif":
        if len(stack) > 1 and stack[-1]["type"] in {"if_truthy", "if_falsy"}:
            stack.pop()
        return

    context["nodes"].append(TextNode(f"{{% {token.value} %}}"))


def _parse_for_header(header: str) -> tuple[str, str]:
    header = header.strip()
    if " in " in header:
        variable, iterable = header.split(" in ", 1)
        return variable.strip(), iterable.strip()
    parts = header.split()
    if parts:
        return parts[0], " ".join(parts[1:]).strip()
    return "", ""


def parse_variable_spec(var_spec: str) -> tuple[str, str | None] | None:
    """Parse a variable spec string into (name, default) or None if invalid."""
    trimmed = var_spec.strip()
    if not trimmed or trimmed.startswith(">"):
        return None
    if "|" not in trimmed:
        return trimmed, None
    name_part, rest = trimmed.split("|", 1)
    name = name_part.strip()
    default_value: str | None = None
    if "=" in rest:
        key, _, raw = rest.partition("=")
        if key.strip() == "default":
            value = raw.strip()
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            default_value = value
    return name, default_value


def parse_metadata_line(line: str) -> tuple[str | None, str | None] | None:
    """Parse a `{# ... #}` metadata comment line into (version, purpose) or None."""
    trimmed = line.strip()
    if (
        len(trimmed) < METADATA_COMMENT_MIN_LENGTH
        or not trimmed.startswith("{#")
        or not trimmed.endswith("#}")
    ):
        return None
    inner = trimmed[2:-2].strip()
    version = None
    purpose = None
    if inner.startswith("Version:"):
        version = inner[len("Version:") :].strip()
    if inner.startswith("PURPOSE:"):
        purpose = inner[len("PURPOSE:") :].strip()
    return version, purpose


def is_metadata_comment(line: str) -> bool:
    """Return True if the line is a `{# ... #}` metadata comment."""
    trimmed = line.strip()
    return trimmed.startswith("{#") and trimmed.endswith("#}")


def split_loop_items(values: str) -> list[str]:
    """Split a comma- or newline-separated string into a list of trimmed items."""
    if "," in values:
        return [item.strip() for item in values.split(",")]
    return [line.strip() for line in values.splitlines() if line.strip()]


def eval_conditional(condition: str, variables: Mapping[str, str]) -> bool:
    """Evaluate a template condition as truthy if the named variable is non-empty."""
    if not condition:
        return False
    return bool(variables.get(condition, ""))
