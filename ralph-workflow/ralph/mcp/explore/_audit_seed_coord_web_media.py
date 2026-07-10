"""Per-family audit seed entries for the coord_web_media family group.

Extracted from :mod:`ralph.mcp.explore.audit_register` so the hub
module stays under the per-file line ceiling. Each tuple is merged
into :data:`ralph.mcp.explore.audit_register.AUDIT_REGISTER` at
module load via the per-family aggregate.

AC-06: each entry keeps its full rationale, counters, and risk
fields so the audit-register registry can validate measured
provenance end-to-end.
"""

from __future__ import annotations

from ralph.mcp.explore.audit_register import (
    AuditEntry,
    AuditFamily,
    AuditOutcome,
    _counters,
)
from ralph.mcp.tools.names import RalphToolName

_SEED_COORD_WEB_MEDIA: tuple[AuditEntry, ...] = (
    AuditEntry(
        tool=RalphToolName.REPORT_PROGRESS,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "report_progress already emits a bounded text payload "
            "suffixed with ``PROGRESS_PIPELINE_MARKER`` so the idle "
            "watchdog can key on it. The structured marker matches "
            "the Phase-4 acceptance contract for coordination tools."
        ),
        counters=_counters(
            transcript_tokens=24,
            returned_bytes=48,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.DECLARE_COMPLETE,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "declare_complete writes a HMAC-signed completion "
            "sentinel and emits a bounded text payload with the "
            "completion marker. The structured behavior matches "
            "the Phase-4 acceptance contract for coordination tools."
        ),
        counters=_counters(
            transcript_tokens=16,
            returned_bytes=32,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.COORDINATE,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "coordinate is artifact.plan_write-gated; the response "
            "is suffixed with the ``[Coordination event emitted to "
            "pipeline]`` marker so the parent process can observe "
            "structured events. The structured marker matches the "
            "Phase-4 acceptance contract for coordination tools."
        ),
        counters=_counters(
            transcript_tokens=96,
            returned_bytes=192,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.READ_ENV,
        family=AuditFamily.COORDINATION,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Single-string env read; current output is already bounded."
        ),
        counters=_counters(
            transcript_tokens=16,
            returned_bytes=32,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.WEB_SEARCH,
        family=AuditFamily.WEB,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Phase 4: ``format='summary'`` returns a compact JSON envelope "
            "with truncated snippets (<=240 chars), a ``backend_chain_used`` "
            "counter, and ``bytes_in``/``bytes_out`` size counters. Default "
            "``format='raw'`` preserves the legacy Title/URL/Snippet text "
            "shape byte-for-byte so existing callers are unaffected. "
            "Byte-savings on Phase 4 fixtures are >=30 percent versus the "
            "raw shape."
        ),
        counters=_counters(
            transcript_tokens=320,
            returned_bytes=1024,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.VISIT_URL,
        family=AuditFamily.WEB,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Phase 4: ``format='metadata'`` returns a bounded JSON envelope "
            "with ``head_preview``, ``byte_count``, and ``bytes_in``/``bytes_out`` "
            "size counters and DROPS the full text body inline. Default "
            "``format='raw'`` preserves the legacy text body shape "
            "byte-for-byte so existing callers are unaffected. "
            "Byte-savings on Phase 4 fixtures are >=30 percent versus the "
            "raw shape."
        ),
        counters=_counters(
            transcript_tokens=512,
            returned_bytes=2048,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.DOWNLOAD_URL,
        family=AuditFamily.WEB,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Phase 4: ``format='summary'`` returns metadata + a bounded "
            "``head_preview`` (first 240 bytes) + a ``sha256`` fingerprint "
            "(first 16 hex chars) and DOES NOT echo the downloaded body "
            "inline. Default ``format='raw'`` preserves the legacy "
            "metadata-only envelope byte-for-byte so existing callers are "
            "unaffected. Byte-savings on Phase 4 fixtures are >=30 percent "
            "versus the raw shape."
        ),
        counters=_counters(
            transcript_tokens=512,
            returned_bytes=2048,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.READ_IMAGE,
        family=AuditFamily.MEDIA,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Phase 4: ``format='metadata'`` returns a bounded JSON envelope "
            "with ``mime_type``, ``size_bytes``, ``sha256``, ``width``, "
            "``height`` (PNG only), and an ``inline_only`` flag. Default "
            "``format='inline'`` preserves the legacy image content block "
            "byte-for-byte. ``handle_read_image`` never persists a "
            "``ralph://media/{artifact_id}`` artifact so the envelope's "
            "``resource_handle`` is always ``None``."
        ),
        counters=_counters(
            transcript_tokens=128,
            returned_bytes=512,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.READ_MEDIA,
        family=AuditFamily.MEDIA,
        outcome=AuditOutcome.ADD_ARGUMENT,
        rationale=(
            "Phase 4: ``format='metadata'`` returns a bounded JSON envelope "
            "with ``media_kind``, ``mime_type``, ``size_bytes``, ``sha256``, "
            "and a replayable ``resource_handle`` (``ralph://media/{artifact_id}``) "
            "when the underlying delivery registered a Ralph-owned artifact. "
            "Default ``format='inline'`` preserves the legacy block shape "
            "byte-for-byte. Inline media bytes are dropped in metadata mode."
        ),
        counters=_counters(
            transcript_tokens=192,
            returned_bytes=768,
            tool_calls=1,
        ),
    ),
    AuditEntry(
        tool=RalphToolName.RALPH_INDEX_STATUS,
        family=AuditFamily.METADATA,
        outcome=AuditOutcome.KEEP,
        rationale=(
            "Phase 1 ships the new tool with the documented health/"
            "freshness contract; the schema is compact and stable so "
            "no rework is required in this slice."
        ),
        counters=_counters(
            transcript_tokens=96,
            returned_bytes=384,
            tool_calls=1,
            index_storage_bytes=4096,
        ),
    ),
)
