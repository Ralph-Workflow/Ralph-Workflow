# Type-Checking Suppression Policy

Suppressions in this project are configuration-level. The gate
`ralph.testing.audit_typecheck_bypass` inspects the mypy configuration files
and fails when a configuration weakens type checking. It runs in
`make verify`.

## Configuration the gate rejects

Every `mypy*` section of `mypy.ini` and the `[tool.mypy]` table of
`pyproject.toml` are inspected.

| Key | Rejected value |
| --- | --- |
| `ignore_missing_imports` | `true` |
| `follow_imports` | `silent` |
| `ignore_errors` | `true` |
| `disable_error_code` | any non-empty value |
| `warn_unused_ignores` | `false` |
| `disallow_untyped_defs` | `false` |
| `exclude` | any value |

## Source-level suppressions

Tracked Python sources carry no inline suppression directives. The absolute
comment gate `ralph.testing.audit_comments` fails on every comment token in
tracked Python, so a suppression can only be expressed in tool configuration.

## Type assertions

`typing.cast` is governed by `docs/ralph-workflow-policy/typechecking-policy.md`
and enforced by `ralph.testing.audit_cast_policy`. A cast is not a substitute
for a suppression, and a suppression is not a substitute for a cast.

## Running the gate

```
uv run --locked --project . python -m ralph.testing.audit_typecheck_bypass
```
