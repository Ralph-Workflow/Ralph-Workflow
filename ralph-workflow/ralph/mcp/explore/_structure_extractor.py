from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Protocol

from ralph.mcp.explore.structure import StructureExtraction

DEFAULT_CROSSOVER_NODES: Final[int] = 200

IMPL_SCALAR: Final[str] = "scalar"
IMPL_ACCELERATED: Final[str] = "accelerated"
IMPL_AUTO: Final[str] = "auto"
_IMPLEMENTATIONS: Final[frozenset[str]] = frozenset({IMPL_SCALAR, IMPL_ACCELERATED, IMPL_AUTO})


class PythonStructureExtractor(Protocol):
    def __call__(
        self,
        *,
        path: str,
        content: str,
        content_hash: str,
        generation: int,
    ) -> StructureExtraction: ...


_RUNTIME_CROSSOVER: deque[int | None] = deque([None], maxlen=1)


def set_runtime_crossover(nodes: int | None) -> None:
    _RUNTIME_CROSSOVER[0] = nodes


def get_runtime_crossover() -> int:
    if _RUNTIME_CROSSOVER[0] is None:
        return DEFAULT_CROSSOVER_NODES
    return _RUNTIME_CROSSOVER[0]


def _scalar_extract_python(
    *,
    path: str,
    content: str,
    content_hash: str,
    generation: int,
) -> StructureExtraction:
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
    from ralph.mcp.explore.structure import extract_python_accelerated

    return extract_python_accelerated(
        path=path,
        content=content,
        content_hash=content_hash,
        generation=generation,
    )


_IMPL_REGISTRY: Final[Mapping[str, PythonStructureExtractor]] = MappingProxyType(
    {
        IMPL_SCALAR: _scalar_extract_python,
        IMPL_ACCELERATED: _accelerated_extract_python,
    }
)


def select_structure_extractor(
    name: str,
    *,
    ast_node_count: int = 0,
) -> PythonStructureExtractor:
    if name not in _IMPLEMENTATIONS:
        raise ValueError(
            f"unknown structure extractor: {name!r}; expected one of {sorted(_IMPLEMENTATIONS)}"
        )
    if name == IMPL_AUTO:
        cutoff = get_runtime_crossover()
        if ast_node_count <= 0 or ast_node_count >= cutoff:
            return _IMPL_REGISTRY[IMPL_ACCELERATED]
        return _IMPL_REGISTRY[IMPL_SCALAR]
    return _IMPL_REGISTRY[name]


def structure_extractor_name(extractor: PythonStructureExtractor) -> str:
    if extractor is _scalar_extract_python:
        return IMPL_SCALAR
    if extractor is _accelerated_extract_python:
        return IMPL_ACCELERATED
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
