# MCP Shim Audit

## Flat Shim Classification

### Pure Re-export Shims (delete after rewriting importers):
- `transport.py` → `ralph.mcp.protocol.transport`
- `startup.py` → `ralph.mcp.protocol.startup`
- `session.py` → `ralph.mcp.protocol.session`
- `capability_mapping.py` → `ralph.mcp.protocol.capability_mapping`
- `env.py` → `ralph.mcp.protocol.env`
- `tool_names.py` → `ralph.mcp.tools.names`
- `tool_workspace.py` → `ralph.mcp.tools.workspace`
- `tool_git_read.py` → `ralph.mcp.tools.git_read`
- `tool_exec.py` → `ralph.mcp.tools.exec`
- `tool_artifact.py` → `ralph.mcp.tools.artifact`
- `tool_coordination.py` → `ralph.mcp.tools.coordination`
- `tool_websearch.py` → `ralph.mcp.tools.websearch`
- `upstream_client.py` → `ralph.mcp.upstream.client`
- `upstream_config.py` → `ralph.mcp.upstream.config`
- `upstream_models.py` → `ralph.mcp.upstream.models`
- `upstream_registry.py` → `ralph.mcp.upstream.registry`
- `artifacts.py` → `ralph.mcp.artifacts.store`
- `audit_adapter.py` → `ralph.mcp.artifacts.audit_adapter`
- `file_backend.py` → `ralph.mcp.artifacts.file_backend`
- `plan_artifact.py` → `ralph.mcp.artifacts.plan`
- `policy_outcomes.py` → `ralph.mcp.artifacts.policy_outcomes`
- `commit_message.py` → `ralph.mcp.artifacts.commit_message`
- `development_result_artifact.py` → `ralph.mcp.artifacts.development_result`

### Shims with Monkey-patch Hacks (rewrite tests first):
- `upstream_validation.py` → `ralph.mcp.upstream.validation` (has _VALIDATION_IMPL.make_upstream_client hack)
- `agent_transport_probe.py` → `ralph.mcp.upstream.agent_probe` (has _PROBE_IMPL.* hacks)
- `tool_bridge.py` → `ralph.mcp.tools.bridge` (has lazy __getattr__)
- `bridge.py` → `ralph.mcp.artifacts.bridge` (has MCPBridge subclass with extra methods)

### Files with Actual Code (keep):
- `ralph/mcp/__init__.py` - package init with __getattr__ for lazy loading

## Canonical Import Mapping

| Old Import | New Canonical Import |
|------------|---------------------|
| `ralph.mcp.transport` | `ralph.mcp.protocol.transport` |
| `ralph.mcp.startup` | `ralph.mcp.protocol.startup` |
| `ralph.mcp.session` | `ralph.mcp.protocol.session` |
| `ralph.mcp.capability_mapping` | `ralph.mcp.protocol.capability_mapping` |
| `ralph.mcp.env` | `ralph.mcp.protocol.env` |
| `ralph.mcp.tool_bridge` | `ralph.mcp.tools.bridge` |
| `ralph.mcp.tool_names` | `ralph.mcp.tools.names` |
| `ralph.mcp.tool_workspace` | `ralph.mcp.tools.workspace` |
| `ralph.mcp.tool_git_read` | `ralph.mcp.tools.git_read` |
| `ralph.mcp.tool_exec` | `ralph.mcp.tools.exec` |
| `ralph.mcp.tool_artifact` | `ralph.mcp.tools.artifact` |
| `ralph.mcp.tool_coordination` | `ralph.mcp.tools.coordination` |
| `ralph.mcp.tool_websearch` | `ralph.mcp.tools.websearch` |
| `ralph.mcp.upstream_client` | `ralph.mcp.upstream.client` |
| `ralph.mcp.upstream_config` | `ralph.mcp.upstream.config` |
| `ralph.mcp.upstream_models` | `ralph.mcp.upstream.models` |
| `ralph.mcp.upstream_registry` | `ralph.mcp.upstream.registry` |
| `ralph.mcp.upstream_validation` | `ralph.mcp.upstream.validation` |
| `ralph.mcp.agent_transport_probe` | `ralph.mcp.upstream.agent_probe` |
| `ralph.mcp.artifacts` | `ralph.mcp.artifacts.store` |
| `ralph.mcp.bridge` | `ralph.mcp.artifacts.bridge` |
| `ralph.mcp.audit_adapter` | `ralph.mcp.artifacts.audit_adapter` |
| `ralph.mcp.file_backend` | `ralph.mcp.artifacts.file_backend` |
| `ralph.mcp.plan_artifact` | `ralph.mcp.artifacts.plan` |
| `ralph.mcp.policy_outcomes` | `ralph.mcp.artifacts.policy_outcomes` |
| `ralph.mcp.commit_message` | `ralph.mcp.artifacts.commit_message` |
| `ralph.mcp.development_result_artifact` | `ralph.mcp.artifacts.development_result` |
