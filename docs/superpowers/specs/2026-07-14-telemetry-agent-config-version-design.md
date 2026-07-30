# Telemetry: agent-config snapshot + version/policy-schema on the session

Date: 2026-07-14
Status: implemented

## Problem

Sentry sessions carried OS/arch/runtime markers and aggregate phase/agent
invocation counters, but nothing that describes **how the run was configured**.
When a session looks wrong, there is no way to ask "which agent, which model,
which policy schema produced this?" Ralph's version reached Sentry only as the
`release` and a global tag, so a session inspected in isolation was not
self-describing.

## What is forwarded

### 1. `agent_config` context (new)

Set once at the pipeline config-load chokepoint, so it rides on **every**
subsequent event — including crashes — rather than only a cleanly finalized
session.

Per agent, reduced by `ralph/telemetry/_agent_config_payload.py`:

| Field | Treatment |
|---|---|
| dict key (user-authored agent name) | **dropped**; re-keyed by transport family, `_2`/`_3` on collision |
| `transport` | closed vocabulary, else `generic` |
| `model` | **verbatim** (e.g. `zai-coding-plan/glm-5.2`) |
| `cmd` | reduced to `argv[0]` basename, and only if a known agent binary; else `custom` |
| `json_parser`, `can_commit`, `subagent_capability` | verbatim (enum / bool) |
| the seven `*_flag` fields | reduced to presence booleans; flag **values** never leave |

Entries capped at 32; the true size is still reported via `agent_count`.
Tags: `agent_count`, `agent_families`.

**Model IDs are forwarded deliberately.** They are product identifiers, not PII,
and they are the single most useful debugging dimension.

**But `model` is free text, so it is shape-validated first.** An independent
privacy audit confirmed the hole: local-model and proxy workflows (ollama,
llama.cpp, vLLM, LiteLLM) legitimately put a filesystem path or a credentialed
endpoint in that field — `/home/jane/acme/secret-ft.gguf`,
`http://user:pw@llm.internal/v1/m`. The `before_send` scrubber does **not**
save us: it rewrites only the home/cwd/argv *prefix*, so the org name, the
directory structure, and any inline password survive. A model value is therefore
forwarded only when it looks like an identifier (dotted/hyphenated segments, at
most two `/` separators, no scheme, credential, whitespace, or backslash, ≤96
chars). Everything else — every absolute path, every URL — collapses to
`custom`. An unset model stays `None`, so "not configured" remains
distinguishable from "rejected".

**`cmd` is deliberately NOT forwarded.** It can embed absolute paths, wrapper
scripts, and env prefixes carrying API keys.

### 2. Versions on the session context

`finalize_session()` adds `ralph_version` and `python_version` to the `session`
payload, making a session self-describing without cross-referencing the release.

### 3. `policy_schema_state` tag

`markers.SCHEMA_VERSION` describes the *installed* Ralph and is already implied
by `ralph_version`. The useful fact is whether the **project's** policy pack is
current, so `ralph/project_policy/schema_state.py` classifies it as
`current` / `outdated` / `absent` / `unknown` by reading only the first
non-empty line (the schema marker) of each policy file. No file content is
retained.

## Architecture note: why the derivation does not live in telemetry

The first cut imported `ralph.project_policy` from `_sentry.py`. That added
~35ms to an import path loaded by `pipeline/runner.py`, on a suite with a 1s
per-test budget. `ralph/telemetry/` is also not exempt from ruff's `PLC0415`,
so the import could not be made lazy.

Instead the policy layer owns the derivation (`policy_schema_state()`), the CLI
calls it, and telemetry stays a **pure sink**: `set_policy_schema_context(state)`
validates the string against a closed vocabulary and collapses anything else to
`unknown`. Telemetry gains no new dependency and no import cost. The vocabulary
is restated in `_sentry.py` to keep that import graph clean; a drift-guard test
pins the two definitions together.

## Testing

`tests/test_telemetry_sentry.py`: name-dropping and `cmd` reduction, flag→boolean
reduction, model passthrough, same-family slug disambiguation, the entry cap vs.
true count, the opt-out no-op, the four `policy_schema_state` branches,
out-of-vocabulary rejection, the vocabulary drift guard, and the version fields
on the finalize payload.
