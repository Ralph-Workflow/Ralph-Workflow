# MCP Transport Research for RFC-009

**Researched:** 2026-03-25
**Domain:** Rust MCP server crates and transport mechanism tradeoffs
**Confidence:** HIGH

## Executive Summary

The Rust MCP ecosystem has a mature, well-maintained SDK (`rust-mcp-sdk`) that supports both stdio and Streamable HTTP transports. For Ralph's RFC-009 implementation, **stdio transport is recommended as the v1 approach** because it aligns with Ralph's existing subprocess-based agent execution model and provides the simplest path to implementing brokered tool calls.

---

## Finding 1: Viable Rust MCP Crates

### Primary: rust-mcp-sdk

**Repository:** https://github.com/rust-mcp-stack/rust-mcp-sdk
**Crates.io:** https://crates.io/crates/rust-mcp-sdk
**Current Version:** 0.9.0
**License:** MIT

A high-performance, asynchronous Rust toolkit for building MCP servers and clients. Key features:

- ✅ Full MCP protocol specification support (2025-11-25)
- ✅ **Transports:** Stdio, Streamable HTTP, and backward-compatible SSE
- ✅ Lightweight Axum-based server for HTTP transports
- ✅ Multi-client concurrency (HTTP only)
- ✅ Session management and resumability
- ✅ OAuth authentication support
- ✅ Message observer for telemetry
- ✅ Health check endpoints
- ✅ Procedural macros for tool definitions
- ✅ Used in production by moon (repository management tool), mistral.rs, and others

**Maturity Assessment:** HIGH - Active development, clear documentation, production usage evidence

### Related Projects Using rust-mcp-sdk

| Project | Description | Stars |
|---------|-------------|-------|
| rust-mcp-filesystem | High-performance filesystem operations | 141 |
| rust-docs-mcp-server | Fetches current Rust crate docs | 264 |
| notify-mcp | Desktop notifications | - |
| Vaiz/rust-mcp-server | Build, test, analyze Rust code | - |

---

## Finding 2: MCP Specification Transport Details

### Official MCP Transport Mechanisms (2025-11-25)

The MCP specification defines two standard transports:

#### 1. Stdio Transport

- **Mechanism:** Client launches MCP server as subprocess
- **Communication:** Server reads from stdin, writes to stdout
- **Message Format:** Newline-delimited JSON-RPC messages
- **Logging:** Server MAY write to stderr for logs (client MAY capture/ignore)
- **Constraints:**
  - Single client only (one-to-one relationship)
  - No built-in session management
  - No resumability
  - Process lifecycle tied to parent

**Specification Source:** https://modelcontextprotocol.io/specification/2025-11-25/basic/transports.md

#### 2. Streamable HTTP Transport (Recommended)

- **Mechanism:** Independent server process handling multiple connections
- **Communication:** HTTP POST for requests, HTTP GET for SSE streams
- **Features:**
  - Multi-client support
  - Session management with unique IDs
  - Resumability via event IDs
  - Server-Sent Events (SSE) for streaming
  - OAuth authentication support
- **Security Requirements:**
  - Validate Origin header to prevent DNS rebinding
  - Bind to localhost (127.0.0.1) when running locally
  - Implement authentication

**Specification Source:** https://modelcontextprotocol.io/specification/2025-11-25/basic/transports.md

---

## Finding 3: Stdio vs Streamable HTTP Tradeoffs

| Factor | Stdio | Streamable HTTP |
|--------|-------|-----------------|
| **Complexity** | Low - simple subprocess | Medium - HTTP server setup |
| **Multi-client** | No | Yes |
| **Session management** | No | Yes (MCP-Session-Id) |
| **Resumability** | No | Yes |
| **Security** | Simple - process isolation | Requires Origin validation, auth |
| **Ralph alignment** | ✅ Matches existing subprocess model | Requires new infrastructure |
| **Existing integration** | Claude Code, OpenCode, Codex CLI all use stdio | Not applicable to CLI agents |
| **Parallel workers** | One per agent process | Could share server |

---

## Finding 4: Recommendation for RFC-009

### Recommended: Stdio Transport for V1

**Rationale:**

1. **Matches Ralph's execution model:** Ralph already spawns agent CLIs as subprocesses. Stdio transport is the natural fit.

2. **Simpler implementation:** No HTTP server setup, no network configuration, no security considerations for local-only communication.

3. **Agent compatibility:** Claude Code, OpenCode, and Codex CLI all communicate via stdio. Using stdio means direct integration without protocol translation.

4. **V1 scope alignment:** RFC-009 V1 focuses on brokered tool calls, session capabilities, and audit trails. Stdio provides a clean starting point.

5. **Future-proofing:** The rust-mcp-sdk makes it straightforward to add Streamable HTTP support later if needed (e.g., for distributed scenarios or multi-agent coordination).

### Implementation Path

1. **Phase 1:** Add `rust-mcp-sdk` dependency with `stdio` and `server` features
2. **Phase 2:** Implement `ServerHandler` trait for Ralph's tool broker
3. **Phase 3:** Wrap agent CLI spawning to use `StdioTransport`
4. **Phase 4:** Add session handshake with capability envelope
5. **Phase 5:** Implement brokered tool calls with policy evaluation
6. **Future:** Add Streamable HTTP for parallel worker coordination if needed

### Cargo Configuration

```toml
[dependencies]
rust-mcp-sdk = { version = "0.9.0", features = ["server", "macros", "stdio"] }
```

---

## Finding 5: XML Artifact Integration

The RFC specifies that XML artifacts should coexist during migration. With stdio transport:

- **Artifact submission** can be implemented as an MCP tool (e.g., `artifact.submit`)
- **Validation** remains XSD-based but happens through the broker
- **Transition** is gradual: runtime interactions via MCP, artifacts still XML until later phases

---

## Sources

### Primary (HIGH confidence)
- rust-mcp-sdk README: https://github.com/rust-mcp-stack/rust-mcp-sdk
- MCP Specification (Transports): https://modelcontextprotocol.io/specification/2025-11-25/basic/transports.md
- crates.io: https://crates.io/crates/rust-mcp-sdk

### Secondary (MEDIUM confidence)
- GitHub search results for "mcp server rust"
- Projects using rust-mcp-sdk (moon, mistral.rs)

---

## Research Gaps / Validation Flags

- **Not validated:** Actual integration complexity with Ralph's reducer architecture (requires implementation spike)
- **Not validated:** Performance characteristics of stdio vs HTTP for high-frequency tool calls
- **Recommendation assumes:** Single-agent-per-process model (existing Ralph behavior)

---

*Research completed: 2026-03-25*
*Ready for implementation decision: YES*
