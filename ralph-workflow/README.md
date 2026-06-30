<!--
  Review note (docs rubric §"Required review note for meaningful docs changes"):
  - What changed: trimmed manual-depth technical detail from this PyPI
    README so it stays a storefront rather than a manual. The Idle
    watchdog four-channel-evidence section (channel list, default knob,
    `agent_idle_activity_evidence_ttl_seconds`) and the Pro-support
    "Pipeline dependency injection" subsection (PipelineDeps bundle,
    factory references, per-collaborator role narration) were both
    demoted to one-to-two-line summaries that defer to the Sphinx
    manual via link. The autopilot-first H1/tagline, the canonical
    value-prop sentence, the verifiable download stat (10,700+ lifetime
    PyPI downloads · 4,000+ in the last 30 days, pepy.tech, 2026-06-12),
    and the supported-agents table are preserved unchanged.
  - Why it belongs here: this file is the PyPI-facing README
    (`[project] readme = "README.md"` in pyproject.toml). PyPI readers
    want to know what the package is, whether it fits, and how to
    install it before they read about operator/security concerns, and
    they should hear the same autopilot story they would see on the
    top-level README.
  - What was pruned, merged, or explicitly left alone: the rubric-compliant
    download stat and the per-agent supported-agents table are preserved;
    the Idle-watchdog and Pipeline-DI sections were the two longest
    manual-depth subsections on the page and are now Sphinx-link defers.
    The MCP server trust boundary stays in the "Trust and safety" section.
    The "composable loop framework" phrasing is preserved as a
    secondary descriptive clause.
  - How duplication was reduced or contained: the install block already
    lives in the top-level README — this page repeats it once, in the
    canonical sequence (install → first-run), and then defers all
    deeper documentation to the operator manual instead of restating
    it. The canonical value-prop sentence is shared verbatim with both
    START_HERE files and the Sphinx index so all four surfaces reinforce
    the same story.
  - How the route is clearer now than before: what-it-is → who-it's-for →
    install-and-run → supported-agents → what-a-run-leaves-you →
    documentation → fit-or-not-fit → watchdog-pointer → privacy →
    community → trust-and-safety → development-and-verification →
    pro-support-pointer. Manual-depth technical detail is now reached
    only via Sphinx links, never duplicated on this page.
-->

# Ralph Workflow — the autopilot for coding agents

> **Codeberg is primary.** Star, watch, fork, and report issues there first:
> <https://codeberg.org/RalphWorkflow/Ralph-Workflow> (verify: repo-exists)
> GitHub is a read-only mirror:
> <https://github.com/Ralph-Workflow/Ralph-Workflow> (verify: repo-exists)

Ralph Workflow is **the autopilot for coding agents** — a free and
open-source operating system for autonomous coding, an AI agent
orchestrator built around a simple Ralph-loop core that becomes powerful
through composition.

**Hand it a well-specified coding task, let the agents plan, build,
verify, and fix, and come back to reviewable, tested work.**

The default workflow is strong enough to adopt as-is, before you
customize anything.

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![PyPI downloads](https://img.shields.io/pypi/dm/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

*10,700+ lifetime PyPI downloads · 4,000+ in the last 30 days (pepy.tech, 2026-06-12).*

## What it is

Ralph Workflow is an **operating system for autonomous coding**: the
agents handle the long middle of engineering work while you handle the
judgment that only a human can make. **Hand it a well-specified coding
task, let the agents plan, build, verify, and fix, and come back to
reviewable, tested work.**

The simple Ralph-loop idea — plan, build, verify — becomes a
**composable loop framework** under the hood: each phase can loop
independently and hand off to the next, so a single `ralph` command
spawns planning, development iteration, review, and fix cycles across
multiple agents and then produces finished git commits you can review
when you come back.

## Who it's for

Ralph Workflow is for developers and small teams with engineering work
that is **too big to babysit and too risky to trust blindly** — the kind
of ambitious, well-specified work that you would trust a capable
colleague to do unattended. It runs the agents you already use — Claude
Code, Codex, OpenCode, Nanocoder, Google Anti Gravity, and Pi — on your
own machine, with your keys to yourself.

It is **not** for one-line fixes, vague prompts, or repos without tests.
A repo without guardrails will produce results that reflect that.

## Install and run

```bash
pipx install ralph-workflow   # 1. install
ralph --init                  # 2. scaffold .agent/ and PROMPT.md
$EDITOR PROMPT.md             # 3. edit PROMPT.md — your spec for the run
ralph                         # 4. run the unattended workflow
```

This also auto-symlinks the bundled skill bundle into the supported
agent roots and seeds a batteries-included .gitignore covering Python,
Node, Rust, Go, Ruby, PHP, Java/Kotlin, .NET, Dart/Flutter, Elixir,
Scala, Terraform, and common IDE/OS patterns.

Ralph Workflow does not manage provider authentication or store your
agent credentials. You authenticate the agent CLIs yourself first, and
Ralph Workflow then invokes those tools directly and supervises the
workflow, even when different phases are routed through different agent
families.

### Before your first run

1. Install the agent CLIs you want Ralph Workflow to call.
2. Authenticate those CLIs normally.
3. Pick one small, concrete task for the first run.

### Quick start

```bash
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

Run those commands from a human-operated shell outside any Ralph-managed
agent session.

What happens in that flow:

- **`ralph --init`** creates the local `.agent/` support files.
- **`ralph --diagnose`** checks whether your configured agents and MCP
  setup are reachable.
- **`PROMPT.md`** becomes the task spec for the run.
- **`ralph`** directly invokes your configured agent CLIs and starts the
  unattended workflow.

Depth presets control iteration intensity:

```bash
ralph -Q     # quick: small fixes, single iteration
ralph        # standard: most features and tasks
ralph -T     # thorough: complex refactors, ten iterations
```

## Supported agents

Ralph Workflow ships with first-class support for six user-facing agent
CLIs: Claude Code (with both interactive and headless transports),
Codex, OpenCode, Nanocoder, Google Anti Gravity, and Pi. Each agent has
a documented end-to-end verification path: an interactive parity smoke
test for Claude and AGY, and a public-surface black-box pytest suite for
Codex, OpenCode, Nanocoder, and Pi.

| Agent | Description | Verification command |
|---|---|---|
| **Claude Code** | Anthropic's CLI for Claude. The canonical reference agent. | `ralph smoke-interactive-claude` |
| **Codex** | OpenAI's Codex CLI. | (public-surface black-box pytest) |
| **OpenCode** | Open-source terminal coding agent. | (public-surface black-box pytest) |
| **Nanocoder** | Local-only TUI coding agent. | (public-surface black-box pytest) |
| **Google Anti Gravity (AGY)** | Google's Antigravity CLI (`agy`, v1.0.9+). Runs in a PTY with a bounded drain so buffered stdout is captured end-to-end, and the AGY parser classifies live output into `text:` / `thinking:` / `tool_use:` events for the smoke report. | `ralph smoke-interactive-agy` |
| **Pi** | Minimal coding agent. Headless mode is `pi --mode json <prompt>` and emits the documented `AgentSessionEvent` NDJSON stream per <https://pi.dev/docs/latest/json>. | `uv run pytest tests/agents/test_pi_dev_blackbox.py -q` |

The canonical end-to-end AGY verification (mock-backed, always green) is:

```bash
cd ralph-workflow && \
  RALPH_AGY_BINARY="$(pwd)/tests/_support/mock_agy.sh" \
  uv run python -m ralph smoke-interactive-agy --agent 'agy/Gemini 3.5 Flash (Medium)'
```

Expected green parity table excerpt:

```text
| Agent                         | Transport | File | Session                                       | Parser events | Tool activity | Artifact | Breaks |
| agy/Gemini 3.5 Flash (Medium) | agy       | yes  | interactive-agy-smoke-Gemini-3.5-Flash-Medium | 1             | yes           | yes      | none   |
```

Live AGY is also exercised end-to-end by
`tests/test_agy_live_regression.py` (8 black-box tests, marked `live_agy`)
and `tests/test_smoke_agy_end_to_end.py` (4 black-box tests, marked
`subprocess_e2e`). Neither suite runs under `make verify` (the 60s
combined test budget only covers the `ralph.test_suites` invocation
that `make verify` runs); run them on demand with:

```bash
cd ralph-workflow
make test-live-agy                # 8 live tests, 600s per-suite cap
uv run pytest tests/test_smoke_agy_end_to_end.py -q -m subprocess_e2e
```

The live suite either passes or xfails via documented upstream-blocked
gates. The smoke-log e2e suite skips cleanly when `agy` is not on `PATH`.

The full source-of-truth for AGY CLI behavior (version, flag set, model
list, probe output, cli.log tail) is committed to
`tmp/agy-source-of-truth.txt` and re-validated on every plan that
touches AGY support; see the most recent `=== LIVE RE-MEASUREMENT ===`
block for the current local-binary health verdict. The agent-by-agent
documentation lives in `docs/sphinx/agents.md`; this README section is
a one-paragraph pointer, not a duplicate.

## What a run leaves you

Here is the actual finish-receipt from the bundled
[empty-name-validation example](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/examples/first-review-bundle/) —
a real, unedited handoff, not a mock-up. You read this when you come
back instead of a transcript:

```text
# Development Result

## Outcome
Implemented empty-name validation in the CLI create flow and added
test coverage for empty and whitespace-only input.

## Changed files
- cli/create.py
- tests/test_create.py

## Checks run
- pytest tests/test_create.py        ✓ passed
- project formatting / lint checks    ✓ passed

## Reviewer focus
- confirm validation happens before any file creation side effect
- confirm the error message is clear enough for CLI users
- confirm no unrelated flow changed
```

Want to follow a full first run? Read the
[real-task walkthrough](https://ralphworkflow.com/blog/real-task-walkthrough-overnight-refactoring)
or browse the [first-run guide](https://ralphworkflow.com/start).

## Documentation

This README intentionally leaves out deeper implementation details and
defers to the Sphinx operator manual for those.

- **Quickstart:** [`docs/sphinx/quickstart.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/quickstart.md) —
  shorter repeat-use reference with commands and flags
- **Getting Started:** [`docs/sphinx/getting-started.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/getting-started.md) —
  fuller first-run walkthrough with task guidance
- **Concepts:** [`docs/sphinx/concepts.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/concepts.md) —
  terminology and mental model
- **CLI Reference:** [`docs/sphinx/cli.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/cli.md) —
  all flags and sub-commands
- **Configuration:** [`docs/sphinx/configuration.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/configuration.md) —
  config files and precedence
- **Developer Reference:** [`docs/sphinx/developer-reference.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/developer-reference.md) —
  maintained contributor and architecture reference
- **Modules Index:** [`docs/sphinx/modules.rst`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/modules.rst) —
  API/module entry points for deeper internals
- **Adding and managing agent support:** [`docs/agents/README.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/agents/README.md) —
  entry point for adding, updating, or removing a built-in or custom
  agent

## When Ralph Workflow fits (and when it doesn't)

**Fits:**

- Multi-step tasks that outgrow one prompt
- Work you want to review after the fact instead of steering live
- Teams that want AI execution to stay in the repo
- Runs where you want to mix stronger and cheaper models by phase

**Does not fit:**

- One-shot interactive prompts
- Pair-programming sessions with constant human steering
- Tiny tasks where setup overhead is not worth it
- Workflows that need unpredictable mid-run human input

## Idle watchdog

The agent session watchdog judges whether a session is stuck by watching
four evidence channels (stdout, MCP tool calls, subagent activity, and
workspace file changes) instead of stdout alone, so unattended runs that
do real work through quiet channels are not falsely killed.

For full configuration knobs, channel semantics, and post-mortem
diagnostic shape, see
[Watchdogs and timeouts](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/watchdogs-and-timeouts.md)
in the operator manual.

## Privacy & Error Reporting

Ralph Workflow sends anonymous crash reports and performance metrics to
help fix bugs and improve reliability. No personal data is collected.

Each installation generates a random 32-character identifier stored in
`~/.config/ralph-workflow-user.ini`. This identifier is not tied to
your name, email address, IP address, or any other personal data — it
is a random string used only to distinguish different installations in
crash reports. A fresh random session identifier is generated on every
run.

To opt out: delete or rename `~/.config/ralph-workflow-user.ini`.
Ralph Workflow creates a new random ID on the next run.

## Community

Already installed? Run **`ralph star`** from your terminal to open the
primary repo, or visit
<https://codeberg.org/RalphWorkflow/Ralph-Workflow>. Codeberg is primary
— star, watch, fork, and open issues there first; GitHub is a read-only
mirror. Stars are the only signal we get that Ralph Workflow is working
for you, and they set what we build next.

## Trust and safety

The standalone Ralph Workflow MCP server (`ralph-mcp`) binds to
`127.0.0.1` and exposes the exec surface only over loopback. When the
optional `MCP_AUTH_TOKEN` environment variable is set, requests must
carry a matching `Authorization: Bearer <token>` header; the
comparison uses `hmac.compare_digest` to prevent timing-side-channel
attacks. An unset or empty `MCP_AUTH_TOKEN` is a no-op (the loopback
bind is the trust boundary).

Local execution is the rule: Ralph Workflow does not upload your code
or data to a cloud service.

## Development and verification

If you are changing Ralph Workflow itself, start with
[`CONTRIBUTING.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/CONTRIBUTING.md)
and run the canonical verification command before you finish:

```bash
make verify
```

## Pro support (optional GUI layer)

[Ralph-Workflow-Pro](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/pro-support.md)
is an optional GUI layer that runs the engine as a subprocess. The
engine exposes a small, read-only, bounded surface so Pro can monitor
runs and, in advanced uses, inject custom pipeline collaborators.

For the engine-side contract, the `PipelineDeps` bundle shape, and the
default-factory wiring, see
[Pro support](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/pro-support.md)
and
[Developer reference](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/developer-reference.md)
in the operator manual.
