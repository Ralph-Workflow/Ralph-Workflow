# ralph-workflow — the AI agent orchestrator and software factory

Coding agents write code quickly; the human coordination around them does not scale — babysitting,
conflicts, re-running checks, and review queues cap throughput at the handoffs.

**The operating system for autonomous coding.**

**Ralph Workflow — a free, open-source AI agent orchestrator and software factory — lets developers
and teams with a coding agent CLI supply the spec and checks, then runs plan → build → verify → fix
as a configurable workflow graph across Git worktrees, returning integrated software to judge.**
By hand you are the scheduler, reviewer, and merger; a plain loop repeats one prompt. Ralph Workflow
runs that graph with review, retries, recovery, and fallback chains, landing checked work on one
mainline.

It runs locally under AGPL-3.0-or-later: no account, no hosted service, nothing to sign up for — your
specifications, configuration, and Git history stay yours.

Every default ships with the package — workflow graph, agent chains, MCP tools, artifact contracts,
retries, recovery, Git sync — so there is no setup project before the first useful run: productive on
day 1, preconfigured and yours to tweak. See the
[bundled defaults](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/configuration.md#bundled-defaults)
for the exact set.

Ralph Workflow manages the harness, not the models: the coding agents — Claude Code, Codex, Cursor,
and the rest — are harnesses around models; Ralph Workflow is the harness around those. There is no
vendor lock-in — agent chains mix backends and fall back when one fails, so switching vendors is a
configuration change. It launches the agent CLI you installed and authenticated, so whatever login or
subscription that agent supports is what runs; it needs no API key of its own, each agent defines its
own authentication (a login, an API key, or either), and its vendor determines model access, rate
limits, and cost.

It fits work with a clear finish line and checks you trust; it is not (yet) for open-ended
exploration or repositories with no reliable tests or checks.

## Install

**Requirements:** Python 3.12 or newer, Git, `pipx`, and one coding agent CLI you have already
installed and authenticated.

```bash
pipx install ralph-workflow
ralph --version
```

`pip install ralph-workflow` works in a virtual environment of your own.

From a source checkout instead? See [CONTRIBUTING.md](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/CONTRIBUTING.md#dev-build-vs-stable-build).

## First run

New to Ralph Workflow? [START_HERE.md](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/START_HERE.md)
walks through a first serious run.

In the git repository you want Ralph Workflow to work on:

```bash
ralph --init          # user-global config, starter PROMPT.md, detected agents
ralph --list-agents   # the agent CLIs Ralph Workflow can see
ralph --diagnose      # pre-flight checks — every line should be green
```

Then edit `PROMPT.md`: state the outcome and the checks you expect, and remove the
`<!-- ralph:starter-prompt ... -->` marker at the top — Ralph Workflow refuses to run while it remains.
A small first task looks like this:

```markdown
# Goal
Reject an empty email address in the signup validator with a clear error.

## Acceptance criteria
- An empty email is rejected before a user record is created.
- Valid signup behavior and public interfaces stay unchanged.

## Verification
- Run python -m pytest and fix any failures.
```

Start the run with `ralph`. A successful first run completes with the changes committed in a
worktree and either integrated onto the mainline or reported with the evidence it produced. Then read
the summary of what changed and which checks passed.

## Built-in agent backends

Nine backends ship with Ralph Workflow. Authenticate the CLI yourself; Ralph Workflow uses each
agent's own login or key.

| Backend | CLI | Authentication | Transport and caveats |
|---|---|---|---|
| **Claude Code** | `claude` | `claude login` or an Anthropic API key | Interactive PTY; the reference agent. |
| **Claude Code (headless)** | `claude` | Same as `claude` | Same binary without a PTY. |
| **Codex** | `codex` | `codex login` or an OpenAI API key | Unattended `codex exec`; no session resume. |
| **OpenCode** | `opencode` | Provider-specific keys | Headless subprocess; model selection uses `-m <provider>/<model>`. |
| **Nanocoder** | `nanocoder` | Local-only TUI, no remote auth | Local-only TUI; a provider is required for model aliases. |
| **AGY (Google Anti Gravity)** | `agy` | `agy login` with a Google account | PTY transport; published model IDs change between releases. |
| **Pi** | `pi` | Provider configuration | Headless `pi --mode json <prompt>`. |
| **Cursor** | `agent` | `CURSOR_API_KEY` or a file-backed login | Headless `--print`; credentials come from file, not the Keychain, unless `AGENT_CLI_CREDENTIAL_STORE=memory` is set explicitly. If no valid Cursor auth file exists, Ralph Workflow copies your existing Cursor IDE login into Cursor's file-backed auth paths. |
| **Kimi Code** | `kimi` | OAuth login wiring the `managed:kimi-code` subscription provider | Headless; `-m` needs a fully configured model ID from `~/.kimi-code/config.toml`. |

Model strings differ per agent — see [agent compatibility](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/agent-compatibility.md).

## Independent work, shared mainline

Each run works in its own Git worktree and syncs with the configured mainline at startup, after
commits, and at phase boundaries. Integration rebases the feature onto the mainline, routes conflicts
to a resolution agent chain, falls back to an endpoint merge when resolution fails, and fast-forwards
the mainline to the landed tip once Git proves the integration finished. Local integration is on by
default with `main` as the default target;
remote sync publishes to the configured remote when one exists and can be turned off for local-only
operation; recovery state is durable, so an interrupted run resumes where it stopped.

Two limits: branch protections and required checks still apply — Ralph Workflow does not bypass them.
And the worktree holding `main` is integration infrastructure: Ralph Workflow may snapshot its
uncommitted content to a recoverable ref and reset the worktree. Keep active edits in feature
worktrees. Details: [auto-integration](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/configuration.md#auto-integration).

## Make it yours

| File | What it configures | Reference |
|---|---|---|
| `ralph-workflow.toml` | agent chains, retries, and Git sync | [Configuration reference](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/configuration.md) |
| `pipeline.toml` | phases, routing, and loopbacks | [Pipeline configuration](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/advanced-pipeline-configuration.md) |
| `mcp.toml` | MCP tool servers and web access | [MCP configuration](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/advanced-mcp-configuration.md) |
| `artifacts.toml` | artifact contracts and output types | [Artifact configuration](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/advanced-artifact-configuration.md) |

User-global (`~/.config/`) and project-local (`.agent/`) are separate scopes: `ralph-workflow.toml`
keeps one name in both, while `pipeline.toml`, `mcp.toml`, and `artifacts.toml` are the project-local
names for the user-global `ralph-workflow-pipeline.toml`, `ralph-workflow-mcp.toml`, and
`ralph-workflow-artifacts.toml`.

## Skills

Ralph Workflow installs two kinds of skills.

**Artifact skills** ship with Ralph Workflow and are installed into the project scope. The set
follows the pipeline's artifact types automatically, so adding a skill now means adding an artifact
type.

**Universal skills** are exactly three — `test-driven-development`, `systematic-debugging`, and
`verification-before-completion` — fetched from the single upstream `obra/superpowers` at release
versions and installed user-global, never committed into your project, so upstream changes never
dirty your git tree. The release check runs at the start of a run, at most once per day, and fails
soft: an offline, rate-limited, slow, or malformed upstream leaves the installed skills as they are.
No upstream copy is bundled, so nothing can go stale — a machine that is never online has no
universal skills, while the artifact skills still work.

Two switches turn every skill-update network request off: `universal_updates_enabled = false` under
`[skills]` in `~/.config/ralph-workflow.toml`, and the `RALPH_DISABLE_SKILL_UPDATES` environment
variable. The [configuration reference](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/configuration.md#skills)
covers both, the update safety rules, the trust statement, and the removal of the retired default
skills after upgrading.

## Protections

Two mechanisms guard the tree you work in.

- **Two-stage commit review.** Before any commit the pending diff passes deterministic tripwires and
  then a review agent. It is a **damage gate, not a code review**: it decides whether the diff looks
  like accidental damage (mass deletion, protected paths, staged secrets, oversized binaries), not
  whether the code is good. A rejection rolls the tree back and keeps the sealed proposal ref for
  inspection. [Per-setting details](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/configuration.md#commit-review-configuration).
- **Engine-owned writes are auto-committed.** Ralph Workflow's own background writers — the
  project-scope artifact skills, the `.gitignore` seed, the project-policy surfaces — commit their
  own byte-exact diff immediately, so those writes never leak into an agent's commit. Agent and user
  edits to those paths are never swept in. [Contract](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/docs/agents/deterministic-auto-commit.md).

## Documentation

The maintained operator manual is the Sphinx build at [`docs/sphinx/index.rst`](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/index.rst).

- [Docs map](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/docs/README.md) — route by reader job
- [Getting started](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/getting-started.md) — the first run, end to end
- [Agent CLI lifecycle](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/agents.md) and [agent compatibility](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/agent-compatibility.md) — selection, authentication, model syntax
- [Configuration reference](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/configuration.md) — every setting
- [CLI reference](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/cli.md) — every flag

## License

AGPL-3.0-or-later: use it, read it, change it, and publish your changes under the same license if you
offer a modified version over a network. The Ralph loop pattern is credited to Geoffrey Huntley;
Ralph Workflow is an independent implementation built on that core. No agent vendor named above
endorses this project.

## Project home

- **Repository:** <https://github.com/Ralph-Workflow/Ralph-Workflow> — **PyPI:** <https://pypi.org/project/ralph-workflow/>
- **Issue tracker:** <https://github.com/Ralph-Workflow/Ralph-Workflow/issues/new>
- **License:** [LICENSE](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/LICENSE) — **Code of conduct:** [CODE_OF_CONDUCT.md](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/CODE_OF_CONDUCT.md)
- **Contributing:** [CONTRIBUTING.md](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/CONTRIBUTING.md) — every pull request must agree to the [Contributor License Agreement](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/CLA.md)
