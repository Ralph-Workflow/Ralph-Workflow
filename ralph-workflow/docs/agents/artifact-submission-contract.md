# Artifact submission canonical-path contract

All artifact submission side effects in Ralph Workflow must route through a
single canonical backend entry point. This document explains the contract,
what it protects, and how to stay on the right side of the audit.

## Problem

Artifact submission touches several run-scoped files:

- ``.agent/artifacts/<artifact_type>.md`` — the canonical artifact file: the
  validated markdown document itself, stored byte-for-byte (artifacts are
  markdown source documents; there is no JSON envelope)
- ``.agent/<TYPE>.md`` handoff copies (for example ``.agent/PLAN.md``) — a
  byte-identical copy of the canonical markdown, written in the same
  canonical submission step for the types listed in
  ``ralph.mcp.artifacts.handoffs.HANDOFF_PATHS``
- ``.agent/state.db`` (RFC-013 P3) — the **canonical** durable store for
  completion receipts and completion sentinels, backed by
  ``RunStateDB`` (one WAL-mode SQLite database per workspace, ``state.db``
  alongside the auxiliary ``state.db-wal`` / ``state.db-shm`` files that
  the kernel manages for the WAL)
- ``.agent/receipts/<run_id>/<artifact_type>.json`` — the legacy
  completion-receipt file path; **read-fallback only** during the dual-read
  rollout window so an in-flight run that was upgraded mid-run still
  passes its completion gate. Production writes do not create this file.
- ``.agent/completion_seen_<run_id>.json`` — the legacy completion-sentinel
  file path; **read-fallback / durable-fallback only** (used when the DB
  write fails and during the dual-read rollout window). Production writes
  do not create this file.
- ``.agent/tmp/<artifact_type>.json`` — the **obsolete** prompt-side JSON
  fallback file from the pre-markdown era; new runs clear any leftovers
  (``_clear_fallback_artifacts``) and nothing promotes them anymore
- ``.agent/tmp/<artifact_type>.md`` — an agent-written markdown fallback;
  ``promote_fallback_artifact`` validates it through the same registered
  markdown spec as the MCP tools before routing it through canonical submit

The classification matters: ``.agent/receipts/<run_id>/<artifact_type>.json``
and ``.agent/completion_seen_<run_id>.json`` are **legacy paths**, not the
normal canonical store. They appear in the audit allowlist (under
``ralph/testing/audit_artifact_submission_canonical_path.py``) only as
read-fallback / durable-fallback surfaces, never as production write targets.
Anything that writes one of these files outside a documented fallback path
is a contract bypass.

When different code paths write these files directly, the following failures
become possible:

- A receipt is stamped but the artifact file is malformed or missing.
- A model writes an artifact file directly, but no receipt is stamped, so the
  gate reports "no artifact submitted".
- A bypass write evades validation, logging, history snapshotting, or the
  markdown handoff copy.
- A bypass writer writes to the legacy file paths under the impression
  that they are still the canonical store, which silently breaks the DB-
  backed completion gate for any reader that does not also fall back to
  the file path.

## Contract

The only allowed writers are:

1. ``ralph.mcp.artifacts.canonical_submit.submit_artifact_canonical`` —
   the canonical backend for the MCP ``ralph_submit_md_artifact`` and
   ``ralph_finalize_md_artifact`` tools (``handle_submit_md_artifact`` /
   ``handle_finalize_md_artifact`` in ``ralph.mcp.tools.md_artifact``).
   Draft staging (``ralph_stage_md_artifact`` / ``ralph_get_md_draft`` /
   ``ralph_discard_md_draft``) writes only the per-type draft file
   ``.<artifact_type>.draft.md`` via ``ralph.mcp.artifacts.md_draft_io``,
   never the canonical artifact, receipt, or sentinel surfaces.
2. Type-specific layout modules (``commit_message.py``,
   ``smoke_test_result.py``) that are explicitly allowlisted because they
   implement the file-format details used by the canonical backend.

``promote_fallback_artifact`` supports the markdown fallback path used when an
agent writes ``.agent/tmp/<artifact_type>.md`` instead of calling the MCP tool.
It validates the document with the registered markdown spec, routes valid
content through ``submit_artifact_canonical``, and removes the fallback only
after successful submission. Invalid or absent fallback documents return
``None`` and never stamp a receipt.

No other module may:

- call ``store.submit_artifact``,
- call ``write_artifact_receipt`` or ``delete_artifact_receipt`` directly,
- write to ``.agent/receipts/`` as a **production** write (the audit
  recognises the read-fallback / durable-fallback surfaces only; a
  direct write of a fresh receipt file outside the documented fallback
  path is a contract bypass),
- write to ``.agent/completion_seen_*.json`` as a **production** write
  (same caveat as above),
- write to ``.agent/state.db`` directly — the SQLite surface is owned by
  ``RunStateDB`` and the canonical-submit chain; bypass writers bypass
  validation, logging, and history snapshotting,
- write to the canonical artifact files under ``.agent/artifacts/`` or
  their ``.agent/<TYPE>.md`` handoff copies outside the canonical chain
  (the audit's static path patterns additionally flag any write to the
  legacy ``.agent/artifacts/<canonical-type>.json`` and
  ``.agent/tmp/<canonical-type>.json`` names).

The set of canonical artifact types is defined in
``ralph/testing/audit_artifact_submission_canonical_path.py`` and kept in sync
with ``ralph.mcp.tools.artifact._KNOWN_ARTIFACT_TYPES``.

## Canonical entry point

Use ``submit_artifact_canonical`` for every artifact submission. The caller
(normally ``handle_submit_md_artifact``) validates the markdown first via
``parse_and_validate``; the function persists the already-validated markdown
source:

```python
from pathlib import Path
from ralph.mcp.artifacts.canonical_submit import submit_artifact_canonical

result = submit_artifact_canonical(
    workspace_root=Path("."),
    artifact_type="commit_message",
    parsed_content=parsed,          # retained for validate-before-persist callers
    markdown=document,              # required: the markdown source of truth
    run_id="run-123",
)
```

The function:

1. Requires the ``markdown`` source (raises ``ValueError`` without it); the
   stored artifact is always ``.md``, never a JSON envelope.
2. Snapshots the existing artifact into history first (when history is
   enabled and a canonical file already exists).
3. Persists the markdown under ``.agent/artifacts/<type>.md`` (idempotent —
   byte-identical content is not rewritten).
4. Writes the byte-identical handoff copy (for example ``.agent/PLAN.md``)
   when the type has a handoff path.
5. Stamps the run-scoped receipt by upserting one row into
   ``.agent/state.db`` via ``RunStateDB.upsert_receipt`` (RFC-013 P3).
   When the DB write raises ``sqlite3.Error`` or ``OSError`` on open or
   upsert, a durable-fallback write lands at the legacy
   ``.agent/receipts/<run_id>/<artifact_type>.json`` path so the gate
   still has evidence.
6. For single-shot artifact types, writes the completion sentinel by
   upserting one row into ``.agent/state.db`` via
   ``RunStateDB.upsert_completion_sentinel`` so a model that stops without
   calling ``declare_complete`` is not force-retried. The same durable-
   fallback rule applies to ``.agent/completion_seen_<run_id>.json``.
   For the current completion gate, a run-scoped artifact receipt is
   already sufficient completion evidence for required-artifact flows;
   ``declare_complete`` remains useful as an explicit signal but is not
   the sole path to terminal completion.

The receipt is written strictly after the artifact and handoff files, so if
the function raises before stamping it, the completion gate sees no
submission evidence for the run.

## Run-id binding rule

The bridge ``run_id`` is the receipt key. There is no separate label or
secondary source of truth for the receipt namespace. Every caller — the MCP
handler and the completion-signal layer — threads the same ``run_id`` into
``submit_artifact_canonical``. After RFC-013 P3, the
receipt is **always written to ``.agent/state.db``** (one row keyed on
``(run_id, artifact_type)``) via ``RunStateDB.upsert_receipt``; the completion
sentinel (for single-shot types) is **always written to ``.agent/state.db``**
(one row keyed on ``run_id``) via ``RunStateDB.upsert_completion_sentinel``.
The legacy file paths ``.agent/receipts/<run_id>/<artifact_type>.json`` and
``.agent/completion_seen_<run_id>.json`` are **legacy read-fallback and
durable-fallback paths only** — production writes never create them under
RFC-013 P3. They exist so an in-flight run that was upgraded mid-run still
passes its completion gate, and so a DB write failure (``sqlite3.Error`` /
``OSError`` on open or upsert) can still produce durable evidence through
the legacy file path. See ``commit_plumbing.py:611-620`` for the prior fix
that locked the ``run_id`` binding into the commit plumbing path, and
``ralph/mcp/artifacts/state_db.py`` for the canonical SQLite surface.

## Markdown fallback promotion

When an agent cannot call the submit tool, it may write the complete document
to ``.agent/tmp/<type>.md``. The completion gate then uses the same validation
and submission path as an MCP submission:

- ``promote_fallback_artifact`` reads ``.agent/tmp/<type>.md``, validates it
  with the registered ``MdArtifactSpec``, and calls
  ``submit_artifact_canonical`` only when there are no error diagnostics.
- Successful promotion removes the temporary markdown file. Missing, unreadable,
  unknown-type, and invalid documents remain unsubmitted and stamp no receipt.
- ``_clear_fallback_artifacts`` removes stale temporary markdown documents and
  obsolete ``.agent/tmp/*.json`` files when a new run starts.
- ``is_artifact_submitted`` attempts markdown fallback promotion before checking
  for the run-scoped receipt.

The MCP tools, staged-draft finalization, and markdown fallback promotion all
converge on ``submit_artifact_canonical``.

## Audit

``make verify`` runs
``ralph.testing.audit_artifact_submission_canonical_path``. The audit scans
``ralph/`` with Python's ``ast`` module and flags any bypass outside the
allowlisted sites.

It detects:

- ``Path(...).write_text(...)`` or ``backend.write_text(...)`` targeting a
  protected path.
- ``open(...)`` targeting a protected path.
- Calls to ``store.submit_artifact``.
- Calls to ``write_artifact_receipt`` / ``delete_artifact_receipt``.

It skips:

- ``tests/``
- ``ralph/testing/audit_artifact_submission_canonical_path.py``
- ``ralph/mcp/artifacts/canonical_submit.py``
- ``ralph/mcp/artifacts/commit_message.py``
- ``ralph/mcp/artifacts/smoke_test_result.py``
- Code between ``# === BEGIN CANONICAL SUBMIT OPS ===`` and
  ``# === END CANONICAL SUBMIT OPS ===`` markers in
  ``ralph/mcp/tools/artifact.py``

The audit is enforced as a mandatory ``make verify`` step. A bypass fails
verification with output like:

```
ralph/evil.py:42: [ARTIFACT-BYPASS] receipt_write: direct write to .agent/receipts/ outside canonical submit
```

## Adding a new canonical artifact type

1. Add the type to ``_KNOWN_ARTIFACT_TYPES`` in
   ``ralph/mcp/tools/artifact.py``. The audit's ``_CANONICAL_TYPES`` is
   derived from this set via import, so no separate audit update is needed.
2. Declare an ``MdArtifactSpec`` for the type under
   ``ralph/mcp/artifacts/markdown/specs/`` and register it (the specs
   package registers every spec on import via
   ``ralph.mcp.artifacts.markdown.registry``).
3. Add a format doc under ``ralph/mcp/artifacts/format_docs/``.
4. If the type needs custom layout logic, add it in a type-specific module
   under ``ralph/mcp/artifacts/`` and add the module path to
   ``_FILE_ALLOWLIST`` in the audit.

## Allowlist markers

If a legitimate write must live outside the canonical module, bound it with
inline markers and document why in a comment:

```python
# === BEGIN CANONICAL SUBMIT OPS ===
# This block is part of the canonical artifact-submission chain and is
# allowlisted by audit_artifact_submission_canonical_path.
write_artifact_receipt(workspace_root, run_id, artifact_type)
# === END CANONICAL SUBMIT OPS ===
```

Do not use the markers to hide unrelated writes. The audit is a guardrail,
not a general-purpose escape hatch.

## Audit invariants

Five module-level constants are guarded by ``if``/``raise RuntimeError`` checks
at import time: ``_CANONICAL_TYPES``, ``_FILE_ALLOWLIST``,
``_CANONICAL_BLOCK_START``, ``_CANONICAL_BLOCK_END``, ``_SKIP_DIRS``.
``RuntimeError`` is used instead of ``assert`` because Python's ``-O`` flag
strips all ``assert`` statements, which would silently disable the invariants
in optimized builds. The path-existence check on ``_FILE_ALLOWLIST`` entries
ensures every allowlisted module actually exists on disk; a renamed or deleted
layout module causes an immediate import failure rather than a silent audit
gap.

Every invariant is tested in ``test_audit_artifact_submission_canonical_path.py``
under normal ``python`` execution and under ``python -O`` to confirm immunity
to optimization stripping.

## Detection coverage

Four new AST detection categories have been added to the audit:

- ``shutil.copy``, ``shutil.copy2``, ``shutil.copyfile``, ``shutil.copytree``,
  ``shutil.move`` — file-copy operations that could duplicate a protected file
  outside the canonical chain.
- ``os.rename``, ``os.renames``, ``os.replace`` — filesystem renames that could
  move a receipt or artifact file out from under the canonical bookkeeping.
- ``pathlib.Path.replace``, ``pathlib.Path.rename`` — the pathlib equivalents
  of the ``os``-module rename operations.

All ``shutil`` and ``os`` detections work through import aliasing (e.g.
``import shutil as s; s.copy(src, dst)`` is caught, not just
``import shutil; shutil.copy(src, dst)``). A call to ``shutil.move(src, dst)``
where ``dst`` resolves to ``.agent/receipts/x.json`` would be flagged.

## Canonical types sync

The audit module imports ``_CANONICAL_TYPES`` directly from
``ralph.mcp.tools.artifact._KNOWN_ARTIFACT_TYPES`` instead of maintaining a
separate hardcoded set. The set matches the eleven artifact types that have
registered markdown specs (``plan``, the three ``*_analysis_decision`` types,
``development_result``, ``product_spec``, ``issues``, ``fix_result``,
``smoke_test_result``, ``commit_cleanup``, ``commit_message``).

This single-source-of-truth arrangement means any future canonical type added
to ``_KNOWN_ARTIFACT_TYPES`` propagates automatically to the audit — no
manual sync is needed. ``test_audit_artifact_submission_canonical_types_sync.py``
pins the equality so drift is caught at test time.

## Smoke plumbing

``artifact_submitted`` in ``SmokeRunResult`` is computed by calling
``is_artifact_submitted`` instead of a raw file-presence check. With the
fallback promotion path removed, this reduces to canonical receipt
presence: a run counts as submitted only when the markdown submission
stamped a receipt, so a stray or malformed artifact file never reports
``artifact_submitted=True``.
