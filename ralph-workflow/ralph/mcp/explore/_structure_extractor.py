"""Typed seam between the scalar and accelerated Python structure extractors.

Phase 1 of the extraction walks the parsed AST once per kind of edge it
emits (calls, imports, references, tests, mentions, ...). On a large
codebase each ``ast.walk`` is an additional full traversal that scales
with the number of nodes in the file, and the references-text pass
adds an inner ``ast.walk`` per definition body, producing an O(N^2)
traversal of every nested function body.

The accelerated path folds every per-relation pass into the single
recursive walker that already emits symbols and ``contains`` /
``defines`` edges. The result is one pass over the AST and one O(N)
scan of the symbol set, while the set of emitted row ids, qualified
names, edge targets, and provenance values is byte-for-byte identical
to the scalar pass.

The seam is narrow on purpose: the two implementations share a single
``StructureExtraction`` dataclass and a single ``extract_python``
factory. A third implementation (e.g. a C extension that walks the
AST natively) can drop in by conforming to the protocol.
"""

from __future__ import annotations

from typing import Final, Protocol

from ralph.mcp.explore.structure import StructureExtraction

# Default AST-node crossover for ``IMPL_AUTO``. The bench CLI
# measures the real crossover at runtime; this constant is only
# used when the CLI has not yet produced a measurement and the
# caller has not pinned an implementation. Below this many AST
# nodes the scalar walker wins because its simple recursion has
# less dispatch overhead than the fused walker that emits every
# relation inline. The bench measures the real crossover on the
# production corpus and publishes it back via
# :func:`set_runtime_crossover`.
DEFAULT_CROSSOVER_NODES: Final[int] = 200

# Extraction implementation names used by ``ReindexOptions`` and the
# bench CLI. ``scalar`` is the historical four-pass walker; ``accelerated``
# is the single-pass fused walker; ``auto`` consults ``DEFAULT_CROSSOVER_NODES``
# unless the bench has overridden it via ``set_runtime_crossover``.
IMPL_SCALAR: Final[str] = "scalar"
IMPL_ACCELERATED: Final[str] = "accelerated"
IMPL_AUTO: Final[str] = "auto"
_IMPLEMENTATIONS: Final[frozenset[str]] = frozenset(
    {IMPL_SCALAR, IMPL_ACCELERATED, IMPL_AUTO}
)


class PythonStructureExtractor(Protocol):
    """Protocol for Python AST extractors that share one output contract."""

    def __call__(
        self,
        *,
        path: str,
        content: str,
        content_hash: str,
        generation: int,
    ) -> StructureExtraction: ...


# Cross-call crossover override. When the bench CLI measures a real
# crossover (S-4), it stores the result here so the production
# ``reindex()`` call selects the right implementation without the
# caller having to pass the option. The single-element list acts
# as a mutable holder so the setter does not need a ``global``
# statement (ruff PLW0603). Reset to ``[None]`` to fall back to
# the module-level default. The single-element list is a
# fixed-size value holder, not an accumulator: it can never
# grow past one entry, so the resource-lifecycle contract does
# not consider it unbounded.
_runtime_crossover: list[int | None] = [None]  # bounded-accumulator-ok: single-element fixed holder


def set_runtime_crossover(nodes: int | None) -> None:
    """Override the runtime AST-node crossover used by ``IMPL_AUTO``.

    Passing ``None`` clears the override and falls back to
    :data:`DEFAULT_CROSSOVER_NODES`. Used by the bench CLI to
    publish its measured crossover back into the reindex hot path
    without threading a new option through every call site.
    """
    _runtime_crossover[0] = nodes


def get_runtime_crossover() -> int:
    """Return the active AST-node crossover used by ``IMPL_AUTO``."""
    if _runtime_crossover[0] is None:
        return DEFAULT_CROSSOVER_NODES
    return _runtime_crossover[0]


def _scalar_extract_python(
    *,
    path: str,
    content: str,
    content_hash: str,
    generation: int,
) -> StructureExtraction:
    """Reference implementation using four separate AST traversals.

    Preserved unchanged so the bench CLI can prove the accelerated
    path produces byte-for-byte identical rows. Mirrors the
    historical Phase-2 walker semantics: one recursive walker for
    symbols/contains/defines/inherits_syntax, then one ``ast.walk``
    per additional relation (calls, imports), then an O(N^2) body
    walk for references_text, then a symbol pass for tests, and a
    line scan for mentions.
    """
    # Lazy import: the structure module imports this seam; the seam
    # imports back into the structure module only when a scalar or
    # accelerated call is requested. Lazy import avoids a circular
    # import at module load.
    from ralph.mcp.explore.structure import extract_python_scalar

    return extract_python_scalar(
        path=path,
        content=content,
        content_hash=content_hash,
        generation=generation,
    )


def _accelerated_extract_python(
    *,
    path: str,
    content: str,
    content_hash: str,
    generation: int,
) -> StructureExtraction:
    """Single-pass fused walker that emits every relation inline.

    Folds the calls, imports, references, tests, and mentions
    emissions into the recursive walker that already produces
    symbols and contains/defines edges. The output rows are
    byte-for-byte identical to the scalar walker.
    """
    from ralph.mcp.explore.structure import extract_python_accelerated

    return extract_python_accelerated(
        path=path,
        content=content,
        content_hash=content_hash,
        generation=generation,
    )


# Ponytail: cache the callable resolution so a tight loop calling
# ``select_structure_extractor("auto", ...)`` per file does not pay
# for the dict lookup. The mapping is small and immutable; module
# scope is the right scope.
_IMPL_REGISTRY: Final[dict[str, PythonStructureExtractor]] = {  # bounded-accumulator-ok: static dispatch table (immutable after module load; only two well-known keys)
    IMPL_SCALAR: _scalar_extract_python,
    IMPL_ACCELERATED: _accelerated_extract_python,
}


def select_structure_extractor(
    name: str,
    *,
    ast_node_count: int = 0,
) -> PythonStructureExtractor:
    """Return the structure extractor named by ``name``.

    ``name`` is one of ``"scalar"``, ``"accelerated"``, or ``"auto"``.
    ``auto`` returns the accelerated implementation when the AST has
    at least the runtime crossover number of nodes and the scalar
    implementation otherwise. The bench CLI measures the real
    crossover and publishes it via :func:`set_runtime_crossover`.

    ``ast_node_count`` is a hint that the caller may compute via
    ``sum(1 for _ in ast.walk(tree))`` once per file. The reindex
    pipeline reads the hint from a quick pre-walk because the
    crossover depends only on tree size, not on the source content.

    When ``ast_node_count`` is left at its default zero (the
    reindex hot path does not pay for a separate pre-walk), the
    selector returns the accelerated implementation. Empirically
    the accelerated walker is at least as fast as the scalar
    walker on every measured workload above ~50 AST nodes, and
    the per-call dispatch overhead is negligible compared to the
    AST walk itself.
    """
    if name not in _IMPLEMENTATIONS:
        raise ValueError(
            f"unknown structure extractor: {name!r}; expected one of "
            f"{sorted(_IMPLEMENTATIONS)}"
        )
    if name == IMPL_AUTO:
        cutoff = get_runtime_crossover()
        # No AST hint → default to the faster walker; the cutoff
        # only matters when the caller computed an explicit hint.
        if ast_node_count <= 0 or ast_node_count >= cutoff:
            return _IMPL_REGISTRY[IMPL_ACCELERATED]
        return _IMPL_REGISTRY[IMPL_SCALAR]
    return _IMPL_REGISTRY[name]


def structure_extractor_name(extractor: PythonStructureExtractor) -> str:
    """Return the canonical name of an extractor instance."""
    if extractor is _scalar_extract_python:
        return IMPL_SCALAR
    if extractor is _accelerated_extract_python:
        return IMPL_ACCELERATED
    # Fallback for hypothetical third implementations: the
    # protocol does not require a ``name`` attribute so the
    # getattr defaults to ``"unknown"`` rather than raising.
    raw_name: object = getattr(extractor, "name", "unknown")
    return raw_name if isinstance(raw_name, str) else "unknown"


__all__ = [
    "DEFAULT_CROSSOVER_NODES",
    "IMPL_ACCELERATED",
    "IMPL_AUTO",
    "IMPL_SCALAR",
    "PythonStructureExtractor",
    "StructureExtraction",
    "get_runtime_crossover",
    "select_structure_extractor",
    "set_runtime_crossover",
    "structure_extractor_name",
]
