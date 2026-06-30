# XML Deprecation Timeline

> **Status: Historical Rust-era reference only.** This page describes the
> retired Rust implementation (references `json_artifact.rs`, `src/prompts/xsd/`,
> and a never-shipped Rust deserializer). The maintained Python package in
> `ralph-workflow/` — specifically `ralph-workflow/ralph/mcp/artifacts/`
> (Pydantic models + JSON Schema) — is the source of truth.


This document tracks the migration from XSD-validated XML artifacts to JSON
Schema-validated JSON artifacts submitted via MCP. It defines the current
state, transition conditions, removal criteria, rollback procedure, and
monitoring checklist.

No hard removal dates are set. Removal is gated on measurable conditions only — see Section 3.

---

## 1. Current State: Dual-Mode

JSON is the preferred artifact format for reading. XML is the fallback.

| Boundary       | Extract        | Validate       | JSON converter           |
|----------------|----------------|----------------|--------------------------|
| planning       | JSON-first     | JSON-first     | `plan_elements_from_envelope` |
| development    | JSON-first     | JSON-first     | `development_result_from_envelope` |
| review (issues)| XML-only       | XML-only       | Not yet implemented      |
| fix (result)   | XML-only       | XML-only       | Not yet implemented      |
| commit         | XML-only       | XML-only       | Not yet implemented      |

**Write path**: Agents still write XML files. MCP JSON submission is the
target but agents have not fully transitioned yet.

**Read path**: For `plan` and `development_result`, the reducer boundary
checks for a JSON artifact first (`workspace.read_artifact_json`), converts
it via `json_artifact.rs`, and falls back to XML if no JSON artifact exists
or if JSON conversion fails.

---

## 2. Transition Period

During the transition period:

- Agents gradually transition from writing XML files to submitting JSON via
  MCP tool calls.
- The reducer boundary modules maintain dual-mode reading: JSON-first with
  XML fallback.
- JSON Schema files exist for all artifact types (plan, development_result,
  issues, fix_result, commit_message) but boundary integration is only
  complete for plan and development_result.
- Remaining boundaries (review, fix, commit) need JSON converters and
  boundary wiring before they can participate in dual-mode reading.

### Transition steps (no dates, condition-gated)

1. **Complete boundary wiring** for review issues, fix result, and commit
   message (add converters to `json_artifact.rs` and wire into respective
   boundary modules).
2. **Agent prompt updates**: Update agent prompts to instruct MCP JSON
   submission instead of XML file writes.
3. **Monitoring period**: Track `json_artifact_read` vs `xml_fallback_read`
   counts to confirm agents are producing JSON.
4. **XML write removal from agents**: Once agents exclusively produce JSON,
   remove XML file write instructions from prompts.

---

## 3. Removal Conditions

XML support will be removed only when ALL of the following are true:

- All agents exclusively use MCP JSON submission for artifact output. No XML
  file writes observed for 30 or more consecutive days in production.
- All five boundary modules (planning, development, review, fix, commit) have
  functioning JSON-first paths with converters.
- All integration tests pass in JSON-only mode (with XML fallback paths
  disabled or removed).
- Zero `xml_fallback_read` events recorded in production logs for 14 or more
  consecutive days.
- Manual review confirms no third-party tooling depends on the XML artifact
  files.

---

## 4. Rollback Procedure

If issues arise during or after the transition, the rollback procedure is:

1. **Revert JSON Schema validation priority**: Restore XML validation as the
   primary path. The JSON Schema files and converters can remain in the
   codebase but should not be the first-checked path.
2. **Restore ralph-prefixed XSD elements**: If any XSD elements were removed
   or renamed during the migration, restore them to their pre-migration
   state.
3. **Agent prompts**: No changes needed. Agent prompts still contain XML
   examples throughout the transition period. Only remove XML examples after
   the removal conditions above are fully met.
4. **Boundary modules**: Swap the check order in `extract_*` and `validate_*`
   methods so XML is checked first, or remove the JSON-first branches
   entirely.

The dual-mode design means rollback is a code-level change to boundary
modules only. No data migration is needed because both formats produce the
same domain types (`PlanElements`, `DevelopmentResultElements`, etc.).

---

## 5. Monitoring Checklist

### Counters to track

| Metric                  | Description                                        |
|-------------------------|----------------------------------------------------|
| `json_artifact_read`    | Count of successful JSON artifact reads per boundary |
| `xml_fallback_read`     | Count of XML fallback reads (JSON missing or failed) |
| `json_conversion_error` | Count of JSON-to-domain conversion failures         |
| `xml_validation_error`  | Count of XML validation failures                    |

### Alerts

- **After expected agent transition**: Alert on any `xml_fallback_read` event.
  This indicates an agent is still producing XML instead of JSON.
- **Conversion errors**: Alert on `json_conversion_error` spike. This may
  indicate a schema mismatch between what agents submit and what the
  converter expects.
- **Dual failures**: Alert when both JSON conversion and XML validation fail
  for the same artifact in a single pipeline run. This indicates a
  fundamental output problem.

### Dashboard queries (pseudocode)

```
# Daily JSON vs XML read ratio per boundary
SELECT boundary, source_type, COUNT(*)
FROM artifact_reads
WHERE timestamp > now() - interval '1 day'
GROUP BY boundary, source_type

# Consecutive days with zero XML fallback
SELECT MAX(streak_length)
FROM (
  SELECT date, SUM(CASE WHEN source_type = 'xml_fallback' THEN 1 ELSE 0 END) as xml_count
  FROM artifact_reads
  GROUP BY date
) AS daily
WHERE xml_count = 0
```

---

## References

- `ralph-workflow/src/reducer/boundary/json_artifact.rs` -- JSON-to-domain converters
- `ralph-workflow/src/reducer/boundary/planning.rs` -- dual-mode planning boundary
- `ralph-workflow/src/reducer/boundary/development.rs` -- dual-mode development boundary
- `ralph-workflow/src/reducer/boundary/run_review.rs` -- XML-only review boundary
- `ralph-workflow/src/reducer/boundary/run_fix.rs` -- XML-only fix boundary
- `ralph-workflow/src/reducer/boundary/commit.rs` -- XML-only commit boundary
- `docs/migration/xsd-to-json-schema-mapping.md` -- schema mapping reference
