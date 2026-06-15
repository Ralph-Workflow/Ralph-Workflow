---
orphan: true
---

# Agents

Ralph Workflow can supervise multiple coding agents, but the contract stays the same: the workflow is built for **unattended** orchestration that still comes back reviewable in the morning.

## Supported agents

Ralph Workflow currently supports **Claude**, **Codex**, **OpenCode**, **Nanocoder**, and **Google Anti Gravity** as orchestration targets. Each runs under the same unattended workflow contract described on this page. For help choosing, see [Which Agent Should I Start With?](which-agent-should-i-start-with.md).

> See `ralph/skills/_agent_paths.py` for the canonical mapping of every supported agent's user-global skill-discovery root.

Every supported agent has a manual smoke entry-point for live end-to-end verification against the real binary. Claude is verified with `python -m ralph smoke-interactive-claude` and AGY is verified with `python -m ralph smoke-interactive-agy`. Run the corresponding command on Linux or macOS to confirm the transport, MCP wiring, and tool invocation pipeline produce real output.

### Google Anti Gravity (AGY)

AGY is discovered from `PATH` like any other agent. Set `RALPH_AGY_BINARY` to point at a custom executable or at the deterministic mock at `tests/_support/mock_agy.sh` for CI. The mock simulates AGY v1.0.8's measured wire format and is the supported path for proving real output end-to-end without a live account. The mock entrypoint is `tests/_support/mock_agy.py` (run as `python -m tests._support.mock_agy`); `tests/_support/mock_agy.sh` is a thin shell wrapper suitable for `RALPH_AGY_BINARY`.

The canonical display names accepted by `agy models` are the only valid `--model` values; lowercased or dashed slugs are rejected by the upstream binary. The eight canonical names are:

- `Gemini 3.5 Flash (Medium)`
- `Gemini 3.5 Flash (High)`
- `Gemini 3.5 Flash (Low)`
- `Gemini 3.1 Pro (Low)`
- `Gemini 3.1 Pro (High)`
- `Claude Sonnet 4.6 (Thinking)`
- `Claude Opus 4.6 (Thinking)`
- `GPT-OSS 120B (Medium)`

Use `ralph --check-mcp` to validate AGY transport compatibility before the first run.

## Project-local skills

In addition to the user-global skill bundle, every `ralph` run auto-seeds a project-local skill fan-out so the same baseline is available to every supported agent at the project scope (not just in your user home).

### Canonical+symlinks design

`.opencode/skills/` is the single source of truth. The 3 project-scope sibling roots (`./.claude/skills/`, `./.codex/skills/`, `./.gemini/antigravity-cli/skills/`) are symlinks into it. OpenCode is intentionally absent as a project sibling because the project canonical `./.opencode/skills/` IS the opencode project root, so it would be a self-symlink. The user-global opencode root at `~/.config/opencode/skills/` is covered by the user-global install.

Self-improving skills are not yet implemented — see the future-extension sketch below and the `# FUTURE: self-improving skills hook goes here` comment at the END of `install_project_baseline_skills` in `ralph/skills/_installer.py`.

### Auto-seed behavior

On every `ralph` run, missing project skills AND the batteries-included `.gitignore` (see `_DEFAULT_GITIGNORE_PATTERNS` in `ralph/config/bootstrap.py`) are auto-seeded when missing. Re-running is idempotent. Use `ralph --force-init-skills` to force a full re-resolve even when valid installs exist. If a project-scope install reports a conflict (NEEDS_REPAIR), `_sync_shipped_skills_on_pipeline_run` surfaces `ralph --force-init-skills` as the remediation hint on a non-DEBUG channel so the user actually sees it.

### User-global update policy

On a normal `ralph` run, outdated **user-global** baseline skills (e.g. `~/.claude/skills/`, `~/.codex/skills/`, `~/.config/opencode/skills/`, `~/.gemini/antigravity-cli/skills/`) are **NOT auto-repaired**. `SkillManager.check_skills_for_updates()` records `update_available=True` in capability state but never mutates the user-global canonical root or any sibling symlink. The run prints a `ralph --force-init-skills` hint on a non-DEBUG channel so the user knows how to apply the update. Only an explicit `ralph --force-init-skills` (or `ralph --init`) invocation overwrites the user-global canonical or sibling symlinks.

This split — project-scope artifacts are auto-seeded, user-global artifacts are hint-only — matches the prompt's "we don't update it unless the person runs --force-init-skills" contract.

### Customization contract

Editing the SKILL.md under `./.opencode/skills/` propagates to all sibling symlinks. Editing a sibling symlink's target directly is a no-op. The `.ralph-managed.json` marker protects user-edited content from being overwritten.

### Code citations

- `ralph/skills/_agent_paths.py` — `project_skill_root` and `project_sibling_skill_roots`
- `ralph/skills/_installer.py` — `install_project_baseline_skills`, `_project_skills_need_install`
- `ralph/cli/commands/run.py` — `_sync_shipped_skills_on_pipeline_run`

### Self-improving skills (future)

The TODO lives at the end of `install_project_baseline_skills` in `ralph/skills/_installer.py`. The future design is a hook called after every `ralph` run that lets agents write back improvements to `./.opencode/skills/<name>/SKILL.md`, with a prompt-confirmation gate to prevent runaway mutations. The hook entry point lives at `ralph/skills/_installer.py:self_improving_skills_hook` and is called from `install_project_baseline_skills` after every successful project fan-out. The default body is a no-op; the future implementation will gate mutations on a prompt-confirmation step.

**Scope constraint:** the hook fires only on the project-scope fan-out (`install_project_baseline_skills`); the user-global install path (`install_baseline_skills`, invoked by `ralph --init` and `ralph --force-init-skills`) is intentionally NOT wired in this iteration to avoid silently mutating the user's home directory (e.g. `~/.claude/skills/`). Project-scope mutations are reversible (delete `./.opencode/skills/`, re-run `ralph`); user-global mutations are not.

## Supported-agent research contract

Every `AgentSkillRoot` in `ralph/skills/_agent_paths.py` carries a `source_url` and a `last_verified_iso` (YYYY-MM-DD). The audit in `tests/test_skills_agent_paths_research.py` pins the contract for user-global entries. `ProjectAgentSkillRoot` (in the same file) mirrors the same contract for project-scope entries; `tests/test_skills_project_paths_research.py` pins it. Future maintainers MUST bump `last_verified_iso` in the same commit that changes `source_url` or `path_segments`, citing the re-verification source.

## What this page is for

This page explains how Ralph Workflow orchestrates agent sessions, what completion means, and why interactive and headless transports make different tradeoffs.

## The unattended orchestration contract

Ralph Workflow does not treat an agent transcript as proof that the work is done. It supervises each session, orchestrates the configured phases, and looks for concrete completion evidence before handing control back.

That evidence comes from:

- **artifact** output that shows what was produced for the phase
- explicit tool or MCP signals such as `declare_complete`
- verification and review steps that confirm the handoff is not just a confident draft

If an agent exits **without completing** the phase, Ralph Workflow treats that as **incomplete** work rather than silently calling it done. The session can be resumed, retried, or routed through the next recovery path depending on the configured policy.

## Interactive vs headless modes

Interactive transports give Ralph Workflow better streaming **observability** into what the agent is doing during a live session. Headless transports can be simpler to automate, but the tradeoff is less step-by-step visibility while the run is in flight.

That tradeoff matters most when you want stronger supervision of a long-running interactive coding session. Ralph Workflow can still manage either mode, but the operational visibility differs.

## Completion and parser behavior

Completion is evaluated from durable evidence, not from a conversational vibe. Parsers may produce **bounded summaries** of what happened, but they do not preserve every multimodal parser output as first-class artifacts in the final event stream.

In practice, Ralph Workflow expects either:

- phase artifacts that show the result
- an explicit `declare_complete` call
- or a recovery path when the session ends before either condition is met

## Resolved capability delivery

Multimodal delivery is decided per session through `ResolvedCapabilityProfile`, which acts as the pre-computed, session-owned contract for how each modality is delivered to the active agent transport.

That keeps media, artifacts, and tool output aligned with the capabilities of the current session instead of assuming one fixed behavior for every provider.

## Dedicated parallel worker bootstrap

Ralph-managed parallel worker bootstrap is dormant in the bundled default (see [Parallel Mode](parallel-mode.md)). This section documents the opt-in contract for the `ralph_fan_out` dispatch mode.

When Ralph Workflow fans out parallel workers for a multi-unit execution, each worker enters through a dedicated bootstrap path that short-circuits the shared pipeline startup loop.

### What each worker receives

Each parallel worker gets its own isolated execution context:

- **Work-unit manifest** — serialized at `.agent/workers/<unit_id>/worker-manifest.json` before launch, containing the unit description, allowed directories, phase, drain, and the parent run's config path and CLI overrides
- **Worker-local prompt dump** — rendered prompt written to `.agent/workers/<unit_id>/tmp/<phase>_prompt.md` instead of the shared `.agent/tmp/` location
- **Worker-local checkpoint** — saved to `.agent/workers/<unit_id>/tmp/checkpoint.json` instead of `.agent/checkpoint.json`
- **Worker-local system prompt and current-prompt mirror** — materialized under the same worker namespace, keeping the worker's view of PROMPT.md and system prompt isolated from other workers
- **Worker-local multimodal sidecar** — handoff metadata written to `.agent/workers/<unit_id>/tmp/<phase>_multimodal_handoff.json`

### Why isolation matters

The old bootstrap path launched workers as generic `python -m ralph` invocations that entered the shared pipeline loop and competed for singleton runtime files. The dedicated bootstrap path bypasses that loop entirely and threads the work-unit context through the manifest so each worker operates on its own state.

Post-fanout verification remains serialized — Ralph Workflow waits for all workers to finish before running the single verification step, but the workers themselves execute in parallel with no shared state to corrupt.

### Bootstrap entry point

Workers launched via fan-out receive the manifest path through the hidden `--parallel-worker-manifest` CLI option. The worker runtime loads the manifest, reconstructs the work-unit context, materializes the prompt for the unit, and executes the phase without re-entering the outer pipeline loop.

## Related pages

- [Developer Internals](developer-internals.md)
- [MCP Architecture](mcp-architecture.md)
- [Artifacts](artifacts.md)
- [Transcript and Display Reference](transcript.md)
