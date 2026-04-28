# Web Search

Ralph Workflow ships a built-in `web_search` tool with a pluggable multi-backend design.

## Backends

| Backend | Needs Key | Shape | Notes |
|---|---|---|---|
| `ddgs` | No | In-process | Default. No configuration required. |
| `searxng` | No | HTTP | Requires `url` field. Self-hosted OSS search. |
| `tavily` | Yes | HTTP | Requires `api_key_env` or `api_key`. |
| `brave` | Yes | HTTP | Requires `api_key_env` or `api_key`. |
| `exa` | Yes | HTTP | Requires `api_key_env` or `api_key`. |

### Backend configuration fields

| Field | Type | Description |
|---|---|---|
| `url` | `string \| null` | Required for `searxng`. Not used by other backends. |
| `api_key` | `string \| null` | **Prefer `api_key_env` instead.** Inline secrets may be committed accidentally. |
| `api_key_env` | `string \| null` | Name of an environment variable holding the API key. Recommended for all keyed backends. |

## Fallback chain

Web search is tried in dispatch order: `backend` first, then each entry in `fallback` in order.

```
primary → fallback[0] → fallback[1] → ... → is_error=True
```

- If the primary backend succeeds, results are returned.
- If it raises a `WebSearchError`, Ralph Workflow logs a warning and tries the next backend.
- Retrying the same backend is **not** done — a backend that returns an error (rate-limited, etc.) is skipped for the remainder of the session.
- If all backends fail, Ralph Workflow returns `is_error=True` with message `"all web_search backends failed"`.

```toml
[web_search]
enabled = true
backend = "ddgs"
fallback = ["searxng", "tavily", "brave", "exa"]
```

## Secrets

> **BIG WARNING**: `mcp.toml` files may be committed to version control. Prefer `api_key_env = "TAVILY_API_KEY"` over `api_key = "sk-..."`. Never commit inline secrets.

### How to keep secrets out of git

Store secrets in the **user-global** config layer instead of project-local:

```
~/.config/ralph-workflow-mcp.toml   ← your user-global config (NOT committed)
your-project/.agent/mcp.toml       ← project config (may be committed)
```

Put only the `api_key_env` reference in the committed file:

```toml
[web_search.backends.tavily]
api_key_env = "TAVILY_API_KEY"
```

Set the environment variable in your shell profile:

```bash
export TAVILY_API_KEY="YOUR_API_KEY_HERE"
```

Never put a real API key in any `mcp.toml` that could be committed.

## Disabling

Set `enabled = false` to omit the `web_search` tool entirely from `tools/list`:

```toml
[web_search]
enabled = false
```

When disabled, the tool is not registered and will not appear in agent tool listings.

## Install keyed-backend extras

Keyed backends (`tavily`, `brave`, `exa`) require extra dependencies:

```bash
pip install ralph-workflow[web-search]
```

This installs `tavily-python`, `brave-search-python-client`, and `exa-py` alongside Ralph Workflow.

## Privacy note

The **query string is NOT logged**. Ralph Workflow logs only the backend name and error type on failure:

```
warning: web_search backend tavily failed: <error type>; trying next
```

No query content appears in logs.

## Worked examples

### ddgs (default, no config needed)

```toml
[web_search]
enabled = true
backend = "ddgs"
```

### searxng

```toml
[web_search]
enabled = true
backend = "searxng"

[web_search.backends.searxng]
url = "https://search.example.com"
```

### tavily

```toml
[web_search]
enabled = true
backend = "tavily"

[web_search.backends.tavily]
api_key_env = "TAVILY_API_KEY"
```

### brave

```toml
[web_search]
enabled = true
backend = "brave"

[web_search.backends.brave]
api_key_env = "BRAVE_API_KEY"
```

### exa

```toml
[web_search]
enabled = true
backend = "exa"

[web_search.backends.exa]
api_key_env = "EXA_API_KEY"
```

### Full example with fallback chain

```toml
[web_search]
enabled = true
backend = "ddgs"
fallback = ["searxng", "tavily"]

[web_search.backends.searxng]
url = "https://search.example.com"

[web_search.backends.tavily]
api_key_env = "TAVILY_API_KEY"
```
