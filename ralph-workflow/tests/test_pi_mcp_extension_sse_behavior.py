"""Behavioral subprocess test for the generated Pi MCP extension SSE client.

This is marked subprocess_e2e because the only faithful way to execute the
TypeScript extension that Pi loads is to run a JS/TS runtime. The test uses a
fully mocked ``fetch`` and mocked Pi ``registerTool`` surface: no network, no
real Ralph server, no sleeps. It proves the regression class behind the live
300s ``terminated`` loop cannot return: an open SSE stream that does not EOF
must still resolve as soon as the matching JSON-RPC response frame arrives.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ralph.mcp.transport.pi import write_pi_mcp_extension

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def test_generated_pi_extension_resolves_matching_sse_frame_without_eof(tmp_path: Path) -> None:
    bun = shutil.which("bun")
    assert bun is not None, "bun is required to execute the generated TypeScript extension"

    extension_path, cleanup = write_pi_mcp_extension(
        "http://localhost:9999/mcp",
        workspace_path=tmp_path,
    )
    assert cleanup is None

    harness_path = tmp_path / "pi_extension_sse_harness.ts"
    harness_path.write_text(
        f"""
import extensionFactory from {json.dumps(extension_path.as_posix())};

let registeredTool: {{ execute: Function }} | undefined;
let cancelCount = 0;
let toolsCallCount = 0;

const encoder = new TextEncoder();

function jsonResponse(payload: unknown): Response {{
  return new Response(JSON.stringify(payload), {{
    status: 200,
    headers: {{ "content-type": "application/json", "mcp-session-id": "sess-1" }},
  }});
}}

function sseFrame(payload: unknown): Uint8Array {{
  return encoder.encode(`event: message\\ndata: ${{JSON.stringify(payload)}}\\n\\n`);
}}

function openSseResponse(): Response {{
  const stream = new ReadableStream<Uint8Array>({{
    start(controller) {{
      controller.enqueue(sseFrame({{
        jsonrpc: "2.0",
        method: "notifications/message",
        params: {{ text: "progress" }},
      }}));
      controller.enqueue(sseFrame({{
        jsonrpc: "2.0",
        id: 999,
        result: {{ content: [{{ type: "text", text: "wrong id" }}] }},
      }}));
      controller.enqueue(sseFrame({{
        jsonrpc: "2.0",
        id: 3,
        result: {{ content: [{{ type: "text", text: "ok" }}], isError: false }},
      }}));
      // Deliberately do NOT close. The generated bridge must cancel the reader
      // after the matching id=3 frame instead of waiting for EOF.
    }},
    cancel() {{
      cancelCount += 1;
    }},
  }});
  return new Response(stream, {{
    status: 200,
    headers: {{ "content-type": "text/event-stream" }},
  }});
}}

globalThis.fetch = (async (_url: string, init?: RequestInit): Promise<Response> => {{
  const body = JSON.parse(String(init?.body ?? "{{}}"));
  if (body.method === "initialize") {{
    return jsonResponse({{ jsonrpc: "2.0", id: body.id, result: {{}} }});
  }}
  if (body.method === "notifications/initialized") {{
    return new Response(null, {{ status: 202 }});
  }}
  if (body.method === "tools/list") {{
    return jsonResponse({{
      jsonrpc: "2.0",
      id: body.id,
      result: {{
        tools: [{{
          name: "exec",
          description: "Execute",
          inputSchema: {{ type: "object", additionalProperties: true }},
        }}],
      }},
    }});
  }}
  if (body.method === "tools/call") {{
    toolsCallCount += 1;
    if (body.id !== 3) throw new Error(`expected tools/call id 3, got ${{body.id}}`);
    return openSseResponse();
  }}
  throw new Error(`unexpected method ${{body.method}}`);
}}) as typeof fetch;

await extensionFactory({{
  registerTool(tool: {{ execute: Function }}) {{
    registeredTool = tool;
  }},
}} as any);

if (!registeredTool) throw new Error("extension did not register tool");
const result = await registeredTool.execute("call-1", {{ command: "printf ok" }}, undefined);
const text = result.content?.[0]?.text;
if (text !== "ok") throw new Error(`unexpected tool text: ${{text}}`);
if (toolsCallCount !== 1) throw new Error(`tools/call count: ${{toolsCallCount}}`);
if (cancelCount !== 1) throw new Error(`reader cancel count: ${{cancelCount}}`);
console.log("ok");
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [bun, "run", str(harness_path)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=3,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
