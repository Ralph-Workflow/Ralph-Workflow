"""Pi-specific MCP transport helpers."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

PI_MCP_EXTENSION_ENV = "RALPH_PI_MCP_EXTENSION"
CallableCleanup = Callable[[], None]


def pi_mcp_extension_path(workspace_path: Path) -> Path:
    """Return the deterministic per-workspace Pi MCP extension path."""
    return workspace_path / ".agent" / "tmp" / "ralph_pi_mcp_extension.ts"


def write_pi_mcp_extension(
    endpoint: str,
    *,
    workspace_path: Path | None,
) -> tuple[Path, CallableCleanup | None]:
    """Write a Pi extension that bridges Pi custom tools to Ralph's MCP server."""
    cleanup: CallableCleanup | None
    if workspace_path is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="ralph-pi-mcp-"))
        extension_path = temp_dir / "ralph_pi_mcp_extension.ts"

        def cleanup() -> None:
            shutil.rmtree(temp_dir, ignore_errors=True)

    else:
        extension_path = pi_mcp_extension_path(workspace_path)
        cleanup = None

    extension_path.parent.mkdir(parents=True, exist_ok=True)
    extension_path.write_text(_render_pi_mcp_extension(endpoint), encoding="utf-8")
    return extension_path, cleanup

def _render_pi_mcp_extension(endpoint: str) -> str:
    endpoint_literal = json.dumps(endpoint)
    return f"""import type {{ ExtensionAPI }} from "@earendil-works/pi-coding-agent";

const ENDPOINT = {endpoint_literal};
const FALLBACK_SCHEMA = {{ type: "object", additionalProperties: true }};

let nextId = 1;
let sessionId: string | undefined;
let initialized = false;

type JsonObject = Record<string, unknown>;
type McpTool = {{
  name: string;
  description?: string;
  inputSchema?: JsonObject;
}};

function isObject(value: unknown): value is JsonObject {{
  return typeof value === "object" && value !== null && !Array.isArray(value);
}}

function textFromContent(content: unknown): string {{
  if (!Array.isArray(content)) return "";
  return content
    .map((item) => {{
      if (!isObject(item)) return "";
      const text = item.text;
      return typeof text === "string" ? text : "";
    }})
    .filter((text) => text.length > 0)
    .join("\\n");
}}

function parseJsonRpcPayload(text: string): JsonObject | null {{
  const trimmed = text.trim();
  if (!trimmed) return null;
  if (!trimmed.startsWith("event:") && !trimmed.startsWith("data:")) {{
    return JSON.parse(trimmed) as JsonObject;
  }}
  for (const line of trimmed.split(/\\r?\\n/)) {{
    if (!line.startsWith("data:")) continue;
    const data = line.slice("data:".length).trim();
    if (data && data !== "[DONE]") return JSON.parse(data) as JsonObject;
  }}
  return null;
}}

async function mcpRequest(
  method: string,
  params: JsonObject,
  signal?: AbortSignal,
  includeId = true,
): Promise<unknown> {{
  const headers: Record<string, string> = {{
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
  }};
  if (sessionId) headers["mcp-session-id"] = sessionId;

  const payload: JsonObject = {{ jsonrpc: "2.0", method, params }};
  if (includeId) payload.id = nextId++;

  const response = await fetch(ENDPOINT, {{
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    signal,
  }});
  const nextSessionId = response.headers.get("mcp-session-id");
  if (nextSessionId) sessionId = nextSessionId;
  if (response.status === 202 || response.status === 204) return undefined;

  const text = await response.text();
  const decoded = parseJsonRpcPayload(text);
  if (!decoded) return undefined;
  const error = decoded.error;
  if (isObject(error)) {{
    const message = typeof error.message === "string" ? error.message : JSON.stringify(error);
    throw new Error(message);
  }}
  return decoded.result;
}}

async function ensureInitialized(signal?: AbortSignal): Promise<void> {{
  if (initialized) return;
  await mcpRequest("initialize", {{
    protocolVersion: "2024-11-05",
    capabilities: {{}},
    clientInfo: {{ name: "ralph-pi-extension", version: "0" }},
  }}, signal);
  await mcpRequest("notifications/initialized", {{}}, signal, false);
  initialized = true;
}}

async function listTools(signal?: AbortSignal): Promise<McpTool[]> {{
  await ensureInitialized(signal);
  const result = await mcpRequest("tools/list", {{}}, signal);
  if (!isObject(result) || !Array.isArray(result.tools)) return [];
  return result.tools.filter((tool): tool is McpTool => {{
    return isObject(tool) && typeof tool.name === "string" && tool.name.length > 0;
  }});
}}

function piToolResult(result: unknown): JsonObject {{
  if (!isObject(result)) {{
    return {{
      content: [{{ type: "text", text: JSON.stringify(result) }}],
      details: {{ result }},
    }};
  }}

  if (result.isError === true) {{
    const message = textFromContent(result.content) || JSON.stringify(result);
    throw new Error(message);
  }}

  const content = Array.isArray(result.content)
    ? result.content
    : [{{ type: "text", text: JSON.stringify(result) }}];
  return {{
    content,
    details: result,
  }};
}}

export default async function (pi: ExtensionAPI) {{
  const tools = await listTools();
  for (const tool of tools) {{
    pi.registerTool({{
      name: tool.name,
      label: tool.name,
      description: tool.description ?? `Ralph MCP tool ${{tool.name}}`,
      parameters: isObject(tool.inputSchema) ? tool.inputSchema : FALLBACK_SCHEMA,
      async execute(_toolCallId, params, signal) {{
        await ensureInitialized(signal);
        const result = await mcpRequest("tools/call", {{
          name: tool.name,
          arguments: isObject(params) ? params : {{}},
        }}, signal);
        return piToolResult(result);
      }},
    }});
  }}
}}
"""


__all__ = [
    "PI_MCP_EXTENSION_ENV",
    "pi_mcp_extension_path",
    "write_pi_mcp_extension",
]
