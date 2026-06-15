# Agent support architecture

## 1. Overview

The consolidated agent-registration model is built around five core abstractions:
**`AgentCatalog`** owns all registrations and is the single injectable registry;
**`AgentSupport`** is the unit of registration — one dataclass bundling a parser
factory, a strategy factory, an `AgentSpec`, and an `AgentConfig`;
**`AgentSpec`** captures the headless-vs-interactive axis declaratively (replacing
the legacy `interactive=True` flag and magic `session_flag` defaults);
**`ParserTemplateBase`** factors parser boilerplate via a template-method `parse`
that subclass hooks (`classify_line`, `_classify_json_object`) specialise;
**`WatchLoopBase`** factors the watchdog poll loop via a `wait_until` template
method with injected `Clock` for testability;
**`InvocationContext`** is a frozen dataclass threading the dependency-injection
seams (clock, liveness registry, label scope, subagent activity sink) through
the executor + watchdog + strategy stack without ad-hoc `**kwargs` plumbing.

## 2. The `AgentSupport` / `AgentCatalog` / `AgentSpec` triple

### 2.1 `AgentSupport` — the registration unit

```python
@dataclass(frozen=True, slots=True)
class AgentSupport:
    name: str
    spec: AgentSpec
    parser_factory: Callable[[], AgentParser]
    strategy_factory: StrategyFactory
    config: AgentConfig
    _name_lower: str = ""

    @property
    def cmd(self) -> str:
        return self.config.cmd

    @property
    def transport(self) -> AgentTransport:
        return self.spec.transport
```

Every agent that Ralph can invoke is represented by exactly one `AgentSupport`
instance. The dataclass bundles everything the invocation engine needs: a parser
to decode the agent's stdout, a strategy to govern its lifecycle, a spec to
declare its transport requirements, and a config with CLI-flag defaults. The
`parser_factory` is a zero-arg callable so each invocation gets a fresh parser
(parsers carry per-run accumulator state). `AgentSupport` is immutable (frozen,
slots) — once added to a catalog the registration is read-only.

### 2.2 `AgentCatalog` — the injectable registry

```python
@dataclass
class AgentCatalog:
    _entries: dict[str, AgentSupport] = field(default_factory=dict)
    _by_command: dict[str, AgentSupport] = field(default_factory=dict)

    def add(self, support: AgentSupport) -> None: ...
    def remove(self, name: str) -> None: ...
    def get(self, name_or_command: str) -> AgentSupport | None: ...
    def get_parser(self, name_or_command: str) -> AgentParser: ...
    def get_strategy(self, transport: AgentTransport,
                     command: str | None = None) -> BaseExecutionStrategy: ...
    def list_agents(self) -> tuple[str, ...]: ...
    def by_transport(self, transport: AgentTransport) -> tuple[AgentSupport, ...]: ...
```

`AgentCatalog` is instance-owned — tests construct a fresh catalog per test case
instead of mutating global state. It guards against duplicate names and commands
at `add()` time and supports lookup by name or by command string. The
`get_strategy` method prefers an exact command match before falling back to
the first entry matching the given transport, which lets callers resolve a
strategy without needing the canonical agent name. The module-level singleton
`default_catalog()` exists for production bootstrap but all new code should
prefer explicit injection.

### 2.3 `AgentSpec` — the headless-vs-interactive axis

```python
@dataclass(frozen=True, slots=True)
class AgentSpec:
    name: str
    transport: AgentTransport
    interactive: bool = False
    requires_pty: bool = False
    session_resume_template: str | None = None
    completion_required: bool = False
    subagent_capable: bool = False
```

`AgentSpec` is the single declarative home for the headless-vs-interactive axis.
It replaces the legacy pattern of scattering `interactive=True`, a magic
`session_flag='--resume {}'` default, and ad-hoc PTY logic across
`registration.py`. The frozen dataclass validates its own invariants in
`__post_init__`: `requires_pty=True` requires `interactive=True`;
`session_resume_template` requires `completion_required=True`. The
`from_agent_config` classmethod builds a spec from an `AgentConfig` plus keyword
overrides, keeping the two config shapes in sync.

## 3. Adding a new headless agent in 5 lines

The following snippet is self-contained and will compile when pasted into a test
file that has the standard imports:

```python
from ralph.agents.catalog import AgentCatalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.spec import AgentSpec
from ralph.agents.support import AgentSupport
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport

class _FakeParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()
    def classify_line(self, line):  # type: ignore[override]
        stripped = line.strip()
        result = self.parse_json_line(stripped)
        if result is not None:
            yield result  # type: ignore[misc]
        else:
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)

class _FakeStrategy(BaseExecutionStrategy):
    pass

catalog = AgentCatalog()
support = AgentSupport(
    name="fake-headless",
    spec=AgentSpec(name="fake-headless", transport=AgentTransport.GENERIC),
    parser_factory=_FakeParser,
    strategy_factory=_FakeStrategy,
    config=AgentConfig(cmd="fake-headless"),
)
catalog.add(support)
assert isinstance(catalog.get_parser("fake-headless"), _FakeParser)
```

**What each line does:**
1. **`class _FakeParser(ParserTemplateBase)`** — define a minimal parser that
   delegates JSON lines through `parse_json_line` and wraps non-JSON lines as
   `raw` output.
2. **`class _FakeStrategy(BaseExecutionStrategy)`** — define a strategy with
   default single-process semantics; no methods need overriding.
3. **`catalog = AgentCatalog()` / `support = AgentSupport(...)`** — create a
   fresh catalog and a support bundle. The `AgentSpec` uses
   `AgentTransport.GENERIC` for headless.
4. **`catalog.add(support)`** — register. This also populates the legacy
   module-level dicts for backward compatibility.
5. **`assert isinstance(...)`** — verify the round-trip works. The catalog
   returns a fresh parser instance and a strategy instance, both of the
   expected types.

## 4. Adding a new interactive agent in 5 lines

```python
from ralph.agents.catalog import AgentCatalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.spec import AgentSpec
from ralph.agents.support import AgentSupport
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport

class _FakeParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()
    def classify_line(self, line):  # type: ignore[override]
        stripped = line.strip()
        result = self.parse_json_line(stripped)
        if result is not None:
            yield result  # type: ignore[misc]
        else:
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)

class _FakeStrategy(BaseExecutionStrategy):
    pass

catalog = AgentCatalog()
support = AgentSupport(
    name="fake-interactive",
    spec=AgentSpec(
        name="fake-interactive",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        interactive=True,
        requires_pty=True,
        session_resume_template="--resume {}",
        completion_required=True,
    ),
    parser_factory=_FakeParser,
    strategy_factory=_FakeStrategy,
    config=AgentConfig(cmd="fake-interactive"),
)
catalog.add(support)
assert isinstance(catalog.get_parser("fake-interactive"), _FakeParser)
```

**What differs from the headless recipe:**
- `transport=AgentTransport.CLAUDE_INTERACTIVE` instead of `GENERIC`.
- `interactive=True` — signals this agent uses a PTY-backed interactive session.
- `requires_pty=True` — the execution environment must allocate a pseudo-terminal.
- `session_resume_template="--resume {}"` — the CLI flag template for resuming
  a prior session (passes the session ID through `str.format`).
- `completion_required=True` — the engine must wait for an explicit completion
  signal (a stop event from the parser) before treating exit as a clean
  termination.

## 5. Parser template

### 5.1 The four reusable pieces

`ParserTemplateBase` (`ralph/agents/parsers/_template.py`) provides four
reusable methods that parser subclasses call or override:

**`parse_json_line(line: str) -> AgentOutputLine | None`**

Tries to parse a raw line as JSON. Returns:
- `AgentOutputLine(type='raw', ...)` when the line is not valid JSON or is a
  JSON value that is not a dict (bare string, number, array).
- The return value of `_classify_json_object(dict, raw)` when the line is a
  valid JSON dict.
- `None` when `_classify_json_object` returns `None` (signalling the template
  did not handle it and the subclass's `classify_line` should).

```python
def parse_json_line(self, line: str) -> AgentOutputLine | None:
    stripped = line.strip()
    try:
        parsed: object = json.loads(stripped, strict=False)
    except JSONDecodeError:
        return AgentOutputLine(type="raw", content=stripped, raw=line)
    if not isinstance(parsed, dict):
        return AgentOutputLine(type="raw", content=stripped, raw=line)
    return self._classify_json_object(cast("dict[str, object]", parsed), line)
```

**`is_stop_event(event_type: str) -> bool`**

Checks whether `event_type` is a known stop marker (membership test against
`_STOP_EVENT_TYPES`). Subclasses declare their stop types as a class-variable
`frozenset`:

```python
class MyParser(ParserTemplateBase):
    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset({"done", "error"})
```

**`flush_accumulators() -> Iterator[AgentOutputLine]`**

Drains all pending `TextAccumulator` instances and yields their content as
`AgentOutputLine(type='text', ...)`. Called automatically at the end of
`parse` and manually when a subclass detects an end-of-block signal. The
default implementation pops each key from `self._accumulators` and flushes
the accumulator.

```python
def flush_accumulators(self) -> Iterator[AgentOutputLine]:
    for key in list(self._accumulators.keys()):
        acc = self._accumulators.pop(key)
        yield from acc.flush(kind="text")
```

**`parse(lines: Iterator[str]) -> Iterator[AgentOutputLine]`**

The template method that defines the overall parsing algorithm. It iterates
each line through `classify_line`, then drains accumulators after the final
line:

```python
def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
    for line in lines:
        yield from self.classify_line(line)
    yield from self.flush_accumulators()
```

Subclasses override `classify_line` (and optionally `_classify_json_object`
for JSON-dict dispatch) to inject parser-specific logic. They rarely need to
override `parse` itself.

### 5.2 Checklist for parser authors

- [ ] Does the parser declare `_STOP_EVENT_TYPES` as a `ClassVar[frozenset[str]]`?
- [ ] Does `classify_line` yield at most one `AgentOutputLine` per input line
      (unless the line naturally produces multiple outputs like accumulator
      flushes)?
- [ ] Is non-JSON output wrapped as `AgentOutputLine(type='raw', ...)` so the
      engine never drops unstructured text?
- [ ] Are text accumulators created lazily (`TextAccumulator` instantiated on
      first use) rather than pre-allocated?
- [ ] Does the parser avoid mutable default arguments in `__init__`?
- [ ] If the parser overrides `_classify_json_object`, does it return `None`
      for unrecognised dicts to let the subclass path handle them?
- [ ] Are all `__init__` call chains compatible with a zero-arg factory call
      (i.e. `parser_factory()` returns a ready-to-use parser)?

## 6. Watch loop template

### 6.1 The `wait_until` + `signal_activity` model

`WatchLoopBase` (`ralph/agents/idle_watchdog/_watch_loop_base.py`) provides a
`wait_until` template method that polls a predicate at a configurable interval.
An injected `Clock` (the `Clock` protocol from `ralph/agents/clock`) keeps the
base fully testable with `FakeClock` — no real wall-clock waits in unit tests.
A `threading.Event` (`self._event`) allows `signal_activity` to wake a blocked
`wait_until` early from another thread.

```python
class WatchLoopBase:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self._event = threading.Event()

    def wait_until(
        self,
        *,
        predicate: Callable[[], T | None],
        timeout_s: float,
        poll_interval_s: float,
        on_tick: Callable[[T | None], None] | None = None,
    ) -> T | None:
        deadline = self.clock.monotonic() + timeout_s
        result = predicate()
        if result:
            return result
        while self.clock.monotonic() < deadline:
            if on_tick is not None:
                on_tick(result)
            self.clock.wait_for_event(self._event, poll_interval_s)
            result = predicate()
            if result:
                return result
        return None

    def signal_activity(self) -> None:
        self._event.set()
        self._event.clear()
```

**Flow:**
1. Entry check — the predicate is evaluated immediately. If true, returns with
   zero clock budget consumed.
2. Poll loop — on each tick the poll interval is spent in
   `clock.wait_for_event(event, poll_interval_s)`, which either waits the full
   duration or returns early when `signal_activity()` fires the event from
   another thread.
3. After each wait, the predicate is re-evaluated. If it returns truthy the
   method returns that value; if the deadline passes, returns `None`.

The `on_tick` callback (called after each predicate check that did not return)
enables progress/debug logging without subclass hook pollution.

### 6.2 Checklist for watchdog authors

- [ ] Does the watchdog accept an injected `Clock` in `__init__` and pass it to
      `super().__init__(clock)`?
- [ ] Are all time-based decisions (`timeout_s`, `poll_interval_s`) based on
      `self.clock.monotonic()` rather than `time.monotonic()` or
      `datetime.now()`?
- [ ] Does the watchdog call `self.signal_activity()` whenever it detects
      meaningful agent output, so that a blocked `wait_until` in another thread
      wakes up early?
- [ ] Is the predicate in `wait_until` a pure function of observable state, not
      a side-effecting operation?
- [ ] Does the watchdog test use `FakeClock` (from `tests/agents/clock.py` or
      equivalent) to avoid real wall-clock delays?
- [ ] Is `poll_interval_s` set to a value that balances responsiveness (low)
      against CPU overhead (high)? Common default: 0.1 s.
- [ ] Has the watchdog been checked with the MCP timeout audit
      (`ralph/testing/audit_mcp_timeout.py`)? Every blocking call in
      `ralph/mcp/` must carry a bounded, fail-closed `timeout=`.

## 7. Migration from the legacy 3 module-level dicts

Before the consolidated model, agent registrations were scattered across three
module-level dicts in `ralph/agents/parsers/__init__.py` and
`ralph/agents/execution_state/_factory.py`: `_PARSER_REGISTRY`,
`_CUSTOM_COMMAND_REGISTRY`, and `_STRATEGY_DISPATCH`. New code would mutate
all three dicts directly (or indirectly via `register_agent_support`), leading
to inconsistent state when one dict was updated but not the others.

These three dicts are now **deprecated write-through state**. Every
`AgentCatalog.add(support)` call atomically populates all three dicts via
`_write_through()`, so existing code that reads from the module-level dicts
continues to work without changes. However, **new code MUST use
`AgentCatalog` directly** — construct or inject a catalog instance, call
`catalog.add(support)`, and resolve registrations through `catalog.get()`,
`catalog.get_parser()`, and `catalog.get_strategy()`. The deprecated dicts
will be removed in a future release.

## 8. See also

- [Testing guide](testing-guide.md) — patterns for testable agent registrations,
  fake parsers, and fake strategies.
- [`ralph/agents/_contracts.py`](../ralph-workflow/ralph/agents/_contracts.py) —
  `StrategyFactory` protocol and re-exported `Clock` / `SystemClock`.
- [`ralph/agents/catalog.py`](../ralph-workflow/ralph/agents/catalog.py) —
  `AgentCatalog` implementation, singleton management, and legacy write-through.
- [`ralph/agents/spec.py`](../ralph-workflow/ralph/agents/spec.py) — `AgentSpec`
  dataclass with invariants and `from_agent_config` factory.
- [`ralph/agents/support.py`](../ralph-workflow/ralph/agents/support.py) —
  `AgentSupport` dataclass and `from_registration_kwargs` legacy-compat factory.
- [`ralph/agents/invocation_context.py`](../ralph-workflow/ralph/agents/invocation_context.py) —
  `InvocationContext` frozen dataclass threading DI seams.
- [`ralph/agents/parsers/_template.py`](../ralph-workflow/ralph/agents/parsers/_template.py) —
  `ParserTemplateBase` template-method class.
- [`ralph/agents/idle_watchdog/_watch_loop_base.py`](../ralph-workflow/ralph/agents/idle_watchdog/_watch_loop_base.py) —
  `WatchLoopBase` template-method class.
