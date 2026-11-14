# OpenCode Subagent Wire and Child-Store Provenance

This document records the measured OpenCode 1.19.4 subagent session with task
dispatch, capturing both the wire NDJSON frames and the corresponding SQLite
child-store records committed under:
- `tests/display/_fixtures/opencode_subagent_wire.jsonl`
- `tests/display/_fixtures/opencode_subagent_child_store.json`

## Probed binary

- Version: `opencode 1.19.4`
- Binary: `/home/mistlight/.local/bin/opencode`
- Database store: `~/.local/share/opencode/opencode.db`
- Measured on: 2026-11-14

## Capture command

The capture was executed against the live OpenCode runtime in a clean working directory:

```bash
mkdir -p /tmp/test-subagent-capture
echo "hello world" > /tmp/test-subagent-capture/sample.txt
opencode run --format json --auto --model opencode/longcat-2.5-free \
  --dir /tmp/test-subagent-capture \
  "Use the task tool to dispatch a subagent with agent 'explore' or 'general' to read /tmp/test-subagent-capture/sample.txt and report its contents."
```

## Captured wire session

The live parent session emitted 6 NDJSON frames:
1. `step_start`: Initial step start carrying parent sessionID `ses_e25127f85ffesMj5vyUffLGYFT`.
2. `tool_use`: Native `task` tool call dispatching child session `ses_e25126d50ffeAJgP3Zps4s3fnU` with subagent type `explore`. The `metadata` payload includes `parentSessionId` and `sessionId`. The `output` field contains the wrapped `<task id="..." state="completed">` result from the child.
3. `step_finish`: Terminal step-finish for the tool call step (`reason: "tool-calls"`).
4. `step_start`: Step start for the final synthesis turn.
5. `text`: Assistant text summarizing the subagent's output.
6. `step_finish`: Terminal step-finish (`reason: "stop"`).

## Captured child store schema

OpenCode manages session state in SQLite (`opencode.db`). Subagent sessions are related to parent sessions via `session.parent_id`.

Key tables captured in `opencode_subagent_child_store.json`:

1. `session`:
   - `id`: Unique session identifier (`TEXT PRIMARY KEY`).
   - `parent_id`: Parent session identifier (`TEXT NULL`), establishing the parent-child lineage.
   - `agent`: Agent role (`TEXT`), e.g. `"build"` for parent, `"explore"` for subagent.
   - `title`: Session title (`TEXT`), e.g. `"Read sample.txt (@explore subagent)"`.
   - `time_created`, `time_updated`: Epoch millisecond timestamps (`INTEGER`).
   - Additional fields: `project_id`, `slug`, `directory`, `path`, `version`, `model`, `tokens_*`, `cost`.

2. `part`:
   - `id`: Part identifier (`TEXT PRIMARY KEY`).
   - `session_id`: Associated session (`TEXT NOT NULL`).
   - `message_id`: Associated message (`TEXT NOT NULL`).
   - `time_created`, `time_updated`: Epoch millisecond timestamps (`INTEGER`).
   - `data`: Serialized JSON payload (`TEXT NOT NULL`) indicating part kind:
     - `{"type":"text", ...}`
     - `{"type":"step-start"}`
     - `{"type":"reasoning", "text": ..., "time": ...}`
     - `{"type":"tool", "tool":"read", "state": ...}`
     - `{"type":"step-finish", "reason": ...}`

3. `message`:
   - `id`: Message identifier (`TEXT PRIMARY KEY`).
   - `session_id`: Associated session (`TEXT NOT NULL`).
   - `time_created`, `time_updated`: Epoch millisecond timestamps (`INTEGER`).
   - `data`: Serialized JSON message envelope (`TEXT NOT NULL`).

## Lineage and probe query contract

`SqliteOpenCodeChildPartSource` traverses subagent activity using the recursive query:
```sql
WITH RECURSIVE lineage(id, depth) AS (
  SELECT id, 1 FROM session WHERE parent_id = ?
  UNION ALL
  SELECT s.id, l.depth + 1 FROM session AS s JOIN lineage AS l ON s.parent_id = l.id
  WHERE l.depth < ?
)
SELECT p.id, p.session_id, s.agent, s.title, p.time_updated, p.data
FROM part AS p JOIN session AS s ON s.id = p.session_id
WHERE p.session_id IN (SELECT id FROM lineage) AND p.time_updated >= ?
ORDER BY p.time_updated ASC, p.id ASC LIMIT ?;
```

This ensures any child session whose `parent_id` matches the parent's `sessionID` (and recursive descendants up to depth 8) has its parts fetched and surfaced to the watchdog.
