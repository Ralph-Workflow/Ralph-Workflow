# Recovery

> **New to Ralph Workflow?** Start with [Getting Started](getting-started.md) first. This page explains how Ralph Workflow behaves when a run is interrupted or something fails.

Ralph Workflow treats recovery as a built-in part of the product, not an afterthought. In normal use, you do not need to turn anything on. Ralph Workflow retries transient failures, keeps checkpoints up to date, and can resume safely after interruptions.

## The practical model

Most users only need to know three things:

- Ralph Workflow can retry some failures automatically
- Ralph Workflow writes checkpoints so a run can resume later
- you choose whether to resume from a saved checkpoint or start fresh

## Failure categories

Every failure is classified into one of four categories:

| Category | Description | Counts against budget? |
|----------|-------------|----------------------|
| `environmental` | Network outage, upstream service error, transport disconnect | No |
| `agent` | Empty output, idle timeout, malformed tool calls, repeated policy violations | Yes |
| `user_config` | Invalid config, unbound agent chain, missing required inputs | No |
| `ambiguous` | Ralph Workflow cannot confidently determine the cause | No |

The goal is simple: transient infrastructure problems should not burn the same budget as genuine agent failures.

## Offline detection and auto-resume

Ralph Workflow monitors connectivity. While offline, the run pauses instead of burning budget. When connectivity returns, Ralph Workflow resumes automatically.

You will see messages like:

```
Offline — paused (since HH:MM:SS)
```

and later:

```
Recovery resumed after offline
```

## Two-SIGINT behavior

- **First Ctrl+C** — cancel in-flight work, shut down in order, save the checkpoint, and pause
- **Second Ctrl+C** — exit immediately without waiting for cleanup

## Retry and fallover

Each phase uses an agent chain. If one agent exhausts its retry budget, Ralph Workflow can fall over to the next configured agent.

That is how longer unattended runs stay moving without being pinned to one provider.

## Recovery-cycle cap

`[general].max_cycles` limits how many full fallback cycles Ralph Workflow will attempt before stopping.

```toml
[general]
max_cycles = 3
```

This prevents a persistently failing workflow from retrying forever without making progress.

## Checkpoints

Ralph Workflow saves a checkpoint after each successful phase transition so a run can continue from the last completed step after an interruption or crash.

### Where the checkpoint lives

```
.agent/checkpoint.json
```

### What is stored

Typical checkpoint information includes:

- the current phase
- references to needed handoff artifacts
- consumed iteration counts
- the last error, when present

### Useful checkpoint commands

```bash
ralph --resume
ralph --inspect-checkpoint
ralph --no-resume
```

- `ralph --resume` — continue from the saved checkpoint
- `ralph --inspect-checkpoint` — show what would be resumed
- `ralph --no-resume` — ignore the checkpoint and start fresh

### Enabling or disabling checkpoints

```toml
[general.workflow]
checkpoint_enabled = true
```

When `checkpoint_enabled = false`, Ralph Workflow stops writing checkpoints and every run starts from the beginning.

## When to read the deeper internals

Most operators do not need the lower-level liveness rules, watchdog thresholds, or session-safety edge cases. If you are debugging those details specifically, use the maintainer-oriented architecture docs.

## Related pages

- [Concepts](concepts.md) — phase, drain, checkpoint, and recovery terminology
- [Troubleshooting](troubleshooting.md) — common recovery-related issues and fixes
- [Parallel Mode](parallel-mode.md) — recovery behavior in same-workspace parallel runs
- [Developer Reference](developer-reference.md) — deeper implementation detail
