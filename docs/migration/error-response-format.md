# Directive-Style Error Response Format for MCP Artifact Validation

> Design document for Task 2 of the XSD-to-MCP migration plan.
> These types are design sketches; implementation is deferred to T5.

---

## Error Response Structure

When artifact validation fails, the MCP server returns a structured error response
inside the JSON-RPC `error.data` field. The response contains one or more
`ValidationError` objects, each pinpointing a single field-level problem and
telling the agent exactly what to do next.

```json
{
  "errors": [
    {
      "code": "MISSING_FIELD",
      "field_path": "steps[0].title",
      "expected": "non-empty string",
      "got": null,
      "next_actions": ["Set steps[0].title to a non-empty string describing the step"],
      "prohibition": null
    }
  ],
  "artifact_type": "plan"
}
```

### Integration with JSON-RPC

The error response is carried in `JsonRpcError.data`. The outer `JsonRpcError`
uses code `-32602` (invalid params) with a short summary message. The `data`
field holds the full `ErrorResponse` object shown above.

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Artifact validation failed: 2 errors",
    "data": {
      "errors": [ ... ],
      "artifact_type": "plan"
    }
  },
  "id": 1
}
```

---

## Error Codes

Exactly 4 error codes. No others are permitted.

| Code | Meaning | When to use |
|---|---|---|
| `MISSING_FIELD` | Required field is absent or null | Field listed in `required` is missing, or value is `null` for a non-nullable field |
| `INVALID_ENUM` | Value not in allowed enum set | String value does not match any entry in the `enum` array |
| `TYPE_MISMATCH` | Value type does not match schema | Expected `string` but got `integer`, expected `array` but got `object`, etc. |
| `CONSTRAINT_VIOLATION` | Value violates a schema constraint | `minItems`, `maxItems`, `minLength`, `maxLength`, `minimum`, `maximum`, `pattern`, `additionalProperties` violations |

### Code selection rules

- If a field is missing entirely: `MISSING_FIELD`.
- If a field is present but `null` when the schema does not allow it: `MISSING_FIELD`.
- If a field is present with wrong JSON type: `TYPE_MISMATCH`.
- If a field is present with correct type but invalid enum value: `INVALID_ENUM`.
- If a field is present with correct type and not an enum, but fails a constraint: `CONSTRAINT_VIOLATION`.

---

## Field Path Format

Field paths use a JSON Path subset. No leading `$` or `.` prefix.

### Syntax

| Access type | Syntax | Example |
|---|---|---|
| Object property | `field` | `title` |
| Nested property | `parent.child` | `steps.title` (only when steps is an object) |
| Array element | `field[index]` | `steps[0]` |
| Combined | `field[index].property` | `steps[2].targets[0].path` |

### Rules

- Paths always start from the root of the artifact content (not the JSON-RPC envelope).
- Array indices are zero-based.
- No quoting of property names (property names must be valid identifiers).
- No wildcard or recursive descent operators.

### Examples

```
title                       -- root-level field
steps[0]                    -- first element of steps array
steps[0].title              -- title field of first step
steps[2].targets[0].path    -- path field inside first target of third step
```

---

## `next_actions` Format

The `next_actions` array contains 1 to 3 imperative sentences. Each sentence
tells the agent exactly what action to take.

### Rules

- Minimum 1 action, maximum 3 actions.
- Each action is a single imperative sentence (starts with a verb).
- Each action references the specific `field_path`.
- No hedging ("please", "try", "consider", "maybe").
- No apologies or pleasantries.
- Actions are specific: include concrete values, valid options, or exact constraints.

### Examples by error code

**MISSING_FIELD:**
```json
["Set steps[0].title to a non-empty string describing the step"]
```

**INVALID_ENUM:**
```json
[
  "Set steps[1].action to one of: create, modify, delete, verify",
  "Remove the current value \"update\" which is not a valid option"
]
```

**TYPE_MISMATCH:**
```json
["Change steps[0].number from string \"1\" to integer 1"]
```

**CONSTRAINT_VIOLATION:**
```json
[
  "Add at least 1 item to steps (current count: 0)",
  "Each plan must contain a non-empty steps array"
]
```

---

## `prohibition` Format

The `prohibition` field is optional (`null` when not applicable). When present,
it tells the agent what NOT to do -- preventing common repeat mistakes.

### Rules

- Single imperative sentence starting with "Do not".
- Specific to the error, not generic advice.
- Used only when there is a known pattern of agents repeating the same mistake.

### Examples

```json
"Do not use $ref or oneOf in the schema"
"Do not wrap the value in an array"
"Do not set number to a string; it must be an integer"
```

### When to include a prohibition

- Agent previously made the same mistake in this session (retry scenario).
- The error involves a weak-model anti-pattern (see `weak-model-json-schema-conventions.md`).
- The correct fix is non-obvious and the wrong fix is common.

When none of these apply, set `prohibition` to `null`.

---

## Design Principles

1. **Machine-parseable**: Structured JSON with typed fields. No prose-only messages.
2. **Directive-style**: Every error tells the agent what to DO, not what went wrong.
3. **Field-path always present**: Every validation error references a specific field path.
4. **No human-facing language**: No "please", "try again", "sorry", "invalid input".
5. **No oneOf in error schema**: The error format itself follows the weak-model conventions.
6. **Bounded**: At most 3 next_actions per error. At most 1 prohibition.
7. **Idempotent**: The same input always produces the same set of errors.

---

## Rust Types

Proposed type definitions for T5 implementation. These are design sketches, not
final code.

```rust
/// Error codes for artifact validation failures.
/// Exactly 4 variants -- no others are permitted.
enum ErrorCode {
    MissingField,
    InvalidEnum,
    TypeMismatch,
    ConstraintViolation,
}

/// A single field-level validation error with directive-style recovery actions.
struct ValidationError {
    /// Which category of validation failure occurred.
    code: ErrorCode,
    /// JSON-path-subset pointing to the offending field (e.g. "steps[0].title").
    field_path: String,
    /// What the schema expected (e.g. "non-empty string", "integer >= 1").
    expected: String,
    /// What was actually found, or None if the field was absent.
    got: Option<String>,
    /// 1-3 imperative sentences telling the agent what to do. Never empty.
    next_actions: Vec<String>,
    /// Optional imperative sentence telling the agent what NOT to do.
    prohibition: Option<String>,
}

/// Complete validation error response for an artifact submission.
/// Carried in JsonRpcError.data when validation fails.
struct ErrorResponse {
    /// One or more validation errors found in the artifact.
    errors: Vec<ValidationError>,
    /// The artifact type that was being validated (e.g. "plan", "review").
    artifact_type: String,
}
```

### Serialization

`ErrorCode` serializes to `SCREAMING_SNAKE_CASE` strings to match the JSON
representation:

| Variant | JSON value |
|---|---|
| `MissingField` | `"MISSING_FIELD"` |
| `InvalidEnum` | `"INVALID_ENUM"` |
| `TypeMismatch` | `"TYPE_MISMATCH"` |
| `ConstraintViolation` | `"CONSTRAINT_VIOLATION"` |

This will use `#[serde(rename_all = "SCREAMING_SNAKE_CASE")]` on the enum.

### Invariants enforced at construction time

- `next_actions` must contain 1-3 elements.
- `field_path` must be non-empty.
- `expected` must be non-empty.
- `errors` in `ErrorResponse` must be non-empty.
