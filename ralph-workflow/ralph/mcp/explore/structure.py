
from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ralph.mcp.explore.store import EdgeRow, SpanRow, SymbolRow

EXTRACTOR_VERSION: Final[str] = "phase2-structure-v1"

_ID_BYTES = 32

CONFIDENCE_EXTRACTED: Final[float] = 1.0
CONFIDENCE_INFERRED: Final[float] = 0.6
CONFIDENCE_AMBIGUOUS: Final[float] = 0.3

_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_SETEXT_UNDERLINE_RE = re.compile(r"^[=-]+\s*$")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\n]+)\)")
_MARKDOWN_AUTOLINK_RE = re.compile(r"<([^>\s]+)>")
_MARKDOWN_REF_LINK_RE = re.compile(r"\[([^\]\n]+)\]\[([^\]\n]+)\]")


@dataclass(frozen=True, slots=True)
class StructureExtraction:

    path: str
    content_hash: str
    spans: tuple[SpanRow, ...]
    symbols: tuple[SymbolRow, ...]
    edges: tuple[EdgeRow, ...]




def _stable_id(*parts: str) -> str:
    payload = "\x00".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def derive_span_id(
    *,
    path: str,
    start_line: int,
    start_col: int,
    end_line: int,
    end_col: int,
    kind: str,
    content_hash: str,
) -> str:
    return _stable_id(
        "span",
        path,
        str(start_line),
        str(start_col),
        str(end_line),
        str(end_col),
        kind,
        content_hash,
        EXTRACTOR_VERSION,
    )


def derive_symbol_id(
    *,
    path: str,
    qualified_name: str,
    kind: str,
    span_id: str,
) -> str:
    return _stable_id("sym", path, qualified_name, kind, span_id)


def derive_edge_id(
    *,
    source_id: str,
    target_id: str,
    relation: str,
    path: str,
    span_id: str | None,
) -> str:
    return _stable_id("edge", source_id, target_id, relation, path, span_id or "")




def detect_language(path: str) -> str | None:
    lowered = path.lower()
    if lowered.endswith(".py"):
        return "python"
    if lowered.endswith(".md") or lowered.endswith(".markdown"):
        return "markdown"
    return None




def _line_col(node: ast.AST) -> tuple[int, int, int, int]:
    start_line = _get_int_attr(node, "lineno", 0)
    start_col = _get_int_attr(node, "col_offset", 0)
    end_line = _get_int_attr(node, "end_lineno", 0)
    end_col = _get_int_attr(node, "end_col_offset", 0)
    return (start_line, start_col, end_line, end_col)


def _get_int_attr(node: ast.AST, attr: str, default: int) -> int:
    raw: object = getattr(node, attr, default)
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    if raw is None:
        return default
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return default
    return default


def _qualify(parent: str, name: str) -> str:
    return f"{parent}.{name}" if parent else name


class PythonExtractionError(Exception):
    pass


def extract_python(
    *,
    path: str,
    content: str,
    content_hash: str,
    generation: int,
    structure_extractor: str = "scalar",
) -> StructureExtraction:
    from ralph.mcp.explore._structure_extractor import (
        select_structure_extractor,
    )

    extractor = select_structure_extractor(structure_extractor)
    return extractor(
        path=path,
        content=content,
        content_hash=content_hash,
        generation=generation,
    )


def extract_python_scalar(
    *,
    path: str,
    content: str,
    content_hash: str,
    generation: int,
) -> StructureExtraction:
    spans: list[SpanRow] = []
    symbols: list[SymbolRow] = []
    edges: list[EdgeRow] = []

    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        raise PythonExtractionError(f"{path!r}: {exc}") from exc

    def _walk(node: ast.AST, parent_qualified: str, container_span_id: str) -> None:
        kind_name = _node_kind(node)
        if kind_name is not None and hasattr(node, "name"):
            name_obj: object = getattr(node, "name", "")
            name = name_obj if isinstance(name_obj, str) else str(name_obj)
            if name:
                start_line, start_col, end_line, end_col = _line_col(node)
                span_id = derive_span_id(
                    path=path,
                    start_line=start_line,
                    start_col=start_col,
                    end_line=end_line,
                    end_col=end_col,
                    kind=kind_name,
                    content_hash=content_hash,
                )
                sym_id = derive_symbol_id(
                    path=path,
                    qualified_name=_qualify(parent_qualified, name),
                    kind=kind_name,
                    span_id=span_id,
                )
                spans.append(
                    SpanRow(
                        span_id=span_id,
                        path=path,
                        start_line=start_line,
                        start_col=start_col,
                        end_line=end_line,
                        end_col=end_col,
                        kind=kind_name,
                        symbol_id=sym_id,
                        content_hash=content_hash,
                        generation=generation,
                    )
                )
                symbols.append(
                    SymbolRow(
                        symbol_id=sym_id,
                        name=name,
                        qualified_name=_qualify(parent_qualified, name),
                        kind=kind_name,
                        path=path,
                        span_id=span_id,
                        language="python",
                        extracted_from="ast",
                        confidence=CONFIDENCE_EXTRACTED,
                        generation=generation,
                    )
                )
                if parent_qualified:
                    edges.append(
                        EdgeRow(
                            edge_id=derive_edge_id(
                                source_id=f"sym:{path}:{parent_qualified}",
                                target_id=sym_id,
                                relation="contains",
                                path=path,
                                span_id=container_span_id,
                            ),
                            source_id=f"sym:{path}:{parent_qualified}",
                            target_id=sym_id,
                            relation="contains",
                            path=path,
                            span_id=container_span_id,
                            provenance="extracted",
                            confidence=CONFIDENCE_EXTRACTED,
                            reason="ast:ClassDef/FunctionDef body",
                            generation=generation,
                        )
                    )
                    defines_source_id = f"sym:{path}:{parent_qualified}"
                    edges.append(
                        EdgeRow(
                            edge_id=derive_edge_id(
                                source_id=defines_source_id,
                                target_id=sym_id,
                                relation="defines",
                                path=path,
                                span_id=span_id,
                            ),
                            source_id=defines_source_id,
                            target_id=sym_id,
                            relation="defines",
                            path=path,
                            span_id=span_id,
                            provenance="extracted",
                            confidence=CONFIDENCE_EXTRACTED,
                            reason=f"ast:{kind_name} inside {parent_qualified}",
                            generation=generation,
                        )
                    )
                if kind_name == "class" and isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        base_id_obj: object = getattr(base, "id", None)
                        if isinstance(base_id_obj, str) and base_id_obj:
                            base_id: str | None = base_id_obj
                        else:
                            base_id = _attr_name(base)
                        if base_id:
                            edges.append(
                                EdgeRow(
                                    edge_id=derive_edge_id(
                                        source_id=sym_id,
                                        target_id=f"unresolved:{base_id}",
                                        relation="inherits_syntax",
                                        path=path,
                                        span_id=span_id,
                                    ),
                                    source_id=sym_id,
                                    target_id=f"unresolved:{base_id}",
                                    relation="inherits_syntax",
                                    path=path,
                                    span_id=span_id,
                                    provenance="extracted",
                                    confidence=CONFIDENCE_EXTRACTED,
                                    reason="ast:ClassDef bases",
                                    generation=generation,
                                )
                            )
                child_qualified = _qualify(parent_qualified, name)
            else:
                child_qualified = parent_qualified
        else:
            child_qualified = parent_qualified
        for child in ast.iter_child_nodes(node):
            _walk(child, child_qualified, container_span_id)

    module_start, module_col, module_end, module_end_col = _line_col(tree)
    file_span_id = derive_span_id(
        path=path,
        start_line=module_start,
        start_col=module_col,
        end_line=max(module_end, module_start),
        end_col=max(module_end_col, module_col),
        kind="module",
        content_hash=content_hash,
    )
    spans.append(
        SpanRow(
            span_id=file_span_id,
            path=path,
            start_line=module_start,
            start_col=module_col,
            end_line=max(module_end, module_start),
            end_col=max(module_end_col, module_col),
            kind="module",
            symbol_id=None,
            content_hash=content_hash,
            generation=generation,
        )
    )
    module_qualified = Path(path).stem
    _walk(tree, parent_qualified=module_qualified, container_span_id=file_span_id)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee: object | None = None
            func_obj: object = node.func
            if isinstance(func_obj, ast.Name):
                callee = func_obj.id
            elif isinstance(func_obj, ast.Attribute):
                attr_id: object = getattr(func_obj, "attr", None)
                callee = attr_id if isinstance(attr_id, str) else None
            if not callee or not isinstance(callee, str):
                continue
            span_start_line, span_start_col, span_end_line, span_end_col = _line_col(node)
            span_id = derive_span_id(
                path=path,
                start_line=span_start_line,
                start_col=span_start_col,
                end_line=span_end_line,
                end_col=span_end_col,
                kind="call",
                content_hash=content_hash,
            )
            edges.append(
                EdgeRow(
                    edge_id=derive_edge_id(
                        source_id=f"file:{path}",
                        target_id=f"unresolved:{callee}",
                        relation="calls_syntax",
                        path=path,
                        span_id=span_id,
                    ),
                    source_id=f"file:{path}",
                    target_id=f"unresolved:{callee}",
                    relation="calls_syntax",
                    path=path,
                    span_id=span_id,
                    provenance="extracted",
                    confidence=CONFIDENCE_INFERRED,
                    reason="ast:Call",
                    generation=generation,
                )
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                span_start_line, span_start_col, span_end_line, span_end_col = _line_col(node)
                span_id = derive_span_id(
                    path=path,
                    start_line=span_start_line,
                    start_col=span_start_col,
                    end_line=span_end_line,
                    end_col=span_end_col,
                    kind="import",
                    content_hash=content_hash,
                )
                edges.append(
                    EdgeRow(
                        edge_id=derive_edge_id(
                            source_id=f"file:{path}",
                            target_id=f"unresolved:{target}",
                            relation="imports",
                            path=path,
                            span_id=span_id,
                        ),
                        source_id=f"file:{path}",
                        target_id=f"unresolved:{target}",
                        relation="imports",
                        path=path,
                        span_id=span_id,
                        provenance="extracted",
                        confidence=CONFIDENCE_EXTRACTED,
                        reason="ast:Import",
                        generation=generation,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            for alias in node.names:
                target = f"{module_name}.{alias.name}" if module_name else alias.name
                span_start_line, span_start_col, span_end_line, span_end_col = _line_col(node)
                span_id = derive_span_id(
                    path=path,
                    start_line=span_start_line,
                    start_col=span_start_col,
                    end_line=span_end_line,
                    end_col=span_end_col,
                    kind="import",
                    content_hash=content_hash,
                )
                edges.append(
                    EdgeRow(
                        edge_id=derive_edge_id(
                            source_id=f"file:{path}",
                            target_id=f"unresolved:{target}",
                            relation="imports",
                            path=path,
                            span_id=span_id,
                        ),
                        source_id=f"file:{path}",
                        target_id=f"unresolved:{target}",
                        relation="imports",
                        path=path,
                        span_id=span_id,
                        provenance="extracted",
                        confidence=CONFIDENCE_EXTRACTED,
                        reason="ast:ImportFrom",
                        generation=generation,
                    )
                )

    defined_names: set[str] = {
        symbol_row.name
        for symbol_row in symbols
        if isinstance(symbol_row.name, str) and symbol_row.name
    }
    if defined_names:
        for body_node in ast.walk(tree):
            if not isinstance(body_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            body_start, _, body_end, _ = _line_col(body_node)
            for sub in ast.walk(body_node):
                if isinstance(sub, ast.Name) and isinstance(sub.id, str):
                    name = sub.id
                    if name in defined_names:
                        body_node_name: str = ""
                        if isinstance(
                            body_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                        ):
                            raw_name: object = getattr(body_node, "name", "")
                            body_node_name = raw_name if isinstance(raw_name, str) else ""
                        if body_node_name == name:
                            continue
                        ref_line, _, ref_end, ref_end_col = _line_col(sub)
                        if ref_line < body_start or ref_line > body_end:
                            continue
                        raw_col: object = getattr(sub, "col_offset", 0)
                        ref_col_offset = (
                            int(raw_col)
                            if isinstance(raw_col, int) and not isinstance(raw_col, bool)
                            else 0
                        )
                        ref_span_id = derive_span_id(
                            path=path,
                            start_line=ref_line,
                            start_col=ref_col_offset,
                            end_line=ref_end,
                            end_col=ref_end_col,
                            kind="reference",
                            content_hash=content_hash,
                        )
                        for def_sym in symbols:
                            if def_sym.name == name:
                                edges.append(
                                    EdgeRow(
                                        edge_id=derive_edge_id(
                                            source_id=f"sym:{path}:{def_sym.qualified_name}",
                                            target_id=f"sym:{path}:{body_node_name}",
                                            relation="references_text",
                                            path=path,
                                            span_id=ref_span_id,
                                        ),
                                        source_id=f"sym:{path}:{def_sym.qualified_name}",
                                        target_id=f"sym:{path}:{body_node_name}",
                                        relation="references_text",
                                        path=path,
                                        span_id=ref_span_id,
                                        provenance="inferred",
                                        confidence=CONFIDENCE_INFERRED,
                                        reason="text_match:identifier",
                                        generation=generation,
                                    )
                                )
                                break

    for sym_row in symbols:
        if sym_row.kind == "function" and sym_row.name.startswith("test_"):
            test_span_id = sym_row.span_id
            edges.append(
                EdgeRow(
                    edge_id=derive_edge_id(
                        source_id=sym_row.symbol_id,
                        target_id=f"file:{path}",
                        relation="tests",
                        path=path,
                        span_id=test_span_id,
                    ),
                    source_id=sym_row.symbol_id,
                    target_id=f"file:{path}",
                    relation="tests",
                    path=path,
                    span_id=test_span_id,
                    provenance="extracted",
                    confidence=CONFIDENCE_INFERRED,
                    reason="ast:function:name=test_*",
                    generation=generation,
                )
            )

    if defined_names:
        lines_list: list[str] = content.splitlines()
        for line_index, raw_line in enumerate(lines_list, start=1):
            stripped = raw_line.lstrip()
            if not stripped.startswith("#"):
                continue
            comment_text = stripped.lstrip("#").strip()
            tokens_raw: list[str] = _IDENT_RE.findall(comment_text)
            tokens: list[str] = [t for t in tokens_raw if isinstance(t, str)]
            if not tokens:
                continue
            for token in tokens:
                if token not in defined_names:
                    continue
                mention_span_id = derive_span_id(
                    path=path,
                    start_line=line_index,
                    start_col=raw_line.index("#"),
                    end_line=line_index,
                    end_col=len(raw_line),
                    kind="comment",
                    content_hash=content_hash,
                )
                edges.append(
                    EdgeRow(
                        edge_id=derive_edge_id(
                            source_id=f"comment:{path}:{line_index}",
                            target_id=f"unresolved:{token}",
                            relation="mentions",
                            path=path,
                            span_id=mention_span_id,
                        ),
                        source_id=f"comment:{path}:{line_index}",
                        target_id=f"unresolved:{token}",
                        relation="mentions",
                        path=path,
                        span_id=mention_span_id,
                        provenance="inferred",
                        confidence=CONFIDENCE_AMBIGUOUS,
                        reason="text_match:comment",
                        generation=generation,
                    )
                )

    return StructureExtraction(
        path=path,
        content_hash=content_hash,
        spans=tuple(spans),
        symbols=tuple(symbols),
        edges=tuple(edges),
    )


def extract_python_accelerated(
    *,
    path: str,
    content: str,
    content_hash: str,
    generation: int,
) -> StructureExtraction:
    spans: list[SpanRow] = []
    symbols: list[SymbolRow] = []
    edges: list[EdgeRow] = []

    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        raise PythonExtractionError(f"{path!r}: {exc}") from exc

    module_start, module_col, module_end, module_end_col = _line_col(tree)
    file_span_id = derive_span_id(
        path=path,
        start_line=module_start,
        start_col=module_col,
        end_line=max(module_end, module_start),
        end_col=max(module_end_col, module_col),
        kind="module",
        content_hash=content_hash,
    )
    spans.append(
        SpanRow(
            span_id=file_span_id,
            path=path,
            start_line=module_start,
            start_col=module_col,
            end_line=max(module_end, module_start),
            end_col=max(module_end_col, module_col),
            kind="module",
            symbol_id=None,
            content_hash=content_hash,
            generation=generation,
        )
    )

    defined_names: set[str] = set()
    name_to_qualified: dict[str, list[str]] = {}

    module_qualified = Path(path).stem
    body_stack: list[tuple[int, int, str, str]] = []

    def _walk(node: ast.AST, parent_qualified: str, container_span_id: str) -> None:
        kind_name = _node_kind(node)
        node_name: str = ""
        sym_id: str = ""
        span_id: str = ""
        child_qualified = parent_qualified
        active_body: tuple[int, int, str, str] | None = None

        if kind_name is not None and hasattr(node, "name"):
            name_obj: object = getattr(node, "name", "")
            node_name = name_obj if isinstance(name_obj, str) else str(name_obj)
            if node_name:
                start_line, start_col, end_line, end_col = _line_col(node)
                span_id = derive_span_id(
                    path=path,
                    start_line=start_line,
                    start_col=start_col,
                    end_line=end_line,
                    end_col=end_col,
                    kind=kind_name,
                    content_hash=content_hash,
                )
                child_qualified = _qualify(parent_qualified, node_name)
                sym_id = derive_symbol_id(
                    path=path,
                    qualified_name=child_qualified,
                    kind=kind_name,
                    span_id=span_id,
                )
                spans.append(
                    SpanRow(
                        span_id=span_id,
                        path=path,
                        start_line=start_line,
                        start_col=start_col,
                        end_line=end_line,
                        end_col=end_col,
                        kind=kind_name,
                        symbol_id=sym_id,
                        content_hash=content_hash,
                        generation=generation,
                    )
                )
                symbols.append(
                    SymbolRow(
                        symbol_id=sym_id,
                        name=node_name,
                        qualified_name=child_qualified,
                        kind=kind_name,
                        path=path,
                        span_id=span_id,
                        language="python",
                        extracted_from="ast",
                        confidence=CONFIDENCE_EXTRACTED,
                        generation=generation,
                    )
                )
                defined_names.add(node_name)
                name_to_qualified.setdefault(node_name, []).append(child_qualified)
                if parent_qualified:
                    edges.append(
                        EdgeRow(
                            edge_id=derive_edge_id(
                                source_id=f"sym:{path}:{parent_qualified}",
                                target_id=sym_id,
                                relation="contains",
                                path=path,
                                span_id=container_span_id,
                            ),
                            source_id=f"sym:{path}:{parent_qualified}",
                            target_id=sym_id,
                            relation="contains",
                            path=path,
                            span_id=container_span_id,
                            provenance="extracted",
                            confidence=CONFIDENCE_EXTRACTED,
                            reason="ast:ClassDef/FunctionDef body",
                            generation=generation,
                        )
                    )
                    edges.append(
                        EdgeRow(
                            edge_id=derive_edge_id(
                                source_id=f"sym:{path}:{parent_qualified}",
                                target_id=sym_id,
                                relation="defines",
                                path=path,
                                span_id=span_id,
                            ),
                            source_id=f"sym:{path}:{parent_qualified}",
                            target_id=sym_id,
                            relation="defines",
                            path=path,
                            span_id=span_id,
                            provenance="extracted",
                            confidence=CONFIDENCE_EXTRACTED,
                            reason=f"ast:{kind_name} inside {parent_qualified}",
                            generation=generation,
                        )
                    )
                if kind_name == "class" and isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        base_id_obj: object = getattr(base, "id", None)
                        if isinstance(base_id_obj, str) and base_id_obj:
                            base_id: str | None = base_id_obj
                        else:
                            base_id = _attr_name(base)
                        if base_id:
                            edges.append(
                                EdgeRow(
                                    edge_id=derive_edge_id(
                                        source_id=sym_id,
                                        target_id=f"unresolved:{base_id}",
                                        relation="inherits_syntax",
                                        path=path,
                                        span_id=span_id,
                                    ),
                                    source_id=sym_id,
                                    target_id=f"unresolved:{base_id}",
                                    relation="inherits_syntax",
                                    path=path,
                                    span_id=span_id,
                                    provenance="extracted",
                                    confidence=CONFIDENCE_EXTRACTED,
                                    reason="ast:ClassDef bases",
                                    generation=generation,
                                )
                            )
                if kind_name == "function" and node_name.startswith("test_"):
                    edges.append(
                        EdgeRow(
                            edge_id=derive_edge_id(
                                source_id=sym_id,
                                target_id=f"file:{path}",
                                relation="tests",
                                path=path,
                                span_id=span_id,
                            ),
                            source_id=sym_id,
                            target_id=f"file:{path}",
                            relation="tests",
                            path=path,
                            span_id=span_id,
                            provenance="extracted",
                            confidence=CONFIDENCE_INFERRED,
                            reason="ast:function:name=test_*",
                            generation=generation,
                        )
                    )
                body_start, _, body_end, _ = _line_col(node)
                active_body = (body_start, body_end, child_qualified, node_name)
                body_stack.append(active_body)

        if isinstance(node, ast.Call):
            callee: object | None = None
            func_obj: object = node.func
            if isinstance(func_obj, ast.Name):
                callee = func_obj.id
            elif isinstance(func_obj, ast.Attribute):
                attr_id: object = getattr(func_obj, "attr", None)
                callee = attr_id if isinstance(attr_id, str) else None
            if isinstance(callee, str) and callee:
                span_start_line, span_start_col, span_end_line, span_end_col = _line_col(node)
                call_span_id = derive_span_id(
                    path=path,
                    start_line=span_start_line,
                    start_col=span_start_col,
                    end_line=span_end_line,
                    end_col=span_end_col,
                    kind="call",
                    content_hash=content_hash,
                )
                edges.append(
                    EdgeRow(
                        edge_id=derive_edge_id(
                            source_id=f"file:{path}",
                            target_id=f"unresolved:{callee}",
                            relation="calls_syntax",
                            path=path,
                            span_id=call_span_id,
                        ),
                        source_id=f"file:{path}",
                        target_id=f"unresolved:{callee}",
                        relation="calls_syntax",
                        path=path,
                        span_id=call_span_id,
                        provenance="extracted",
                        confidence=CONFIDENCE_INFERRED,
                        reason="ast:Call",
                        generation=generation,
                    )
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                span_start_line, span_start_col, span_end_line, span_end_col = _line_col(node)
                imp_span_id = derive_span_id(
                    path=path,
                    start_line=span_start_line,
                    start_col=span_start_col,
                    end_line=span_end_line,
                    end_col=span_end_col,
                    kind="import",
                    content_hash=content_hash,
                )
                edges.append(
                    EdgeRow(
                        edge_id=derive_edge_id(
                            source_id=f"file:{path}",
                            target_id=f"unresolved:{target}",
                            relation="imports",
                            path=path,
                            span_id=imp_span_id,
                        ),
                        source_id=f"file:{path}",
                        target_id=f"unresolved:{target}",
                        relation="imports",
                        path=path,
                        span_id=imp_span_id,
                        provenance="extracted",
                        confidence=CONFIDENCE_EXTRACTED,
                        reason="ast:Import",
                        generation=generation,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            for alias in node.names:
                target = f"{module_name}.{alias.name}" if module_name else alias.name
                span_start_line, span_start_col, span_end_line, span_end_col = _line_col(node)
                imp_span_id = derive_span_id(
                    path=path,
                    start_line=span_start_line,
                    start_col=span_start_col,
                    end_line=span_end_line,
                    end_col=span_end_col,
                    kind="import",
                    content_hash=content_hash,
                )
                edges.append(
                    EdgeRow(
                        edge_id=derive_edge_id(
                            source_id=f"file:{path}",
                            target_id=f"unresolved:{target}",
                            relation="imports",
                            path=path,
                            span_id=imp_span_id,
                        ),
                        source_id=f"file:{path}",
                        target_id=f"unresolved:{target}",
                        relation="imports",
                        path=path,
                        span_id=imp_span_id,
                        provenance="extracted",
                        confidence=CONFIDENCE_EXTRACTED,
                        reason="ast:ImportFrom",
                        generation=generation,
                    )
                )
        elif isinstance(node, ast.Name):
            raw_id: object = node.id
            ref_name: str = raw_id if isinstance(raw_id, str) else ""
            if ref_name and ref_name in defined_names and body_stack:
                ref_line, _, ref_end, ref_end_col = _line_col(node)
                target_name: str | None = None
                target_span_id: str | None = None
                for frame in reversed(body_stack):
                    bs, be, _frame_qualified, frame_name = frame
                    if frame_name == ref_name:
                        continue
                    if bs <= ref_line <= be:
                        target_name = frame_name
                        raw_col: object = getattr(node, "col_offset", 0)
                        ref_col_offset = (
                            int(raw_col)
                            if isinstance(raw_col, int) and not isinstance(raw_col, bool)
                            else 0
                        )
                        target_span_id = derive_span_id(
                            path=path,
                            start_line=ref_line,
                            start_col=ref_col_offset,
                            end_line=ref_end,
                            end_col=ref_end_col,
                            kind="reference",
                            content_hash=content_hash,
                        )
                        break
                if target_name is not None and target_span_id is not None:
                    for def_sym in symbols:
                        if def_sym.name == ref_name:
                            edges.append(
                                EdgeRow(
                                    edge_id=derive_edge_id(
                                        source_id=f"sym:{path}:{def_sym.qualified_name}",
                                        target_id=f"sym:{path}:{target_name}",
                                        relation="references_text",
                                        path=path,
                                        span_id=target_span_id,
                                    ),
                                    source_id=f"sym:{path}:{def_sym.qualified_name}",
                                    target_id=f"sym:{path}:{target_name}",
                                    relation="references_text",
                                    path=path,
                                    span_id=target_span_id,
                                    provenance="inferred",
                                    confidence=CONFIDENCE_INFERRED,
                                    reason="text_match:identifier",
                                    generation=generation,
                                )
                            )
                            break

        for child in ast.iter_child_nodes(node):
            _walk(child, child_qualified, container_span_id)

        if active_body is not None:
            body_stack.pop()

    _walk(tree, parent_qualified=module_qualified, container_span_id=file_span_id)

    if defined_names:
        lines_list: list[str] = content.splitlines()
        for line_index, raw_line in enumerate(lines_list, start=1):
            stripped = raw_line.lstrip()
            if not stripped.startswith("#"):
                continue
            comment_text = stripped.lstrip("#").strip()
            tokens_raw: list[str] = _IDENT_RE.findall(comment_text)
            tokens: list[str] = [t for t in tokens_raw if isinstance(t, str)]
            if not tokens:
                continue
            for token in tokens:
                if token not in defined_names:
                    continue
                mention_span_id = derive_span_id(
                    path=path,
                    start_line=line_index,
                    start_col=raw_line.index("#"),
                    end_line=line_index,
                    end_col=len(raw_line),
                    kind="comment",
                    content_hash=content_hash,
                )
                edges.append(
                    EdgeRow(
                        edge_id=derive_edge_id(
                            source_id=f"comment:{path}:{line_index}",
                            target_id=f"unresolved:{token}",
                            relation="mentions",
                            path=path,
                            span_id=mention_span_id,
                        ),
                        source_id=f"comment:{path}:{line_index}",
                        target_id=f"unresolved:{token}",
                        relation="mentions",
                        path=path,
                        span_id=mention_span_id,
                        provenance="inferred",
                        confidence=CONFIDENCE_AMBIGUOUS,
                        reason="text_match:comment",
                        generation=generation,
                    )
                )

    return StructureExtraction(
        path=path,
        content_hash=content_hash,
        spans=tuple(spans),
        symbols=tuple(symbols),
        edges=tuple(edges),
    )


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _node_kind(node: ast.AST) -> str | None:
    if isinstance(node, ast.ClassDef):
        return "class"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "function"
    return None


def _attr_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    return None




_HEADING_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    lowered = text.strip().lower()
    slug = _HEADING_SLUG_RE.sub("-", lowered).strip("-")
    return slug


def extract_markdown(
    *,
    path: str,
    content: str,
    content_hash: str,
    generation: int,
) -> StructureExtraction:
    lines = content.splitlines()
    spans: list[SpanRow] = []
    symbols: list[SymbolRow] = []
    edges: list[EdgeRow] = []

    file_span_id = derive_span_id(
        path=path,
        start_line=1,
        start_col=0,
        end_line=max(len(lines), 1),
        end_col=0,
        kind="document",
        content_hash=content_hash,
    )
    spans.append(
        SpanRow(
            span_id=file_span_id,
            path=path,
            start_line=1,
            start_col=0,
            end_line=max(len(lines), 1),
            end_col=0,
            kind="document",
            symbol_id=None,
            content_hash=content_hash,
            generation=generation,
        )
    )

    line_no = 0
    while line_no < len(lines):
        line = lines[line_no]
        match = _ATX_HEADING_RE.match(line)
        if match:
            level_obj: object = match.group(1)
            title_obj: object = match.group(2)
            level = len(str(level_obj)) if level_obj is not None else 0
            title = str(title_obj).strip() if title_obj is not None else ""
            start_line = line_no + 1
            start_col = 0
            end_line = start_line
            end_col = len(line)
            kind = f"h{level}"
            span_id = derive_span_id(
                path=path,
                start_line=start_line,
                start_col=start_col,
                end_line=end_line,
                end_col=end_col,
                kind=kind,
                content_hash=content_hash,
            )
            sym_id = derive_symbol_id(
                path=path,
                qualified_name=_slugify(title),
                kind=kind,
                span_id=span_id,
            )
            spans.append(
                SpanRow(
                    span_id=span_id,
                    path=path,
                    start_line=start_line,
                    start_col=start_col,
                    end_line=end_line,
                    end_col=end_col,
                    kind=kind,
                    symbol_id=sym_id,
                    content_hash=content_hash,
                    generation=generation,
                )
            )
            symbols.append(
                SymbolRow(
                    symbol_id=sym_id,
                    name=title,
                    qualified_name=_slugify(title),
                    kind=kind,
                    path=path,
                    span_id=span_id,
                    language="markdown",
                    extracted_from="md_heading",
                    confidence=CONFIDENCE_EXTRACTED,
                    generation=generation,
                )
            )
            edges.append(
                EdgeRow(
                    edge_id=derive_edge_id(
                        source_id=f"file:{path}",
                        target_id=sym_id,
                        relation="contains",
                        path=path,
                        span_id=file_span_id,
                    ),
                    source_id=f"file:{path}",
                    target_id=sym_id,
                    relation="contains",
                    path=path,
                    span_id=file_span_id,
                    provenance="extracted",
                    confidence=CONFIDENCE_EXTRACTED,
                    reason="md:heading under document",
                    generation=generation,
                )
            )
            edges.append(
                EdgeRow(
                    edge_id=derive_edge_id(
                        source_id=f"file:{path}",
                        target_id=sym_id,
                        relation="defines",
                        path=path,
                        span_id=span_id,
                    ),
                    source_id=f"file:{path}",
                    target_id=sym_id,
                    relation="defines",
                    path=path,
                    span_id=span_id,
                    provenance="extracted",
                    confidence=CONFIDENCE_EXTRACTED,
                    reason=f"md:heading {kind}={title!r}",
                    generation=generation,
                )
            )
            line_no += 1
            continue
        if (
            line_no + 1 < len(lines)
            and line.strip()
            and _SETEXT_UNDERLINE_RE.match(lines[line_no + 1])
        ):
            underline = lines[line_no + 1].strip()
            level = 1 if underline.startswith("=") else 2
            title = line.strip()
            start_line = line_no + 1
            start_col = 0
            end_line = line_no + 2
            end_col = len(lines[line_no + 1])
            kind = f"h{level}"
            span_id = derive_span_id(
                path=path,
                start_line=start_line,
                start_col=start_col,
                end_line=end_line,
                end_col=end_col,
                kind=kind,
                content_hash=content_hash,
            )
            sym_id = derive_symbol_id(
                path=path,
                qualified_name=_slugify(title),
                kind=kind,
                span_id=span_id,
            )
            spans.append(
                SpanRow(
                    span_id=span_id,
                    path=path,
                    start_line=start_line,
                    start_col=start_col,
                    end_line=end_line,
                    end_col=end_col,
                    kind=kind,
                    symbol_id=sym_id,
                    content_hash=content_hash,
                    generation=generation,
                )
            )
            symbols.append(
                SymbolRow(
                    symbol_id=sym_id,
                    name=title,
                    qualified_name=_slugify(title),
                    kind=kind,
                    path=path,
                    span_id=span_id,
                    language="markdown",
                    extracted_from="md_setext",
                    confidence=CONFIDENCE_EXTRACTED,
                    generation=generation,
                )
            )
            edges.append(
                EdgeRow(
                    edge_id=derive_edge_id(
                        source_id=f"file:{path}",
                        target_id=sym_id,
                        relation="contains",
                        path=path,
                        span_id=file_span_id,
                    ),
                    source_id=f"file:{path}",
                    target_id=sym_id,
                    relation="contains",
                    path=path,
                    span_id=file_span_id,
                    provenance="extracted",
                    confidence=CONFIDENCE_EXTRACTED,
                    reason="md:setext heading under document",
                    generation=generation,
                )
            )
            edges.append(
                EdgeRow(
                    edge_id=derive_edge_id(
                        source_id=f"file:{path}",
                        target_id=sym_id,
                        relation="defines",
                        path=path,
                        span_id=span_id,
                    ),
                    source_id=f"file:{path}",
                    target_id=sym_id,
                    relation="defines",
                    path=path,
                    span_id=span_id,
                    provenance="extracted",
                    confidence=CONFIDENCE_EXTRACTED,
                    reason=f"md:setext heading {kind}={title!r}",
                    generation=generation,
                )
            )
            line_no += 2
            continue
        for link_match in _MARKDOWN_LINK_RE.finditer(line):
            link_text_raw: object = link_match.group(1)
            link_target_raw: object = link_match.group(2)
            link_text = str(link_text_raw).strip() if link_text_raw is not None else ""
            link_target = str(link_target_raw).strip() if link_target_raw is not None else ""
            if not link_text or not link_target:
                continue
            link_start_col = int(link_match.start())
            link_end_col = int(link_match.end())
            link_span_id = derive_span_id(
                path=path,
                start_line=line_no + 1,
                start_col=link_start_col,
                end_line=line_no + 1,
                end_col=link_end_col,
                kind="md_link",
                content_hash=content_hash,
            )
            link_symbol_id = derive_symbol_id(
                path=path,
                qualified_name=f"md_link:{line_no + 1}:{link_start_col}",
                kind="md_link",
                span_id=link_span_id,
            )
            spans.append(
                SpanRow(
                    span_id=link_span_id,
                    path=path,
                    start_line=line_no + 1,
                    start_col=link_start_col,
                    end_line=line_no + 1,
                    end_col=link_end_col,
                    kind="md_link",
                    symbol_id=link_symbol_id,
                    content_hash=content_hash,
                    generation=generation,
                )
            )
            symbols.append(
                SymbolRow(
                    symbol_id=link_symbol_id,
                    name=link_text,
                    qualified_name=f"md_link:{line_no + 1}:{link_start_col}",
                    kind="md_link",
                    path=path,
                    span_id=link_span_id,
                    language="markdown",
                    extracted_from="md_link",
                    confidence=CONFIDENCE_EXTRACTED,
                    generation=generation,
                )
            )
            edges.append(
                EdgeRow(
                    edge_id=derive_edge_id(
                        source_id=file_span_id,
                        target_id=link_symbol_id,
                        relation="mentions",
                        path=path,
                        span_id=link_span_id,
                    ),
                    source_id=file_span_id,
                    target_id=link_symbol_id,
                    relation="mentions",
                    path=path,
                    span_id=link_span_id,
                    provenance="extracted",
                    confidence=CONFIDENCE_EXTRACTED,
                    reason=f"md:link text={link_text!r} target={link_target!r}",
                    generation=generation,
                )
            )
        line_no += 1

    return StructureExtraction(
        path=path,
        content_hash=content_hash,
        spans=tuple(spans),
        symbols=tuple(symbols),
        edges=tuple(edges),
    )




def extract_structure(
    *,
    path: str,
    content: str,
    content_hash: str,
    generation: int,
    structure_extractor: str = "scalar",
) -> StructureExtraction:
    language = detect_language(path)
    if language == "python":
        return extract_python(
            path=path,
            content=content,
            content_hash=content_hash,
            generation=generation,
            structure_extractor=structure_extractor,
        )
    if language == "markdown":
        return extract_markdown(
            path=path,
            content=content,
            content_hash=content_hash,
            generation=generation,
        )
    return StructureExtraction(
        path=path,
        content_hash=content_hash,
        spans=(),
        symbols=(),
        edges=(),
    )


__all__ = [
    "CONFIDENCE_AMBIGUOUS",
    "CONFIDENCE_EXTRACTED",
    "CONFIDENCE_INFERRED",
    "EXTRACTOR_VERSION",
    "PythonExtractionError",
    "StructureExtraction",
    "derive_edge_id",
    "derive_span_id",
    "derive_symbol_id",
    "detect_language",
    "extract_markdown",
    "extract_python",
    "extract_python_accelerated",
    "extract_python_scalar",
    "extract_structure",
]


_ = Path
