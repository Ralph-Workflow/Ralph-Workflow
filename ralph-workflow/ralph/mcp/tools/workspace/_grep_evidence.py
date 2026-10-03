"""Evidence row creation helpers for grep operations."""

from __future__ import annotations

import time

from ralph.mcp.explore.store import EvidenceRow, derive_evidence_id


def _derive_evidence_id_for_span(
    *,
    path: str,
    content_hash: str,
    start_line: int,
    end_line: int,
    kind: str,
) -> str:
    """Compute the prompt-exact evidence id from deterministic inputs.

    Centralized here so the grep handler and the reindex pipeline
    produce identical ids for the same file span.
    """
    return derive_evidence_id(
        path=path,
        content_hash=content_hash,
        start_line=start_line,
        end_line=end_line,
        kind=kind,
        extractor_version="phase2-structure-v1",
    )


class _EvidenceRowBuilder:
    """Tiny helper that builds an ``EvidenceRow`` from span inputs."""

    def __init__(
        self,
        *,
        evidence_id: str,
        path: str,
        start_line: int,
        end_line: int,
        content_hash: str,
        generation: int,
        source_tool: str,
        evidence_kind: str,
    ) -> None:
        self.evidence_id = evidence_id
        self.path = path
        self.start_line = start_line
        self.end_line = end_line
        self.content_hash = content_hash
        self.generation = generation
        self.source_tool = source_tool
        self.evidence_kind = evidence_kind

    def build(self) -> EvidenceRow:
        return EvidenceRow(
            evidence_id=self.evidence_id,
            path=self.path,
            start_line=self.start_line,
            end_line=self.end_line,
            content_hash=self.content_hash,
            generation=self.generation,
            source_tool=self.source_tool,
            evidence_kind=self.evidence_kind,
            created_at=time.time(),
            is_stale=False,
        )

