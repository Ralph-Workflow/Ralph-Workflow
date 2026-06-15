# Artifact submission canonical-path contract

All artifact submission side effects in Ralph Workflow must route through a
single canonical backend entry point. This document explains the contract,
what it protects, and how to stay on the right side of the audit.

## Problem

Artifact submission touches several run-scoped files:

- ``.agent/artifacts/<artifact_type>.json`` — the canonical artifact file
- ``.agent/receipts/<run_id>/<artifact_type>.json`` — the completion receipt
- ``.agent/completion_seen_<run_id>.json`` — the completion sentinel
- ``.agent/tmp/<artifact_type>.json`` — the prompt-side fallback file

When different code paths write these files directly, the following failures
become possible:

- A receipt is stamped but the artifact file is malformed or missing.
- A model writes the fallback file, but no code promotes it, so the gate
  reports "no artifact submitted".
- A model skips ``declare_complete`` after a successful submit, so the run
  is retried even though the artifact was received.
- A bypass write evades validation, logging, history snapshotting, or
  markdown handoff.

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
- call ``write_artifact_receipt`` or ``delete_artifact_receipt``,
- write to ``.agent/receipts/``,
- write to ``.agent/completion_seen_*.json``,
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
2. Persists the artifact file.
3. Syncs the markdown handoff file.
4. Snapshots history (when enabled).
5. Stamps the run-scoped receipt.
6. For single-shot artifact types, writes the completion sentinel so a model
   that stops without calling ``declare_complete`` is not force-retried.

If the function raises, none of the run-scoped files are visible to the gate.

## Run-id binding rule

The bridge ``run_id`` is the receipt key. There is no separate label or
secondary source of truth for the receipt namespace. Every caller — the MCP
handler, the completion-signal layer, and the fallback promoter — threads the
same ``run_id`` into ``submit_artifact_canonical``. The receipt is always
written to ``.agent/receipts/<run_id>/<artifact_type>.json`` and the completion
sentinel (for single-shot types) to ``.agent/completion_seen_<run_id>.json``.
See ``commit_plumbing.py:611-620`` for the prior fix that locked this binding
into the commit plumbing path.

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
current ``run_id``.

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
   ``ralph/mcp/tools/artifact.py``.
2. Add the type to ``_CANONICAL_TYPES`` in
   ``ralph/testing/audit_artifact_submission_canonical_path.py``.
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
