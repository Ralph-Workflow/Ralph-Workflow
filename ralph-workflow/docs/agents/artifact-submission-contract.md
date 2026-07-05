# Artifact submission canonical-path contract

All artifact submission side effects in Ralph Workflow must route through a
single canonical backend entry point. This document explains the contract,
what it protects, and how to stay on the right side of the audit.

## Problem

Artifact submission touches several run-scoped files:

- ``.agent/artifacts/<artifact_type>.json`` — the canonical artifact file
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
- ``.agent/tmp/<artifact_type>.json`` — the prompt-side fallback file

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
- A model writes the fallback file, but no code promotes it, so the gate
  reports "no artifact submitted".
- A multi-step flow (for example staged plan drafting) skips the explicit
  completion action, so the run is retried even though intermediate state was
  written.
- A bypass write evades validation, logging, history snapshotting, or
  markdown handoff.
- A bypass writer writes to the legacy file paths under the impression
  that they are still the canonical store, which silently breaks the DB-
  backed completion gate for any reader that does not also fall back to
  the file path.

## Contract

The only allowed writers are:

1. ``ralph.mcp.artifacts.canonical_submit.submit_artifact_canonical`` —
   the canonical backend for MCP ``submit_artifact`` / ``finalize_plan``.
2. ``ralph.mcp.artifacts.canonical_submit.promote_fallback_artifact`` —
   promotes a prompt-side ``.agent/tmp/<type>.json`` file into the canonical
   chain.
3. ``ralph.agents.completion_signals.is_artifact_submitted`` — checks for a
   receipt or a fallback file and promotes the fallback when present.
4. Type-specific layout modules (``commit_message.py``,
   ``smoke_test_result.py``) that are explicitly allowlisted because they
   implement the file-format details used by the canonical backend.

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
- write to ``.agent/artifacts/<canonical-type>.json``,
- write to ``.agent/tmp/<canonical-type>.json``.

The set of canonical artifact types is defined in
``ralph/testing/audit_artifact_submission_canonical_path.py`` and kept in sync
with ``ralph.mcp.tools.artifact._KNOWN_ARTIFACT_TYPES``.

## Canonical entry point

Use ``submit_artifact_canonical`` for every artifact submission:

```python
from pathlib import Path
from ralph.mcp.artifacts.canonical_submit import submit_artifact_canonical

result = submit_artifact_canonical(
    workspace_root=Path("."),
    artifact_type="commit_message",
    parsed_content={"subject": "fix: typo", "body": "..."},
    run_id="run-123",
)
```

The function:

1. Parses and validates the content against the artifact type.
2. Persists the artifact file under ``.agent/artifacts/<type>.json``.
3. Syncs the markdown handoff file.
4. Snapshots history (when enabled).
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

If the function raises, none of the run-scoped files are visible to the gate.

## Run-id binding rule

The bridge ``run_id`` is the receipt key. There is no separate label or
secondary source of truth for the receipt namespace. Every caller — the MCP
handler, the completion-signal layer, and the fallback promoter — threads the
same ``run_id`` into ``submit_artifact_canonical``. After RFC-013 P3, the
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

## Fallback promotion

Some single-shot prompts instruct the model to write the artifact directly to
``.agent/tmp/<type>.json`` when MCP tool calling is unavailable. The AGY smoke
branch likewise instructs the AGY agent to write the artifact directly to
``.agent/artifacts/<type>.json`` because AGY headless mode does not reliably
call Ralph's MCP tools. Before the run is considered complete, either fallback
file must be promoted into the canonical chain:

```python
from pathlib import Path
from ralph.mcp.artifacts.canonical_submit import promote_fallback_artifact

result = promote_fallback_artifact(
    workspace_root=Path("."),
    artifact_type="commit_message",
    run_id="run-123",
)
```

``promote_fallback_artifact`` checks ``.agent/tmp/<type>.json`` first, then
``.agent/artifacts/<type>.json``. It tolerates both the bare inner payload and
the outer ``{name, type, content, ...}`` envelope produced by the AGY smoke
prompt wrapper, extracts the payload, and routes it through
``submit_artifact_canonical`` so a canonical receipt is stamped under the
current ``run_id`` in ``.agent/state.db`` (the RFC-013 P3 canonical store).
After promotion, no fresh files are created under ``.agent/receipts/`` — the
legacy file path is only consulted during the dual-read rollout window
and during a DB write failure as a durable fallback.

``is_artifact_submitted`` performs the same check during completion
evaluation. If a fallback file exists, it is promoted and the run is treated
as successfully submitted.

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
2. Add a format doc under ``ralph/mcp/artifacts/format_docs/``.
3. If the type needs custom layout logic, add it in a type-specific module
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

The audit module now imports ``_CANONICAL_TYPES`` directly from
``ralph.mcp.tools.artifact._KNOWN_ARTIFACT_TYPES`` instead of maintaining a
separate hardcoded set. The set includes internal compatibility aliases such as
``'review'`` and ``'verification'`` in addition to the user-facing artifact
types documented in the runtime format-doc tables.

This single-source-of-truth arrangement means any future canonical type added
to ``_KNOWN_ARTIFACT_TYPES`` propagates automatically to the audit — no
manual sync is needed. ``test_audit_artifact_submission_canonical_types_sync.py``
pins the equality so drift is caught at test time.

## Smoke plumbing

``artifact_submitted`` in ``SmokeRunResult`` is now computed by calling
``is_artifact_submitted`` — which promotes the AGY-direct fallback to a
canonical receipt — instead of ``read_smoke_test_result_artifact(file) is not
None``. The behavioral change is that a malformed artifact file now correctly
results in ``artifact_submitted=False``, whereas the old raw file-presence
check would have returned ``True`` for a corrupt or unparseable file.

The AGY-direct fallback promotion (``.agent/artifacts/<type>.json``) now runs
at smoke-report time rather than being deferred to the completion-signal
layer, ensuring the receipt namespace is populated before the gate evaluates
the run.
