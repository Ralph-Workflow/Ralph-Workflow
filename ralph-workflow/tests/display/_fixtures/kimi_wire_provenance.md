# Kimi 2.3.0 stream-json wire-format provenance

This file is the git-tracked record of the measured Kimi 2.3.0
`kimi --output-format=stream-json` NDJSON wire format and the focused
regression fixture committed under `tests/display/_fixtures/kimi_wire.jsonl`.
The fixture frames were captured directly from the live `kimi 2.3.0` binary.

## Probed binary

- `kimi --version` -> `2.3.0`
- Binary present at `/home/mistlight/.kimi-code/bin/kimi` (symlinked or installed in user environment)
- Configuration: `/home/mistlight/.kimi-code/config.toml` (default model `kimi-code/kimi-for-coding`)
- Live capture command:

  ```bash
  /home/mistlight/.kimi-code/bin/kimi --output-format=stream-json -p \
      "Create a file named greeting.txt with the content 'Hello Kimi'. Then read greeting.txt."
  ```

- Exit code: `0`
- Stdout: 7 NDJSON frames
- Stderr: empty

## Observed frame vocabulary

The live binary emits these frames in sequence:

| Line | `role` | `type` / key | Event Kind (Parsed) | Notes |
| --- | --- | --- | --- | --- |
| 1 | `meta` | `system.version` | `lifecycle` | Carries `"version": "2.3.0"`; surfaces as `"kimi 2.3.0"` |
| 2 | `assistant` | `tool_calls` | `tool_use` | Calls native tool `"Write"` with JSON string arguments `{"path":"greeting.txt","content":"Hello Kimi\n"}` |
| 3 | `tool` | `tool_call_id` | `tool_result` | Result from `"Write"`: `"Wrote 11 bytes to greeting.txt"` |
| 4 | `assistant` | `tool_calls` | `tool_use` | Calls native tool `"Read"` with JSON string arguments `{"path":"greeting.txt"}` |
| 5 | `tool` | `tool_call_id` | `tool_result` | Result from `"Read"`: `"1\tHello Kimi"` |
| 6 | `assistant` | `content` | `text` | Final assistant response string summarizing action |
| 7 | `meta` | `session.resume_hint` | `lifecycle` | Session resume hint with session ID and resume command |

## Event kind summary

- `lifecycle`: 2 frames (`system.version`, `session.resume_hint`)
- `tool_use`: 2 frames (`Write`, `Read`)
- `tool_result`: 2 frames (`Write` result, `Read` result)
- `text`: 1 frame (assistant explanation)
- Total frames: 7

## Envelope shapes

### Meta frame (`system.version`)

```json
{"role":"meta","type":"system.version","version":"2.3.0"}
```

### Assistant tool call frame

```json
{"role":"assistant","tool_calls":[{"type":"function","id":"tool_b2ieWREjc5uJEN5I7oTTGxIM","function":{"name":"Write","arguments":"{\"path\":\"greeting.txt\",\"content\":\"Hello Kimi\\n\"}"}}]}
```

Function arguments are serialized as a JSON string inside `tool_calls[].function.arguments`. `KimiParser` decodes this string to extract `path` / `command` and produce canonical tool summaries.

### Tool result frame

```json
{"role":"tool","tool_call_id":"tool_b2ieWREjc5uJEN5I7oTTGxIM","content":"Wrote 11 bytes to greeting.txt"}
```

Correlates to the preceding tool call via `tool_call_id`.

### Assistant text frame

```json
{"role":"assistant","content":"Created `greeting.txt` with the content `Hello Kimi`, then read it back — the file contains exactly that one line."}
```

### Meta frame (`session.resume_hint`)

```json
{"role":"meta","type":"session.resume_hint","session_id":"session_eb6d7ec7-8341-4357-b73b-099e24b8102f","command":"kimi -r session_eb6d7ec7-8341-4357-b73b-099e24b8102f","content":"To resume this session: kimi -r session_eb6d7ec7-8341-4357-b73b-099e24b8102f"}
```
