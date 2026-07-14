# Telemetry identity and lifecycle design

Status: approved (2026-07-14)

## Goal

Make Ralph Workflow's metadata-only Sentry telemetry reliable enough to count
active installations and understand workflow usage without sending project,
prompt, path, model, or user-defined configuration names.

The change must provide one persistent random user identifier per operating
system account, even when the same person runs Ralph from terminal applications
with different `XDG_CONFIG_HOME` values. It must also make session timing and
agent usage queryable by safe pipeline and drain classifications.

## Current gaps

- The user identifier follows `XDG_CONFIG_HOME`. Two terminals with different
  values can therefore represent one operating-system user as two users.
- Two processes starting before the identity file exists can generate different
  identifiers and return different values even though only one value remains on
  disk.
- Session telemetry has monotonic start, end, and duration values plus coarse
  UTC start buckets, but no explicit UTC start and end values.
- Phase-role totals exist, but there is no logical agent-invocation metric that
  binds the safe agent family, pipeline profile, phase role, and drain class.

## Non-goals

- No hardware-, login-, hostname-, repository-, or machine-derived identifier.
- No raw custom agent, chain, phase, pipeline, drain, model, prompt, path, or
  environment-variable value in telemetry.
- No attempt to report a clean end for `SIGKILL`, power loss, or an operating
  system crash. A recorded start without an end represents an abandoned run.
- No tracking of individual low-level subprocess retries. An agent invocation
  is one logical `InvokeAgentEffect`; fallback agents are separate logical
  invocations.
- No telemetry upload when either supported opt-out mechanism is active.

## Approach decision

Use a canonical, random, file-backed identity and structured Sentry lifecycle
metrics.

Alternatives rejected:

1. Keep one identity per XDG profile and copy it opportunistically. This leaves
   identity dependent on which terminal runs first and cannot guarantee a
   single value when profiles are isolated.
2. Derive an identifier from machine or account attributes. This avoids a file
   but creates an unnecessary fingerprinting surface and behaves poorly when
   hardware or account attributes change.

## Persistent user identity

### Canonical location and migration

The canonical identity file is always
`~/.config/ralph-workflow-user.ini`, resolved from `Path.home()` and independent
of `XDG_CONFIG_HOME`. The explicit `config_dir` test/dependency-injection
argument remains authoritative when supplied.

Resolution order is:

1. Return a valid identifier from the canonical file.
2. If the canonical file is absent or invalid, read the former
   `$XDG_CONFIG_HOME/ralph-workflow-user.ini` location when it is different.
   Migrate a valid identifier to the canonical file and return the same value.
3. Otherwise generate one cryptographically random identifier, persist it, and
   return it.

The canonical value wins when both locations contain valid but different
identifiers. Migration never rewrites a valid canonical value. The legacy file
is left in place because deleting user configuration is unnecessary and makes
rollback less safe.

### Concurrent creation

Identity initialization uses a bounded cross-process critical section and an
atomic file replacement:

- acquire an adjacent `.lock` file using an atomic exclusive-create operation;
- while holding the lock, repeat the resolution order because another process
  may have published the identity;
- write complete content to a private temporary file in the same directory;
- flush it and atomically replace the canonical path;
- remove the temporary file and release the lock in `finally` blocks.

Lock acquisition waits for at most 500 milliseconds, polling every 10
milliseconds through an injected waiter. A lock older than 30 seconds is stale
and may be removed before contenders retry the same atomic acquisition. If the
lock cannot be acquired, Ralph rereads the canonical file; if no valid identity
is available, telemetry initialization fails soft instead of returning an
unpersisted identifier. This protects unique-user counts and does not block the
host command indefinitely.

The temporary identity file is created with mode `0600` on POSIX; Windows uses
the account permissions supplied by the operating system. Parse errors and
malformed identifiers are treated as invalid identity state and repaired under
the same critical section.

## Session lifecycle

Each enabled CLI process keeps a fresh random session identifier. Session start
records:

- the anonymous user identifier through Sentry's user field;
- the session identifier as a tag;
- a UTC start time rounded to whole seconds;
- the monotonic start value used only to calculate duration;
- the privacy-safe CLI command classification.

Session finalization records:

- UTC end time rounded to whole seconds;
- non-negative duration in seconds from the monotonic clock;
- `success`, `failure`, `interrupted`, or `unknown` outcome;
- the existing phase-role aggregates;
- bounded agent-invocation aggregates.

The start is emitted as a standalone metadata-only Sentry event so an abrupt
termination remains observable as a start without a matching end. Normal
shutdown emits the end event, finishes the transaction, ends the Sentry
session, and performs the existing bounded flush. Repeated finalization is
idempotent and cannot emit duplicate end records.

All clocks remain injectable. Tests use fake values and perform no sleeps.

## Pipeline, drain, and agent classifications

Every logical agent invocation emits count and duration metrics with the same
bounded attributes:

- `agent_family`: a fixed mapping from the resolved `AgentTransport`; the
  generic transport maps to `custom`;
- `transport`: the closed `AgentTransport` value;
- `pipeline_profile`: `default` when the effective policy matches the bundled
  pipeline, otherwise `custom`;
- `phase_role`: the closed `PhaseRole` value;
- `drain`: an allowlisted bundled drain identifier, otherwise `custom`;
- `drain_class`: the validated closed drain-class/phase-role value, otherwise
  `unknown`;
- `outcome`: `success`, `failure`, `interrupted`, or `crashed`.

The pipeline classifier compares validated effective policy with the validated
bundled default rather than forwarding a filename or user-defined policy name.
The agent classifier uses only the closed transport enum. The drain classifier
uses an explicit allowlist derived from bundled configuration. Neither infers
safety from string shape.

Instrumentation begins immediately before executing an `InvokeAgentEffect` and
records in a `finally` path so failures are visible. A logical invocation may
contain internal retries, but it contributes one count and one duration. When a
fallback selects another agent, the new effect contributes another invocation.

The Sentry metrics are:

- `ralph.agent.invocation`, a counter with the classifications above;
- `ralph.agent.duration`, a seconds distribution with the same attributes;
- the existing `ralph.session` and `ralph.session.duration` metrics, extended
  only with safe session-level classifications.

The final session context contains bounded aggregate counts, not an unbounded
list of invocation records. Any aggregate key space is capped explicitly and
uses an overflow bucket.

## Privacy and failure behavior

- Telemetry initialization still checks environment and configuration opt-outs
  before generating an identifier, creating a file, or calling Sentry.
- Exact UTC times are operational metadata. They are rounded to seconds and are
  never combined with timezone, hostname, account, or repository data.
- Existing event scrubbing, disabled local-variable capture, disabled automatic
  integrations, and disabled profiling remain unchanged.
- Classification rejects unknown raw values rather than forwarding them.
- Every telemetry operation remains fail-soft and must not alter the CLI or
  pipeline result.

## Test strategy

Tests exercise public behavior with injected paths, clocks, and Sentry
recorders:

- repeated calls and separate simulated terminal environments return the same
  canonical user identifier;
- a valid legacy XDG identity migrates without changing value;
- a valid canonical identity wins over a conflicting legacy identity;
- concurrent first-use callers all return the one persisted identifier;
- malformed files, stale locks, bounded lock failure, atomic-write cleanup, and
  owner-only permissions follow the fail-soft contract;
- opt-out performs no identity read/write and emits no Sentry calls;
- session start/end UTC values, duration, outcome, idempotent finalization, and
  abandoned-start behavior are deterministic;
- built-in values pass through while every custom agent, pipeline, and drain
  name is classified without appearing in captured payloads;
- success and exception paths both emit one logical invocation with the correct
  duration and safe dimensions;
- telemetry exceptions never change the pipeline event or exit code.

After focused red-green cycles, run the complete `make -C ralph-workflow verify`
gate within its immutable test budget.

## Documentation and migration notes

Update the operator configuration reference and the generated identity-file
comment to name the canonical location and explain one-time migration. Update
the changelog with the user-visible persistence and telemetry-schema changes.
Do not duplicate the full telemetry schema in the README.

Documentation review note:

- This specification records the approved engineering contract and belongs in
  the existing design-specification surface.
- User-facing behavior will be documented in the operator reference, where
  telemetry configuration already lives.
- The README is explicitly left alone to avoid duplicating reference details.
- The route remains unchanged; no new public navigation entry is needed.

## Expected implementation areas

- `ralph/telemetry/_user_identity.py`: canonical resolution, migration, locking,
  atomic persistence, and validation.
- `ralph/telemetry/_sentry.py`: idempotent session times, start/end events,
  safe classifiers, invocation metrics, and bounded aggregates.
- `ralph/cli/main.py`: lifecycle wiring without changing opt-out ordering.
- `ralph/pipeline/runner.py` or the agent-effect execution seam: logical
  invocation timing and outcome wiring.
- Telemetry identity, Sentry, CLI wiring, and pipeline black-box tests.
- Operator configuration documentation and changelog.
