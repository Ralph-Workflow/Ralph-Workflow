<!-- ralph-policy-schema: v4 -->
<!-- ralph-policy-id: execution-kernel.md -->

# Execution Kernel

## Purpose and scope

This file is the shared core that every domain policy file in
`docs/ralph-workflow-policy/` and every starter points to. The kernel
carries the execution rules; each domain file keeps its own
`RALPH-FACT:` / `RALPH-COMMAND:` declarations and adds only domain
outcomes, facts, triggers, and exceptions. No domain file repeats the
kernel's execution rules.

## Ralph markers

Every policy file starts with these two markers as the literal first
non-blank lines:

```
<!-- ralph-policy-schema: v4 -->
<!-- ralph-policy-id: <filename.md> -->
```

The current schema marker is `<!-- ralph-policy-schema: v4 -->`. Both
markers MUST appear verbatim before any heading or content.

## Precedence

* The kernel wins when a domain file's wording conflicts with it.
* A domain file MAY add a stricter rule than the kernel (see
  Local-tightening review below) but MUST NOT relax a kernel rule.
* When a domain file and the kernel both cover the same situation,
  the kernel rule is the floor; the domain rule is the ceiling.

## Loading direction

* Agents MUST NOT load every policy file by default.
* Load order: kernel → domain file(s) whose change actually fires the
  rule → stop.
* The verification entry point (`make -C ralph-workflow verify`) is the
  authoritative loader, not which files an agent has read.
* A change that does not fire a domain file's trigger does not require
  that file to be loaded.

## Shared execution rules

These rules previously appeared verbatim in every domain file's "AI
execution instructions" section. They live here exactly once.

* **Truthful evidence**: a claim of compliance, a recorded finding,
  a test result, or a gate outcome MUST be reproducible from the inputs
  the change actually executed.
* **Run every gate**: run every `RALPH-COMMAND:` gate declared under
  the affected policy's Verification section before claiming
  compliance; report the actual outcome (exit code, error, real
  timing).
* **No weakening**: do NOT weaken a gate to obtain a passing result.
  Weakening moves include `# noqa`, inline allowlist entries,
  per-file ignores, raised timeouts, removed assertions, and
  refactored-out failure paths.
* **Policy follows reality**: update policy facts/commands/requirements
  in the same workflow that changes the underlying tool, gate,
  exclusion, or behaviour.
* **Report failures honestly**: a red gate hidden behind "this is
  unrelated" is itself a failure.

## Local-tightening review

A domain file may add a stricter rule than the kernel. Every local
tightening MUST record inline:

* **Owner** — who is accountable for the tightening.
* **Protected outcome** — what specific risk or regression the tightening
  prevents.
* **Retirement/review trigger** — the condition or date that re-opens
  the tightening for review.

An ownerless tightening, a tightening with no protected outcome, or a
tightening with no trigger is a retirement candidate.

## What the kernel does NOT do

* Does not list domain-specific rules.
* Does not approve audits, gates, or tools.
* Does not grant permissions.
* Does not override the verification entry point (`make -C ralph-workflow
  verify`).
