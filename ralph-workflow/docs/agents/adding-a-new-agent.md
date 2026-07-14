# Adding, Updating, and Removing Agent Support

See also: [Agent Subsystem README](README.md) for the discoverable entry point.

This guide covers the workflows for managing agent support in Ralph. It
describes how to register new agents, update existing ones, remove agent
definitions, and decide whether a change is only a new registered agent or a
full transport/runtime integration.

The single canonical entry point for agent registration is `register_agent_support` (defined in `ralph/agents/registration.py`). For the 90% case, prefer the **opinionated 5-line recipe** `register_my_agent` (also in `ralph/agents/registration.py`); it picks a transport-derived default strategy so an interactive caller can never accidentally register an interactive agent with `BaseExecutionStrategy`. Advanced scenarios may use `register_agent_support_to_catalog` (test-friendly) or `AgentCatalog.add` directly.

---

## First decision: registered agent or new transport?

Most additions should be a **registered agent** on an existing transport. Add a
new `AgentTransport` only when the agent needs transport-specific command
construction, runtime environment setup, MCP wiring, prompt injection, session
handling, or completion semantics that cannot be represented by `AgentConfig`
flags.

Use this rule of thumb:

| Need | Use |
| --- | --- |
| A binary can run from argv and emits plain text or known JSON | `AgentTransport.GENERIC` with `register_my_agent` |
| A binary is another model/provider shape of an existing transport | Existing transport plus `cmd`, `model_flag`, or alias handling |
| A binary requires a PTY but can share existing Claude-style behavior | Existing interactive transport only if prompt/session semantics match exactly |
| A binary has its own MCP config env vars, prompt injection, session flags, parser shape, or completion evidence | New `AgentTransport` and full dispatch-table wiring |
| A built-in agent ships with Ralph out of the box | `BuiltinAgentSpec` in `ralph/agents/builtin.py` |

Do not add a transport just to give an agent a nicer name. Do not force an
agent onto an existing interactive transport if it only happens to use a
terminal UI. Nanocoder is the cautionary example: it is PTY-backed like
interactive Claude, but it needs its own command builder, runtime resolver,
MCP env handling, prompt injection, parser, and smoke command.

## Headless vs interactive agents

Headless agents are subprocesses that accept the prompt through argv/stdin and
emit parseable stdout. Interactive agents are PTY-backed programs that expect a
terminal and may redraw a TUI, show permission menus, spawn child processes, or
hold a session open after useful work has happened.

Headless support existing upstream does **not** mean Ralph should use it by
default. Choose the mode that preserves the agent's real contract: prompt
delivery, tool access, MCP behavior, session continuation, completion evidence,
and operator-visible progress. Some agents are represented by more than one
built-in transport when Ralph maintains more than one invocation contract for
that binary. Keep both built-in Claude contracts: `claude` and
`claude-headless` must not be deprecated, merged, or redirected into each other
as part of agent-maintenance work unless a human maintainer explicitly asks for
that exact change. Nanocoder is the important gotcha: its JSON/plain automation
path has a hidden long-run action limit, observed around 100 actions, so
Ralph's maintained integration must use Nanocoder's PTY-backed Ink runtime when
trust prompts, MCP behavior, and visible progress actually surface there.

### Headless contract

A headless agent should:

- accept the task prompt without requiring a human terminal session;
- emit structured output when the provider supports it, or stable plain text
  that `GenericParser` can parse;
- exit cleanly after the task is complete;
- use `output_flag`, `print_flag`, `streaming_flag`, `model_flag`, and
  `session_flag` when those are enough to describe the CLI;
- use a parser that emits `text`, `thinking`, `tool_use`, `tool_result`,
  `error`, `stop`, or agent-specific events with meaningful `content`.

Headless agents are usually cheaper to support because they avoid PTY lifecycle,
TUI repaint, permission-menu, and descendant-process edge cases.

### Interactive/PTY contract

An interactive agent should be marked `interactive=True` only when the process
really needs a PTY. A PTY-backed agent must define how Ralph will:

- pass the prompt into the session (`prompt_file` argv, positional prompt text,
  or `_PtyExtras.initial_input`);
- decide whether session continuation exists (`session_flag`, `--resume {}`,
  `--session {}`, or `no_default_session_flag=True`);
- auto-handle permission or trust prompts, or fail with a clear diagnostic;
- preserve useful visible output without replaying every terminal repaint;
- terminate the whole PTY process subtree on exit/timeout;
- prove completion through a trusted signal, not only exit code.

Interactive output is user experience, not just logs. If the agent is doing
work in the background, the parser/display path must surface bounded,
meaningful progress. A parser that emits one generic "interactive output" event
and then suppresses the rest creates a silent run even when the agent is
working. For TUI agents, normalize VT/control sequences, coalesce repaint-only
frames, filter internal markers, and cap distinct status snapshots.

---

## Strategy selection by transport

The opinionated `register_my_agent` helper picks the right execution strategy
for you based on the transport, so the typical 5-line recipe is just:

<!-- BLACKBOX_RECIPE_START -->
```python
from ralph.agents import register_my_agent
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport

my_registry = AgentRegistry()
register_my_agent(
    name="my-agent",
    transport=AgentTransport.GENERIC,
    parser=GenericParser,
    agent_registry=my_registry,
)
```
<!-- BLACKBOX_RECIPE_END -->

When `strategy` is omitted, the helper picks from this table so an interactive
caller can never accidentally register an interactive agent with
`BaseExecutionStrategy`:

| `AgentTransport`         | Default strategy class                          |
| ------------------------ | ----------------------------------------------- |
| `CLAUDE_INTERACTIVE`     | `ClaudeInteractiveExecutionStrategy`            |
| `AGY`                    | `_make_agy_strategy` (agy PTY strategy)         |
| `OPENCODE`               | `OpenCodeExecutionStrategy`                     |
| `CLAUDE`                 | `ClaudeExecutionStrategy`                       |
| `CODEX`                  | `GenericExecutionStrategy`                      |
| `NANOCODER`              | `GenericExecutionStrategy`                      |
| `PI`                     | `_make_pi_strategy` (Pi session strategy)       |
| `GENERIC`                | `GenericExecutionStrategy`                      |

For interactive agents (`interactive=True`) the helper also auto-applies
the `--resume {}` session template unless `no_default_session_flag=True` is
passed (agy does this). Pass an explicit `strategy=` to override the
transport-derived default; pass an explicit `session_flag=` to override the
auto-applied `--resume {}` template.

---

## Full transport checklist

Adding a new `AgentTransport` is a cross-cutting runtime change. Treat these as
one contract; missing any axis usually produces a silent fallback, a hanging
agent, or a green process exit with no useful work.

1. Add the enum value in `ralph/config/agent_transport.py`.
2. Add a `CommandBuilder` in `ralph/agents/invoke/_command_builders/` and wire
   it into `COMMAND_BUILDERS`.
3. Add a `RuntimeResolver` in `ralph/agents/invoke/_runtime_resolvers/` and
   wire it into `RUNTIME_RESOLVERS`.
4. Register the parser in `ralph/agents/parsers/__init__.py` and ensure
   `resolve_parser_key()` maps the command/transport pair to it.
5. Register the default strategy in the catalog strategy dispatch. Use a custom
   strategy when completion or child-process semantics differ from the generic
   behavior.
6. For a built-in, add a `BuiltinAgentSpec` row in `ralph/agents/builtin.py`.
7. If the transport supports direct aliases such as `agent/provider/model`, add
   registry normalization tests that preserve the full provider/model suffix.
8. If the transport has MCP config, implement merge/load helpers under
   `ralph/mcp/transport/` and wire them through the runtime resolver.
9. If the transport is PTY-backed, decide whether it can use
   `_run_shared_interactive_pty()` and whether it needs custom `_PtyExtras`.
10. If the upstream CLI exposes both an interactive editor and a non-interactive
    `run` / `--print` / JSON mode, choose the non-interactive contract for
    unattended Ralph phases unless the interactive mode is the documented
    automation API. Do not paste prompts into a TUI editor as the primary
    invocation path; welcome banners and editor buffers are not proof that a
    model turn started.
    Nanocoder is the known exception: do not use its JSON/plain path as the
    durable backend because it can fail after long action sequences. Keep
    Nanocoder on the PTY-backed Ink runtime; Ralph's Nanocoder builder passes
    `--no-plain` before `run` for that reason. Prove prompt submission,
    parser-visible model text/tool activity, artifact completion, and process
    cleanup with the Nanocoder smoke test.
11. Add or update a manual smoke command only for live, token-consuming checks;
    do not add live smoke commands to `make verify`.

Claude built-ins are a separate invariant: preserve both `claude` and
`claude-headless` as maintained invocation contracts. Do not remove, deprecate,
merge, alias, or silently redirect one to the other as part of unrelated agent
work. A task about adding, fixing, or documenting another agent is never a
reason to change either Claude contract.

The guard test
`tests/agents/invoke/test_dispatch_table_covers_every_transport.py` must pass
for every transport. It checks command builders, runtime resolvers, strategy
dispatch, and parser resolution.

## Change-type checklist

Use the smallest checklist that matches the change.

### Custom agent on an existing transport

- Register with `register_my_agent` unless the 14-kwarg advanced form is
  actually needed.
- Prefer `AgentTransport.GENERIC` only when no existing transport behavior is
  required.
- Provide a parser that emits meaningful `content` and any real `tool_use`
  events.
- Add a black-box registration or parser test. Do not mutate the global default
  catalog in tests unless the test is explicitly about default-catalog behavior.
- Document any required CLI flags in the caller-owned config or local docs.

### New built-in agent on an existing transport

- Add one `BuiltinAgentSpec` row in `ralph/agents/builtin.py`.
- Pin the row in `tests/agents/test_builtin_spec_consolidation.py`.
- Update user-facing agent lists in Sphinx docs if the built-in is operator
  visible.
- Add parser, command, and registry tests for any new alias shape.
- Add smoke plumbing only when a live manual smoke is needed to prove a real
  runtime behavior that fakes cannot cover.

### New transport

- Complete the full transport checklist above.
- Add tests for command building, runtime resolution, parser resolution,
  strategy dispatch, config aliases, and MCP merge behavior when applicable.
- Add timeout/resource-lifecycle-safe tests around any process, network, or
  filesystem I/O.
- Decide explicitly whether the maintained path is headless or PTY. Do not pick
  headless only because the upstream CLI exposes it.
- Prefer a documented non-interactive `run` / `--print` / JSON mode over
  automating an interactive editor unless the transport has a documented
  exception such as Nanocoder's JSON/plain long-run action-limit bug. If a PTY
  is still required, prove that the prompt is submitted as a turn, not merely
  rendered in the editor buffer.
- Record the mode decision in this guide or the transport docs when future
  maintainers might reasonably choose the wrong path.

## Parser and display contract

Parsers are the bridge from agent stdout/TUI output to both runtime decisions
and operator-visible progress. Good parser output is bounded, semantic, and
useful when placed in the smoke report's "Observed output" section.

Parser rules:

- Use `NdjsonParserBase` for NDJSON/SSE-like wire formats so shared lifecycle,
  error, `[DONE]`, `data:`, and non-JSON behavior stays consistent.
- Use `GenericParser` only when plain text plus `[plain] tool: NAME` markers are
  enough.
- Emit `tool_use` for real tool activity; do not infer tool activity from an
  agent-authored self-report artifact.
- Make MCP tool activity visible to the watchdog through the execution strategy
  as well as the parser. A transport-specific parser that emits `tool_use` is
  not enough if `classify_activity_line()` still treats the raw provider frame
  as ordinary output. Different AI coding clients can wrap the same model and
  MCP result differently: one may expose a rich `is_error` tool result while
  another collapses the same failure into generic text such as `terminated` or
  an unknown tool-use frame. The strategy must classify provider tool-call
  frames as `AgentActivityKind.TOOL_USE`, provider tool-result/error frames as
  `AgentActivityKind.ERROR_LINE` when they failed, and the tool-call extraction
  helper must understand the provider's raw envelope. This prevents
  client-adapter drift from turning repeated MCP failures into apparent progress
  and retrying the same call forever.
- Preserve provider-specific tool-name metadata for both `tool_use` and
  `tool_result`. If the provider uses a non-canonical field such as
  `toolName`, normalize or preserve it so the subagent progress channel emits
  `tool_result:<name>` instead of `tool_result:unknown`. A named progress
  summary is Ralph-side observability; the actual tool payload still comes from
  the transport/tool-result stream.
- Emit non-empty `content` for user-visible `status` events. The activity
  stream renders `status` content directly.
- For TUI output, call `normalize_vt_text()`, suppress control-only frames, and
  coalesce repeated repaint frames. Keep a small cap for distinct status events.
- Separate UI chrome from agent work. Spinner lines, banners, config paths, and
  "waiting" frames should be `status`; model prose should be `text`; executed
  tool summaries should be `tool_use` / `tool_result` when the upstream stream
  exposes them. Do not let an early cap on status frames hide later model text
  or tool activity.
- Filter internal markers such as turn-boundary sentinels before they reach the
  display.
- Keep parser state bounded. Long-lived sets/lists/deques in parser instances
  must be capped or avoided.

Display rules:

- `stream_parsed_agent_activity()` is the shared path for display output,
  `raw_output_sink`, `rendered_output_sink`, session capture, and parser-driven
  subagent activity.
- If the smoke report is silent, inspect `rendered_output_sink` first. A parser
  may be producing raw lines while the renderer discards their content.
- Do not print raw JSON or full TUI repaint streams as a UX fix. Normalize and
  summarize instead.

## Completion, session, and recovery contract

Agent success requires evidence that matches the transport. A clean subprocess
exit is not enough for transports that can exit while background work or
session state remains unresolved.

Required checks:

- For phases with required artifacts, clear stale per-phase artifacts before
  invocation and require fresh evidence from the current run.
- For smoke commands, use the canonical `smoke_test_result` receipt for
  transports that do not emit Claude's `Task declared complete:` marker.
- Use broker-owned completion sentinels/receipts; do not trust model-authored
  transcript text as completion evidence when a stronger receipt exists.
- Preserve full parsed output and stderr context on failures so recovery prompts
  and exit logs explain what happened.
- Route stale/missing session IDs through the recovery classifier rather than
  ad-hoc retry logic.
- If a clean exit can be resumable for a transport, carry the captured session
  ID through the typed error path and retry the same session within the shared
  same-agent retry budget.

Interactive session rules:

- `interactive=True` auto-applies `--resume {}` in `register_my_agent`; pass
  `no_default_session_flag=True` for agents whose CLI has no default resume flag
  or decides sessions internally.
- Built-ins should declare the exact `session_flag` or `no_default_session_flag`
  in `BuiltinAgentSpec` so behavior is pinned by tests.
- PTY readers must capture visible session IDs and thread them into recovery.
- Terminating a PTY run must tear down the process subtree, not just the PTY
  parent process.
- Closing or abandoning the public invocation iterator must also close the
  inner reader and tear down the live process subtree. This is part of the
  process-lifecycle contract, not only Ctrl-C handling.

## MCP and runtime environment contract

The runtime resolver owns transport-specific environment setup. Do not scatter
MCP config writes or env var mutations across invoke call sites.

When adding MCP support:

- load existing upstream servers through a transport-specific helper;
- merge Ralph's MCP server with upstream config only through the shared MCP
  transport helpers;
- set transport-required trust/env vars in the runtime resolver;
- pass `workspace_path` to helpers that need project-local config;
- keep all blocking I/O bounded so the MCP timeout audit stays clean;
- test both safe overwrite mode and unsafe merge mode when a transport
  preserves native upstream MCP servers.

## Testing and smoke coverage

Every agent-support change needs black-box coverage at the seam it changes.

Use these tests as a checklist:

- registration helpers:
  `tests/agents/test_register_agent_support.py`,
  `tests/agents/test_add_a_new_agent_recipe.py`
- built-in declarations:
  `tests/agents/test_builtin_spec_consolidation.py`,
  `tests/agents/test_builtin_supports.py`
- dispatch-table coverage:
  `tests/agents/invoke/test_dispatch_table_covers_every_transport.py`
- command builders and runtime resolvers:
  `tests/agents/invoke/test_command_builder_spec.py`,
  `tests/agents/invoke/test_invoke_dispatch_parity.py`
- parser behavior:
  `tests/test_<agent>_parser.py` or
  `tests/agents/parsers/test_<agent>_parser.py`
- MCP/tool watchdog parity:
  `tests/agents/idle_watchdog/test_mark_tool_call_runtime_reachability.py`
- PTY/prompt/session behavior:
  `tests/test_claude_interactive_pty.py`,
  `tests/agents/invoke/test_pty_*`
- smoke harness plumbing:
  `tests/test_cli_smoke.py`,
  `tests/test_harness_run_diagnosis.py`,
  `tests/test_smoke_plumbing_uses_canonical_submit.py`
- transport MCP config:
  `tests/mcp/test_<agent>_transport.py` or the transport-specific invoke tests

Live smoke commands are manual diagnostics. They consume real agent tokens or
quota and must remain outside `make verify`. Use them after focused tests when
the bug involves real PTY/TUI behavior:

```bash
python -m ralph smoke-interactive-claude
python -m ralph smoke-interactive-nanocoder
python -m ralph smoke-interactive-agy
```

To exercise the shared subagent lifecycle contract, add `--subagents` to any
interactive smoke command. A passing run must show exactly one native subagent
dispatch, its correlated result, later main-agent activity, and normal smoke
completion. Use a non-empty UTF-8 task file inside the current workspace when
the default read-only child task does not cover the edge case under investigation:

```bash
python -m ralph smoke-interactive-claude --subagents
python -m ralph smoke-interactive-claude \
  --subagents \
  --subagent-prompt-file tmp/subagent-edge-case.txt
```

The custom file changes only the delegated task. Ralph retains the ordering,
artifact, and completion requirements, so a model-authored success claim
cannot replace observed runtime evidence. Use the resulting ordered failures
to separate dispatch, parser/result, post-result progression, and watchdog
problems.

When a live smoke fails, inspect both the parity table and the raw transcript.
A green file/artifact result proves task completion, but it does not prove the
operator saw useful progress. Parser tests must cover the representative raw
lines that should appear in "Observed output" so a transport does not regress to
spinner-only visibility.

## Definition of done for agent support

Agent support is not complete until all of these are true:

- The mode decision is explicit: existing transport vs new transport, headless
  vs PTY, built-in vs custom registration.
- The command builder produces the exact argv the maintained runtime uses.
- The runtime resolver owns all transport-specific env and MCP setup.
- The parser emits bounded, meaningful events that drive both runtime evidence
  and operator-visible output.
- Completion is proven by the right evidence for the transport: required
  artifact, canonical receipt, completion marker, captured session, or typed
  resumable error as appropriate.
- PTY transports handle permission/trust prompts, session ID capture,
  process-subtree teardown, and silent-background-work UX. Prompt injection is
  allowed only when the upstream interactive surface is the documented
  automation contract; otherwise use the upstream non-interactive command.
- Recovery behavior uses the shared classifier/retry machinery, not ad-hoc
  loops.
- Tests cover the changed seam without real subprocess/network/file I/O unless
  the test is deliberately marked as subprocess E2E or manual smoke.
- Public docs list the agent only if it is user-visible, and internal docs
  record any surprising mode choice or runtime gotcha.
- `make verify` passes with no ERROR/WARNING diagnostics.

## Migrating from `register_agent_support` to `register_my_agent`

The long-form `register_agent_support` is unchanged and still supported for
all 14 kwargs.  For the 90% case, `register_my_agent` collapses the recipe to
3 common shapes:

### Headless agent (was 14 kwargs)

```python
from ralph.agents import register_my_agent
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport

my_registry = AgentRegistry()
register_my_agent(
    name="my-headless-agent",
    transport=AgentTransport.GENERIC,
    parser=GenericParser,
    agent_registry=my_registry,
    cmd="my-agent-binary",
)
```

### Interactive agent (was 14 kwargs + risk of BaseExecutionStrategy)

```python
from ralph.agents import register_my_agent
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport

my_registry = AgentRegistry()
register_my_agent(
    name="my-interactive-agent",
    transport=AgentTransport.CLAUDE_INTERACTIVE,
    parser=ClaudeParser,
    agent_registry=my_registry,
    interactive=True,
    cmd="my-agent-binary",
)
```

### Interactive agent that opts out of the default `--resume {}` template (was the hidden `name != "agy"` special case)

```python
from ralph.agents import register_my_agent
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport

my_registry = AgentRegistry()
register_my_agent(
    name="my-no-resume-agent",
    transport=AgentTransport.CLAUDE_INTERACTIVE,
    parser=GenericParser,
    agent_registry=my_registry,
    interactive=True,
    no_default_session_flag=True,
)
```

The `cmd` kwarg defaults to `name`; only pass it when the executable
binary name differs from the agent name.

---

## Add a new agent

To add support for a new agent, register it using the `register_agent_support` function. This writes to the default catalog and configures the agent properties.

### Headless Agent Example

A headless agent runs non-interactively. You can register it by specifying the transport, parser factory, strategy factory, and an agent registry:

```python
from ralph.agents import register_agent_support
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport


class MyParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str):
        from ralph.agents.parsers.agent_output_line import AgentOutputLine
        stripped = line.strip()
        result = self.parse_json_line(stripped)
        if result is not None:
            yield result
        else:
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)


class MyStrategy(BaseExecutionStrategy):
    pass


my_registry = AgentRegistry()

register_agent_support(
    name="my-headless-agent",
    transport=AgentTransport.GENERIC,
    parser_factory=MyParser,
    strategy_factory=MyStrategy,
    agent_registry=my_registry,
    cmd="my-agent-binary",
)
```

### Interactive Agent Example

An interactive agent requires a pseudo-terminal (PTY) to handle user interaction. Set `interactive=True` and use a PTY-capable transport:

```python
from ralph.agents import register_agent_support
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport


class MyParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str):
        from ralph.agents.parsers.agent_output_line import AgentOutputLine
        stripped = line.strip()
        result = self.parse_json_line(stripped)
        if result is not None:
            yield result
        else:
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)


class MyStrategy(BaseExecutionStrategy):
    pass


my_registry = AgentRegistry()

register_agent_support(
    name="my-interactive-agent",
    transport=AgentTransport.CLAUDE_INTERACTIVE,
    parser_factory=MyParser,
    strategy_factory=MyStrategy,
    agent_registry=my_registry,
    interactive=True,
    cmd="my-agent-binary",
)
```

> [!NOTE]
> The eight built-in agents (`claude`, `claude-headless`, `codex`, `opencode`, `nanocoder`, `agy`, `pi`, `cursor`) come from `ralph/agents/builtin.py` via `builtin_supports()`. `AgentRegistry.from_config()` and `AgentRegistry(catalog=...)` both call `_seed_catalog_with_builtins` so the registry and the catalog stay in lockstep. The `default_catalog()` global is seeded only when `AgentRegistry.from_config()` runs; it is not auto-seeded at module import.

---

## Update an existing agent

Updating an agent is done by first unregistering the old definition and then re-registering it with the new parameters.

### Updating the Catalog and AgentRegistry

To update an agent atomically, call the `unregister` method on the registry (which removes it from both the registry and the catalog) and then re-call `register_agent_support`:

```python
from ralph.agents import register_agent_support
from ralph.config.enums import AgentTransport

# 1. Remove the old registration from the registry and catalog
my_registry.unregister("my-agent")

# 2. Re-register the agent with new parameters
register_agent_support(
    name="my-agent",
    transport=AgentTransport.GENERIC,
    parser_factory=new_parser,
    strategy_factory=new_strategy,
    agent_registry=my_registry,
)
```

---

## Remove an agent

### Recommended Approach: unregister()

To remove an agent from both the configuration registry and the global catalog, call the `unregister` method on your `AgentRegistry` instance:

```python
# Unregisters the agent from both the registry and the catalog
registry.unregister("my-agent")
```

### Legacy Fallback

Alternatively, you can manually delete it from the registry's config mapping, but this does not clean up the catalog:

```python
# Legacy fallback (removes from registry configuration only):
del registry.agents["my-agent"]
```

> [!WARNING]
> The eight built-in agents cannot be permanently removed from the caller's `AgentRegistry` because they are re-registered by `AgentRegistry.from_config` on every load.

---

## Common mistakes

* **Forgetting `interactive=True`**: Interactive agents require `interactive=True` to enable session continuation.
* **Using an existing interactive transport by resemblance only**: A TUI agent
  that needs different prompt injection, session flags, MCP env vars, or
  completion evidence needs its own transport wiring.
* **Forgetting one dispatch axis**: New transports must update command builder,
  runtime resolver, strategy, and parser dispatch together.
* **Silent TUI output**: Suppressing repaint noise is correct, but suppressing
  all visible snapshots creates a bad operator experience. Emit bounded
  `status` events with meaningful content.
* **Trusting clean exit as success**: For transports with required artifacts,
  receipts, or resumable sessions, exit code 0 is only one signal.
* **Leaving child processes behind**: PTY termination must tear down the process
  subtree, not just close the parent process.
* **Trusting model-authored self-reporting**: Tool activity and completion must
  come from parser/runtime evidence or canonical receipts, not the contents of
  an agent-authored artifact.
* **Adding live smoke tests to `make verify`**: Manual smoke commands consume
  tokens/quota and stay outside the always-on verification path.
* **Not unregistering before re-registering**: Calling `register_agent_support()` with a name that is already registered in the catalog will raise a `ValueError`. Always call `registry.unregister(name)` first.
* **Using legacy deletion**: Deleting via `del registry.agents[name]` is deprecated; use `registry.unregister(name)` to clean up both the registry and the catalog atomically.
* **Adding a built-in agent name**: Attempting to register a custom agent under a built-in name can cause namespace clashes.

---

## See also

* [Architecture Documentation](../agents/architecture.md)
* [Registration Module](../../ralph/agents/registration.py)
* [Recipe Test (covers headless + interactive)](../../tests/agents/test_add_a_new_agent_recipe.py)
