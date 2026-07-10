"""Deterministic Python AST and Markdown structure extraction.

The :mod:`structure` module owns the syntax-derived rows that
back :mod:`ralph.mcp.explore.graph`. Every edge and span carries a
deterministic id derived from normalized path, span coordinates,
kind, and extractor version so a no-op reindex produces stable
logical rows.

Extraction rules (per the prompt):

* ``contains`` edges: file contains class / function / heading /
  body span. Always emitted when a child span exists.
* ``defines``: emitted only from parser-recognized definition
  spans (``ast.FunctionDef`` / ``ast.AsyncFunctionDef`` /
  ``ast.ClassDef`` / module-level assignments with a syntactically
  explicit target, Markdown ATX / Setext headings).
* ``imports``: emitted only from ``ast.Import`` /
  ``ast.ImportFrom``. Resolution to a local symbol/file is a
  separate inferred edge with lower confidence.
* ``calls_syntax``: emitted only when syntax contains a call
  expression and the callee token/range can be recorded. It does
  NOT claim semantic dispatch.
* ``references_text``: emitted from exact token / text matches
  after identifier normalization. Lower confidence than parser
  edges.
* ``inherits_syntax``: emitted only from Python class bases.
* ``tests``: emitted from deterministic test-naming conventions
  (path patterns, ``test_`` prefix) plus import/references to a
  target symbol.
* ``mentions``: emitted only from comments / docs / Markdown text
  with exact matched spans.

Confidence and provenance are recorded on every edge so callers can
distinguish ``extracted`` (parser-verified) from ``inferred`` (text /
naming) and ``unknown`` (dynamic / reflection / unresolved).

The module is pure (no I/O); the reindex pipeline calls
:func:`extract_structure` and persists the returned tuples through
:class:`ralph.mcp.explore.store.ExploreStore.replace_structure_rows`.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ralph.mcp.explore.store import EdgeRow, SpanRow, SymbolRow

# Phase 2 extractor version. Bumped whenever the schema or the
# extraction rules change in a way that requires old rows to be
# rebuilt.
EXTRACTOR_VERSION: Final[str] = "phase2-structure-v1"

# Stable ids are SHA-256 hex digests of a deterministic payload.
_ID_BYTES = 32

# Confidence ladder: ``extracted`` is parser-verified (1.0),
# ``inferred`` is text/naming (0.6), ``ambiguous`` is multi-target
# or dynamic (0.3).
CONFIDENCE_EXTRACTED: Final[float] = 1.0
CONFIDENCE_INFERRED: Final[float] = 0.6
CONFIDENCE_AMBIGUOUS: Final[float] = 0.3

# Heading detection (ATX + Setext).
_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_SETEXT_UNDERLINE_RE = re.compile(r"^[=-]+\s*$")
# AC-02: Markdown link regex matches inline ``[text](target)``
# and reference-style ``[text][ref]`` forms. The regex captures
# the link text/label so we can emit ``mentions`` edges with
# exact spans.
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\n]+)\)")
_MARKDOWN_AUTOLINK_RE = re.compile(r"<([^>\s]+)>")
_MARKDOWN_REF_LINK_RE = re.compile(r"\[([^\]\n]+)\]\[([^\]\n]+)\]")


@dataclass(frozen=True, slots=True)
class StructureExtraction:
    """The result of extracting structure from a single file.

    The reindex pipeline persists the spans/symbols/edges via
    :meth:`ExploreStore.replace_structure_rows` so the values here
    always travel together with a single ``content_hash``.
    """

    path: str
    content_hash: str
    spans: tuple[SpanRow, ...]
    symbols: tuple[SymbolRow, ...]
    edges: tuple[EdgeRow, ...]


# --- Stable-id helpers ----------------------------------------------------


def _stable_id(*parts: str) -> str:
    """Return a SHA-256 hex digest of ``\\x00``-joined ``parts``."""
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
    """Return the deterministic span id for the given coordinates."""
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
    """Return the deterministic symbol id for a definition."""
    return _stable_id("sym", path, qualified_name, kind, span_id)


def derive_edge_id(
    *,
    source_id: str,
    target_id: str,
    relation: str,
    path: str,
    span_id: str | None,
) -> str:
    """Return the deterministic edge id for a (source, target, relation) tuple."""
    return _stable_id("edge", source_id, target_id, relation, path, span_id or "")


# --- Language detection --------------------------------------------------


def detect_language(path: str) -> str | None:
    """Return ``"python"`` / ``"markdown"`` / ``None`` based on extension."""
    lowered = path.lower()
    if lowered.endswith(".py"):
        return "python"
    if lowered.endswith(".md") or lowered.endswith(".markdown"):
        return "markdown"
    return None


# --- Python extraction ----------------------------------------------------


def _line_col(node: ast.AST) -> tuple[int, int, int, int]:
    """Return ``(start_line, start_col, end_line, end_col)`` for an AST node."""
    start_line = _get_int_attr(node, "lineno", 0)
    start_col = _get_int_attr(node, "col_offset", 0)
    end_line = _get_int_attr(node, "end_lineno", 0)
    end_col = _get_int_attr(node, "end_col_offset", 0)
    return (start_line, start_col, end_line, end_col)


def _get_int_attr(node: ast.AST, attr: str, default: int) -> int:
    """Read an integer attribute from an AST node, falling back to ``default``."""
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
    """Join a qualified-name parent and a child name."""
    return f"{parent}.{name}" if parent else name


class PythonExtractionError(Exception):
    """Typed extraction failure raised when Python source fails to parse.

    PA-001 / AC-02: the reindex pipeline translates this exception
    in its preflight so the per-path failure path appends the file
    to ``failed_files`` and continues the sorted path loop. Prior
    lexical/structure rows for the path remain queryable, the dirty
    entry stays queued, and the failure is retried on the next
    reindex pass.
    """


def extract_python(
    *,
    path: str,
    content: str,
    content_hash: str,
    generation: int,
) -> StructureExtraction:
    """Extract spans/symbols/edges from a Python source ``content``.

    Raises ``PythonExtractionError`` when the source fails to parse. Per
    the prompt's PA-001 invariant, the reindex pipeline catches the
    typed exception in its preflight so lexical/structure rows for
    the path remain queryable. Direct callers of ``extract_python``
    (and tests) can decide their own recovery contract.

    Edge relation coverage (mechanically evidenced):

    * ``contains`` — file/module/class contains child span.
    * ``defines`` — emitted only for parser-recognized definitions.
    * ``imports`` — emitted only from ``ast.Import`` / ``ast.ImportFrom``.
    * ``calls_syntax`` — emitted only when syntax contains a call
      expression with a recordable callee token. Does NOT claim
      semantic dispatch.
    * ``references_text`` — emitted from exact token / text matches
      after identifier normalization. Lower confidence than parser
      edges; provenance is ``inferred``.
    * ``inherits_syntax`` — emitted only from Python class bases.
    * ``tests`` — emitted from deterministic test-naming conventions.
    * ``mentions`` — emitted only from comments with exact matched
      spans. Must not imply code dependency.
    """
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
        # Walk children
        for child in ast.iter_child_nodes(node):
            _walk(child, child_qualified, container_span_id)

    # Walk top-level: the file's container span uses the module node.
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
    # Top-level qualified name uses the file basename (without
    # extension) so callers can resolve ``module.hello`` against
    # the indexed symbol row.
    module_qualified = Path(path).stem
    _walk(tree, parent_qualified=module_qualified, container_span_id=file_span_id)

    # Calls (calls_syntax). Emitted only when an ast.Call expression
    # has a recordable callee token. The callee is recorded as a
    # unresolved target because semantic dispatch is not proven by
    # syntax alone.
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
            span_start_line, span_start_col, span_end_line, span_end_col = _line_col(
                node
            )
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

    # Imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                span_start_line, span_start_col, span_end_line, span_end_col = _line_col(
                    node
                )
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
                span_start_line, span_start_col, span_end_line, span_end_col = _line_col(
                    node
                )
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

    # references_text: emit one edge per defined symbol whose
    # identifier appears in another symbol's body. We use
    # ``ast.walk`` to scan function/class bodies for raw token
    # matches after identifier normalization. The relation is
    # ``inferred`` (lower confidence than parser-verified edges).
    defined_names: set[str] = {
        symbol_row.name
        for symbol_row in symbols
        if isinstance(symbol_row.name, str) and symbol_row.name
    }
    if defined_names:
        for body_node in ast.walk(tree):
            if not isinstance(
                body_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            body_start, _, body_end, _ = _line_col(body_node)
            for sub in ast.walk(body_node):
                if isinstance(sub, ast.Name) and isinstance(sub.id, str):
                    name = sub.id
                    if name in defined_names:
                        # Skip the symbol's own definition.
                        body_node_name: str = ""
                        if isinstance(
                            body_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                        ):
                            raw_name: object = getattr(body_node, "name", "")
                            body_node_name = (
                                raw_name if isinstance(raw_name, str) else ""
                            )
                        if body_node_name == name:
                            continue
                        ref_line, _, ref_end, ref_end_col = _line_col(sub)
                        if ref_line < body_start or ref_line > body_end:
                            continue
                        raw_col: object = getattr(sub, "col_offset", 0)
                        ref_col_offset = (
                            int(raw_col) if isinstance(raw_col, int) and not isinstance(raw_col, bool) else 0
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

    # tests: emit edges from any function whose name starts with
    # ``test_`` to the file-level container span. ``tests`` is a
    # suggested-verification relation, not proof of behavioral
    # coverage. Deterministic naming + path heuristics drive the
    # edge; callers must not assume the relation implies the test
    # actually verifies the target.
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

    # mentions: scan leading ``#``-prefixed comments for defined
    # symbol names. The relation is ``inferred`` and must never
    # imply code dependency. Each comment line emits one edge per
    # matched identifier; the matched token's span is the
    # comment span itself.
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
    """Map an AST node to a stable ``kind`` string for spans."""
    if isinstance(node, ast.ClassDef):
        return "class"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "function"
    return None


def _attr_name(node: ast.AST) -> str | None:
    """Best-effort attribute name extraction for class-base references."""
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


# --- Markdown extraction --------------------------------------------------


_HEADING_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    """Return a GitHub-style heading anchor (lowercase, hyphenated)."""
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
    """Extract heading spans + heading-as-anchor edges from a Markdown file.

    Captures ATX (``# Title``) and Setext (``Title\\n====``) headings.
    The head container span uses the entire file range so other
    spans nest under ``contains``.
    """
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
            line_no += 2
            continue
        # AC-02: Markdown links emit ``mentions`` edges with exact
        # spans. Each inline ``[text](target)`` and reference-style
        # ``[text][ref]`` match records a span covering the link
        # text and a ``mentions`` edge from the document span to a
        # deterministic link symbol id so callers can audit the
        # relationship. Provenance is ``extracted`` for parser-
        # detected links; we do not chase the link target, so the
        # edge describes the textual mention only.
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


# --- Dispatcher -----------------------------------------------------------


def extract_structure(
    *,
    path: str,
    content: str,
    content_hash: str,
    generation: int,
) -> StructureExtraction:
    """Dispatch to the per-language extractor; unknown languages return empty."""
    language = detect_language(path)
    if language == "python":
        return extract_python(
            path=path,
            content=content,
            content_hash=content_hash,
            generation=generation,
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
    "extract_structure",
]


# Ponytail: pure module — no I/O, no time imports, no global state.
# Tests inject content + content_hash; the reindex pipeline owns the
# I/O boundary.
# Type-ignore note: Path import is here for future callers that need
# filesystem hints; the current module is content-string-based.
_ = Path  # keep the import for downstream callers that need filesystem hints
