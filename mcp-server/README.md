# mcp-server

MCP (Model Context Protocol) server implementation for Ralph workflow orchestration.

## Overview

This crate provides the MCP server that enables Ralph to communicate with AI agents (Claude Code, Codex, OpenCode) via JSON-RPC over stdio or Unix sockets.

## Architecture

```
Agent Process <--JSON-RPC--> mcp-server <--> Ralph Workflow
                                          |
                                          +--> HostSession (capabilities)
                                          |
                                          +--> HostWorkspace (file I/O)
```

### Module Organization

| Module | Type | Purpose |
|--------|------|---------|
| `protocol/` | Pure | JSON-RPC types, capability declarations, tool definitions |
| `io/` | Boundary | Transport framing, socket handling, stdio I/O |
| `dispatch/` | Application | Tool registry, handler dispatch, capability gating |

### Boundary Rules

- **`protocol/`** - Pure data types, no side effects
- **`io/`** - Boundary module (Dylint-recognized), all actual I/O lives here
- **`dispatch/`** - Application logic, handlers are pure functions

## RPC Contract

### Supported Methods

| Method | Description | Capability Required |
|--------|-------------|---------------------|
| `initialize` | Handshake, exchange capabilities | None |
| `ping` | Liveness check | None |
| `tools/list` | List available tools | None |
| `tools/call` | Invoke a tool | Tool-specific |

### Error Codes

| Code | Meaning |
|------|---------|
| -32700 | Parse error - invalid JSON |
| -32600 | Invalid request - missing required fields |
| -32601 | Method not found - unknown method |
| -32602 | Invalid params - wrong parameter types |
| -32603 | Internal error - server-side failure |
| -32000 | Tool error - tool execution failed |
| -32001 | Server not initialized |

### Protocol Versioning

Current: `2024-11-05`

The protocol version is negotiated during the `initialize` handshake. The server advertises its supported version in the `InitializeResult`.

## Host Trait Implementation

To use mcp-server with Ralph, implement the `HostSession` and `HostWorkspace` traits from `dispatch`:

```rust
use mcp_server::dispatch::{HostSession, HostWorkspace, PolicyOutcome};

struct RalphHostSession {
    agent_session: AgentSession,
}

impl HostSession for RalphHostSession {
    fn session_id(&self) -> &str {
        &self.agent_session.session_id
    }

    fn check_capability(&self, cap: &str) -> PolicyOutcome {
        self.agent_session.check_capability(cap)
    }

    fn is_parallel_worker(&self) -> bool {
        self.agent_session.is_parallel_worker()
    }

    fn check_edit_area(&self, path: &str) -> PolicyOutcome {
        self.agent_session.check_edit_area(path)
    }
}
```

## Testing

Run tests with:

```bash
cargo test -p mcp-server
```

Run integration tests with:

```bash
cargo test -p mcp-server --test integration
```

## Dependencies

See `Cargo.toml` for the full dependency list. Key dependencies:

- `serde` / `serde_json` - JSON serialization
- `tokio` - Async runtime
- `thiserror` - Error handling
