"""Pin the per-transport MCP-endpoint delivery contract inside the 60-second gate.

The smoke harness's multimodal test suite (``tests/test_smoke_multimodal_end_to_end.py``)
is the only proof that each transport hands Ralph's MCP endpoint to its
spawned agent. The smoke suite carries the ``smoke`` marker so no regular
``make`` target collects it -- an endpoint regression like the S-1 defect
can therefore leave ``make verify`` green while breaking the multimodal
contract criterion 5 demands of every transport.

This guard closes that gap by parametrizing over an explicit per-transport
delivery-channel table:

- Every non-GENERIC transport must echo the endpoint back as
  ``ResolvedInvocationRuntime.mcp_endpoint`` (the one uniform observation
  every resolver exposes).
- Each transport must additionally satisfy the channel its own spawned
  agent reads from (the ``OPENCODE_CONFIG_CONTENT`` JSON, the
  ``NANOCODER_MCPSERVERS`` JSON, the ``<CODEX_HOME>/config.toml`` TOML,
  the ``<workspace>/.cursor/mcp.json`` JSON, the generated
  ``RALPH_PI_MCP_EXTENSION`` TypeScript file). The plan's evidence names
  the per-transport channel explicitly because ``PiRuntimeResolver.resolve``
  deliberately pops ``RALPH_MCP_ENDPOINT`` out of ``runtime_env`` and hands
  the endpoint over inside the generated extension file, so no universal
  env assertion can cover every transport.
- The ``GENERIC`` transport raises :class:`UnsupportedMcpTransportError`
  when an endpoint is supplied, so the test guards that contract too.
- A new ``AgentTransport`` value added to ``RUNTIME_RESOLVERS`` without a
  table entry here fails the run, mirroring the
  ``tests/agents/invoke/test_dispatch_table_covers_every_transport.py``
  precedent in this directory.

The case is offline and fast:

- ``discover_http_mcp_tool_names`` is monkey-patched on the
  ``ralph.agents.invoke`` seam (``_InvokeCompatibilitySeam``) so the
  Nanocoder branch's tool discovery never dials out.
- The autouse ``tests/conftest.py::_isolate_process_home`` keeps
  Cursor's user-global ``~/.cursor/mcp.json`` write inside the
  per-worker temp HOME, so two parallel workers never stomp on each
  other's user-global config.
- Every ``ResolvedInvocationRuntime.cleanup`` is invoked so the per-run
  Codex home allocation and the per-run Cursor config restore run
  inside the test (no leaked tempdirs, no global state mutated by the
  next test in the worker).

The single shared ``endpoint`` value is the only thing every assertion
references, so a regression in the contract reads as the failed
``mcp_endpoint`` echo first and the missing channel second -- both
together identify the resolver whose return shape changed.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke._errors import UnsupportedMcpTransportError
from ralph.agents.invoke._runtime_resolvers import RUNTIME_RESOLVERS
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from ralph.agents.invoke._runtime_resolvers import ResolvedInvocationRuntime


#: Shared endpoint every assertion references. ``127.0.0.1:9`` is the
#: discard port; no real server dials it because the seam is monkey-patched.
_ENDPOINT = "http://127.0.0.1:9/mcp"


def _no_tool_discovery(endpoint: str) -> list[str]:
    """Drop-in for ``discover_http_mcp_tool_names`` that never dials out.

    The Nanocoder resolver's ``_canonical_http_mcp_tool_names`` helper
    calls the seam to build the ``always_allow`` allowlist. Without this
    monkey-patch the test would dial ``127.0.0.1:9`` (refused), hit a
    timeout, and the entire endpoint-delivery contract guard would fail
    for the wrong reason. Returning an empty list is faithful: an empty
    allowlist is what the production Nanocoder path produces when the
    endpoint discovery itself fails (see
    ``_canonical_http_mcp_tool_names``'s ``PreflightError`` branch).
    """
    return []


@pytest.fixture(autouse=True)
def _stub_tool_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``discover_http_mcp_tool_names`` to the offline stub for every case."""
    monkeypatch.setattr(
        "ralph.agents.invoke.discover_http_mcp_tool_names", _no_tool_discovery
    )


def _make_config(transport: AgentTransport) -> AgentConfig:
    """Return a minimal ``AgentConfig`` for ``transport``.

    The cmd field is ``"<name>"`` -- a non-PATH string so the resolvers
    that split it (e.g. Codex) see a deterministic first token without
    invoking any external lookup.
    """
    return AgentConfig(cmd=transport.value.lower(), transport=transport)


def _resolve(
    transport: AgentTransport, workspace_path: Path
) -> ResolvedInvocationRuntime:
    """Resolve ``transport``'s runtime with the shared endpoint + empty base env."""
    resolver_cls = RUNTIME_RESOLVERS[transport]
    return resolver_cls().resolve(
        config=_make_config(transport),
        extra_env={"RALPH_MCP_ENDPOINT": _ENDPOINT, "RALPH_MCP_RUN_ID": "test"},
        workspace_path=workspace_path,
        base_env={},
    )


#: Per-transport delivery-channel table. Each entry is
#: ``(transport, env_keys_with_endpoint, file_channel_locator)``. The
#: ``env_keys_with_endpoint`` tuple lists the ``agent_env`` keys whose
#: string value MUST contain the endpoint. The
#: ``file_channel_locator`` callable returns the file the resolver
#: additionally writes the endpoint into (or ``None`` when the channel
#: is purely env-based). The table covers every transport in
#: ``RUNTIME_RESOLVERS`` except ``GENERIC``, which has its own
#: contract test.
_DELIVERY_CHANNELS: tuple[
    tuple[AgentTransport, tuple[str, ...], Callable[[Path], Path | None]],
    ...
] = (
    # claude / claude_interactive: agent_env is the only channel.
    (AgentTransport.CLAUDE, ("RALPH_MCP_ENDPOINT",), lambda _ws: None),
    (
        AgentTransport.CLAUDE_INTERACTIVE,
        ("RALPH_MCP_ENDPOINT",),
        lambda _ws: None,
    ),
    # agy: agent_env is the only channel.
    (AgentTransport.AGY, ("RALPH_MCP_ENDPOINT",), lambda _ws: None),
    # cursor: env carries the endpoint AND the workspace-local
    # ``.cursor/mcp.json`` is the channel the Cursor CLI reads.
    (
        AgentTransport.CURSOR,
        ("RALPH_MCP_ENDPOINT",),
        lambda ws: ws / ".cursor" / "mcp.json",
    ),
    # codex: env carries the endpoint AND ``<CODEX_HOME>/config.toml``
    # is the channel the Codex CLI reads. The file_channel_locator is
    # resolved dynamically in ``test_codex_config_toml_carries_endpoint``
    # because the per-invocation CODEX_HOME is allocated inside the
    # resolver and lives in ``agent_env``.
    (
        AgentTransport.CODEX,
        ("RALPH_MCP_ENDPOINT",),
        lambda _ws: None,
    ),
    # opencode: env carries the endpoint AND ``OPENCODE_CONFIG_CONTENT``
    # (an env value, not a file) embeds the MCP entry the OpenCode CLI
    # reads. The file_channel_locator returns ``None`` because the
    # channel IS the env value -- the table asserts the URL is in
    # ``OPENCODE_CONFIG_CONTENT``'s string.
    (
        AgentTransport.OPENCODE,
        ("RALPH_MCP_ENDPOINT", "OPENCODE_CONFIG_CONTENT"),
        lambda _ws: None,
    ),
    # nanocoder: env carries the endpoint AND ``NANOCODER_MCPSERVERS``
    # (an env value) embeds the MCP entry the Nanocoder CLI reads.
    (
        AgentTransport.NANOCODER,
        ("RALPH_MCP_ENDPOINT", "NANOCODER_MCPSERVERS"),
        lambda _ws: None,
    ),
    # pi: env deliberately does NOT carry the endpoint -- instead the
    # ``RALPH_PI_MCP_EXTENSION`` env var points at a generated
    # TypeScript extension file whose text embeds the endpoint.
    (
        AgentTransport.PI,
        ("RALPH_PI_MCP_EXTENSION",),
        lambda ws: ws / ".agent" / "tmp" / "ralph_pi_mcp_extension.ts",
    ),
)


class TestRuntimeResolverEndpointDelivery:
    """Pin the per-transport MCP-endpoint delivery contract for every resolver."""

    def test_delivery_table_covers_every_non_generic_transport(self) -> None:
        """Every transport in ``RUNTIME_RESOLVERS`` (except ``GENERIC``) has a table entry.

        A new transport added without registering the channel it ships
        the endpoint over is exactly the silent regression the multimodal
        smoke guard prevents; this assertion forces the maintainer to
        extend the table in lockstep.
        """
        table_transports = {entry[0] for entry in _DELIVERY_CHANNELS}
        registered_transports = {
            transport
            for transport in RUNTIME_RESOLVERS
            if transport is not AgentTransport.GENERIC
        }
        missing = registered_transports - table_transports
        assert not missing, (
            f"_DELIVERY_CHANNELS is missing entries for: "
            f"{sorted(t.name for t in missing)}. "
            f"Add a (transport, env_keys, file_locator) tuple so the "
            f"per-transport channel assertion covers the new transport."
        )
        extras = table_transports - registered_transports
        assert not extras, (
            f"_DELIVERY_CHANNELS has stale entries for: "
            f"{sorted(t.name for t in extras)}. "
            f"Either the transport was removed from RUNTIME_RESOLVERS or "
            f"this entry is orphaned; remove the row."
        )

    def test_delivery_table_covers_exactly_one_generic_case(self) -> None:
        """``GENERIC`` has no channel entry -- its contract is the negative one."""
        table_transports = {entry[0] for entry in _DELIVERY_CHANNELS}
        assert AgentTransport.GENERIC not in table_transports, (
            "GENERIC transport must not appear in _DELIVERY_CHANNELS; "
            "it raises UnsupportedMcpTransportError and has no delivery "
            "channel to assert."
        )

    @pytest.mark.parametrize(
        ("transport", "env_keys", "_file_locator"),
        _DELIVERY_CHANNELS,
        ids=[entry[0].name for entry in _DELIVERY_CHANNELS],
    )
    def test_mcp_endpoint_echo(
        self,
        transport: AgentTransport,
        env_keys: tuple[str, ...],
        _file_locator: Callable[[Path], Path | None],
        tmp_path: Path,
    ) -> None:
        """Every non-GENERIC resolver echoes the endpoint back as ``mcp_endpoint``.

        This is the one uniform observation the S-1 evidence identifies
        as the regression-catching invariant: the resolver's
        ``ResolvedInvocationRuntime.mcp_endpoint`` MUST equal the
        endpoint it was handed. A resolver that returns ``None`` (or a
        different value) here is the failure shape the
        OpencodeRuntimeResolver had pre-fix, where the endpoint never
        reached the agent because the resolver returned early.
        """
        runtime = _resolve(transport, tmp_path)
        assert runtime.mcp_endpoint == _ENDPOINT, (
            f"transport {transport.name!r}: mcp_endpoint echoed "
            f"{runtime.mcp_endpoint!r}, expected {_ENDPOINT!r}"
        )

    @pytest.mark.parametrize(
        ("transport", "env_keys", "_file_locator"),
        _DELIVERY_CHANNELS,
        ids=[entry[0].name for entry in _DELIVERY_CHANNELS],
    )
    def test_env_keys_carry_endpoint(
        self,
        transport: AgentTransport,
        env_keys: tuple[str, ...],
        _file_locator: Callable[[Path], Path | None],
        tmp_path: Path,
    ) -> None:
        """Every channel entry listed for ``transport`` carries the endpoint string.

        The resolver contract for ``agent_env`` is that every key in
        ``env_keys`` holds a string whose value contains the endpoint.
        For transports that hand the endpoint over via a file (Pi),
        the env key is the path to that file -- the file's text is
        asserted in ``test_file_channel_carries_endpoint`` instead.
        """
        runtime = _resolve(transport, tmp_path)
        env = runtime.agent_env or {}
        for key in env_keys:
            assert key in env, (
                f"transport {transport.name!r}: agent_env missing key "
                f"{key!r}; the table declared it as a delivery channel"
            )
            value = env[key]
            if transport is AgentTransport.PI:
                # Pi's env key is a path; the file contents carry the endpoint.
                continue
            assert isinstance(value, str), (
                f"transport {transport.name!r}: agent_env[{key!r}] is "
                f"not a string (got {type(value).__name__})"
            )
            assert _ENDPOINT in value, (
                f"transport {transport.name!r}: agent_env[{key!r}] does "
                f"not contain the endpoint; the channel contract is broken"
            )

    @pytest.mark.parametrize(
        ("transport", "env_keys", "file_locator"),
        [
            entry
            for entry in _DELIVERY_CHANNELS
            if entry[2](Path("/tmp")).__class__ is Path.__class__
        ],
        ids=[
            entry[0].name
            for entry in _DELIVERY_CHANNELS
            if entry[2](Path("/tmp")).__class__ is Path.__class__
        ],
    )
    def test_file_channel_carries_endpoint(
        self,
        transport: AgentTransport,
        env_keys: tuple[str, ...],
        file_locator: Callable[[Path], Path | None],
        tmp_path: Path,
    ) -> None:
        """For transports whose channel is a workspace-local file, the file's text contains the endpoint.

        Pi (``RALPH_PI_MCP_EXTENSION`` -> ``.agent/tmp/ralph_pi_mcp_extension.ts``)
        and Cursor (``~/.cursor/mcp.json`` + ``<workspace>/.cursor/mcp.json``)
        use file channels; this assertion prevents the file-write path
        from silently regressing (e.g. a stale workspace scope, a
        typo in the path, or a write that the cleanup hook deleted).
        """
        runtime = _resolve(transport, tmp_path)
        try:
            file_path = file_locator(tmp_path)
            if file_path is None:
                pytest.skip("no file channel for this transport")
            assert file_path.is_file(), (
                f"transport {transport.name!r}: expected file channel "
                f"at {file_path} does not exist"
            )
            content = file_path.read_text(encoding="utf-8")
            assert _ENDPOINT in content, (
                f"transport {transport.name!r}: file channel at "
                f"{file_path} does not contain the endpoint; the "
                f"channel contract is broken"
            )
        finally:
            if runtime.cleanup is not None:
                runtime.cleanup()

    def test_codex_config_toml_carries_endpoint(self, tmp_path: Path) -> None:
        """The ``<CODEX_HOME>/config.toml`` file the Codex CLI reads contains the endpoint.

        Codex's per-invocation CODEX_HOME is allocated inside the
        resolver and lives in ``agent_env``, so the test resolves the
        env first, then derives the config.toml path from the env
        value. The cleanup hook (released in the finally block)
        rmtree's the per-run directory so the assertion does not leak
        tempdirs.
        """
        runtime = _resolve(AgentTransport.CODEX, tmp_path)
        try:
            codex_home = runtime.agent_env["CODEX_HOME"]
            config_toml = Path(codex_home) / "config.toml"
            assert config_toml.is_file(), (
                f"CODEX config.toml not written at {config_toml}"
            )
            content = config_toml.read_text(encoding="utf-8")
            assert _ENDPOINT in content, (
                f"CODEX config.toml at {config_toml} does not contain "
                f"the endpoint; the per-run MCP wiring is broken"
            )
        finally:
            if runtime.cleanup is not None:
                runtime.cleanup()

    def test_generic_resolver_raises_when_endpoint_supplied(
        self, tmp_path: Path
    ) -> None:
        """``DefaultRuntimeResolver`` raises ``UnsupportedMcpTransportError`` for ``GENERIC``.

        Mirrors the AGENTS.md "no special-casing" rule: the negative
        contract is exactly as documented -- ``GENERIC`` is the only
        transport that REFUSES an MCP endpoint, and the refusal is a
        user-visible error, not a silent no-op.
        """
        resolver_cls = RUNTIME_RESOLVERS[AgentTransport.GENERIC]
        config = _make_config(AgentTransport.GENERIC)
        with pytest.raises(UnsupportedMcpTransportError):
            resolver_cls().resolve(
                config=config,
                extra_env={"RALPH_MCP_ENDPOINT": _ENDPOINT, "RALPH_MCP_RUN_ID": "test"},
                workspace_path=tmp_path,
                base_env={},
            )

    def test_generic_resolver_succeeds_without_endpoint(
        self, tmp_path: Path
    ) -> None:
        """``DefaultRuntimeResolver`` returns a minimal runtime when no endpoint is supplied.

        The negative path is paired with a positive path so a future
        maintainer cannot turn ``GENERIC`` into a permanent refusal.
        """
        resolver_cls = RUNTIME_RESOLVERS[AgentTransport.GENERIC]
        config = _make_config(AgentTransport.GENERIC)
        runtime = resolver_cls().resolve(
            config=config,
            extra_env=None,
            workspace_path=tmp_path,
            base_env={},
        )
        assert runtime.mcp_endpoint is None
        assert runtime.agent_env is None or "RALPH_MCP_ENDPOINT" not in (
            runtime.agent_env or {}
        )

    def test_opencode_resolver_returns_endpoint_after_s2_fix(
        self, tmp_path: Path
    ) -> None:
        """``OpencodeRuntimeResolver`` returns the endpoint after the S-2 fix.

        Pre-fix the OpencodeRuntimeResolver returned
        ``ResolvedInvocationRuntime(agent_env=runtime_env or None)`` (no
        ``mcp_endpoint``) when the endpoint was passed via ``extra_env``
        but ``base_env`` had it too -- a precedence bug that left the
        multimodal fact graded ``ABSENT``. The S-2 fix wires the
        endpoint into the resolved runtime unconditionally; reverting
        the fix turns this assertion red.
        """
        runtime = _resolve(AgentTransport.OPENCODE, tmp_path)
        assert runtime.mcp_endpoint == _ENDPOINT, (
            "OpencodeRuntimeResolver regression: mcp_endpoint not set "
            "after the S-2 fix; the multimodal smoke for OPENCODE will "
            "re-fail with the S-1 evidence 'endpoint never reached the agent'"
        )
