# Developer Reference

This section is for contributors, maintainers, and integrators who need the internals behind Ralph Workflow's runtime, pipeline, prompts, or Python package surface.

If your job is simply to install, configure, and run Ralph Workflow, start with [Getting Started](getting-started.md), [CLI Reference](cli.md), and [Configuration](configuration.md) instead of this section.

## What lives here

- [Operator Reference](reference.md) stays focused on commands, config, and day-to-day lookup
- [Developer Internals](developer-internals.md) groups the maintainer-facing runtime and architecture pages, including Agents, MCP, Artifacts, Prompts, and Transcript
- [Configuration](configuration.md) covers operator-facing reference and the policy-migration background
- [Python API Reference](modules.rst) documents the public `ralph.*` package surface. The reference is integrated with hand-written bridge paragraphs under each top-level group (Top-Level, CLI, Config, Policy, Pipeline, Skills, Git, Phases, Agents, MCP, Pro Support) — see these anchor entries for the most-edited surfaces:
  - [`ralph.pipeline.runner`](modules.html#ralph.pipeline.runner) — the Ralph-loop run loop, the effect router, and the per-iteration state transitions
  - [`ralph.mcp.server`](modules.html#ralph.mcp.server) — the in-process MCP server, factory / lifecycle / runtime, and the `RestartAwareMcpBridge` contract
  - [`ralph.mcp.tools`](modules.html#ralph.mcp.tools) — the tool surface an agent sees (read_file / write_file / exec / git_read / websearch / webvisit / artifact submit / plan draft edit)
  - [`ralph.recovery.controller`](modules.html#ralph.recovery.controller) — the two-state recovery invariant (exponential backoff to the next agent, same-agent retry) and the never-exit-on-unavailability guarantee
- [Release & Versioning](versioning.md) covers release, tagging, and publishing workflow

## Dev build vs stable build

Contributors keep two parallel installs side by side. They never
collide because the dev build registers no global command and ships
under a different launcher name.

| Build | Command to run it | How to install / refresh | Tracks |
|---|---|---|---|
| **Dev build** | `rdev …` (anywhere) or `uv run ralph …` (from the repo) | `make install` | your working tree, live — no reinstall after edits |
| **Stable build** | `ralph …` (anywhere) | `make stable` | a pinned release, isolated via `uv tool` |

- **Dev build** — `make install` syncs the project's uv environment
  (editable project + dev extras) and writes an `rdev` launcher to
  `~/.local/bin/rdev`. From inside the repo you can also use
  `uv run ralph`. There is deliberately **no global `ralph`** for
  the dev build — the distinct `rdev` name is what keeps it from
  shadowing the stable one.
- **Stable build** — `make stable` runs
  `uv tool install --force --upgrade ralph-workflow`, putting an
  isolated `ralph` on your `PATH` (`~/.local/bin/ralph`),
  independent of the working tree. Re-running `make stable`
  **upgrades to the latest published release** if you are behind.
  To pin a specific version:

  ```bash
  python -m ralph.install --version 0.8.18   # or: uv tool install ralph-workflow==0.8.18
  ```

- **Switching** — type `rdev` for the dev build and `ralph` for the
  stable build. Verify which is which with:

  ```bash
  rdev --version   # -> working-tree version  (~/.local/bin/rdev)
  ralph --version  # -> stable release version (~/.local/bin/ralph)
  ```

> `uv` is required for both builds, and `~/.local/bin` must be on
> your `PATH`. Do not install the dev build as a global `ralph`
> (via pipx or `uv tool`) — it would shadow the stable one.

For the full dev-build workflow and the rationale behind the `rdev`
/ `ralph` split, see
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md).

## Related pages

- [CLI Reference](cli.md) — command surface and flags
- [Configuration](configuration.md) — runtime policy and config precedence
- [Versioning](versioning.md) — release and compatibility policy
- [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) — full contributor guide with the dev/stable build workflow
