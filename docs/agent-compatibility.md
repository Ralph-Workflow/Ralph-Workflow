# Agent Compatibility Guide

This guide documents compatibility between Ralph Workflow and various AI coding agents in its configurable review orchestration. Ralph's review phase is designed to be agent-agnostic in its prompts, but different agents may have varying levels of success due to differences in JSON output format, tool execution behavior, and other agent-specific quirks.

> **⚠️ Important Compatibility Note**: GLM, ZhipuAI, Qwen, and DeepSeek agents have known compatibility issues with review tasks. While Ralph automatically applies workarounds (universal prompt), success rates may vary. **For best results, consider using Claude Code or Codex as the reviewer.** You can override the reviewer agent with `--reviewer-agent claude` or `--reviewer-agent codex`.

> **Note**: Ralph now includes a **Universal Review Prompt** that automatically activates for agents with known compatibility issues (GLM, ZhipuAI, Qwen, DeepSeek). This simplified prompt improves success rates with these agents.

## Table of Contents

- [Known Working Agents](#known-working-agents)
- [Agents with Known Issues](#agents-with-known-issues)
- [Configuration Recommendations](#configuration-recommendations)
- [Universal Review Prompt](#universal-review-prompt)
- [Troubleshooting Guide](#troubleshooting-guide)

## Compatibility Matrix

| Agent | Developer Role | Reviewer Role | Notes |
|-------|---------------|---------------|-------|
| **Claude Code** | ✅ Excellent | ✅ Excellent | Best overall compatibility |
| **Codex (OpenAI)** | ✅ Excellent | ✅ Excellent | Great for security-focused reviews |
| **OpenCode** | ✅ Good | ✅ Good | Requires `opencode` parser |
| **CCS/GLM** | ✅ Good | ⚠️ Partial | Universal prompt auto-applied |
| **ZhipuAI/ZAI** | ✅ Good | ⚠️ Partial | Universal prompt auto-applied |
| **Qwen** | ✅ Good | ⚠️ Partial | Universal prompt auto-applied |
| **DeepSeek** | ✅ Good | ⚠️ Partial | Universal prompt auto-applied |
| **Aider** | ✅ Good | ⚠️ Limited | Use `generic` parser |
| **Gemini CLI** | ✅ Good | ⚠️ Experimental | Parser support less mature |

### Legend
- ✅ **Excellent** - Works perfectly, recommended
- ✅ **Good** - Works well with minor caveats
- ⚠️ **Partial** - Works with automatic workarounds, may have reduced capability
- ⚠️ **Limited** - Works but output may be less structured
- ⚠️ **Experimental** - Not thoroughly tested

## Known Working Agents

These agents have been tested and work well with Ralph's review process:

### Claude Code (Recommended)

**Status**: ✅ Fully Compatible

**Configuration**:
```toml
# In ~/.config/ralph-workflow.toml or .agent/ralph-workflow.toml
[agents.claude]
name = "claude"
command = "claude"
args = ["--json", "--full-auto", "--prompt", "<PROMPT>"]
json_parser = "claude"
```

**Recommended Settings**:
- `review_depth`: `standard` or `comprehensive`
- `reviewer_context`: `minimal` or `normal`
- `reviewer_reviews`: `2`

**Notes**:
- Best overall compatibility with Ralph
- Produces structured, well-formatted ISSUES.md
- Handles file writes and tool execution reliably
- Good at following complex review guidelines

### Codex (OpenAI)

**Status**: ✅ Fully Compatible

**Configuration**:
```toml
[agents.codex]
name = "codex"
command = "codex"
args = ["exec", "--json", "--full-auto", "<PROMPT>"]
json_parser = "codex"
```

**Recommended Settings**:
- `review_depth`: `standard` or `security`
- `reviewer_context`: `normal`
- `reviewer_reviews`: `2`

**Notes**:
- Excellent for security-focused reviews
- Good at identifying code quality issues
- May be more verbose than Claude Code

### OpenCode

**Status**: ✅ Compatible with Proper Configuration

**Configuration**:
```toml
[agents.opencode]
name = "opencode"
command = "opencode"
args = ["--json", "<PROMPT>"]
json_parser = "opencode"
```

**Recommended Settings**:
- `review_depth`: `standard`
- `reviewer_context`: `normal`
- `reviewer_reviews`: `1` or `2`

**Notes**:
- Requires the `opencode` parser (not interchangeable)
- May produce less structured output than Claude/Codex
- Works best with explicit review guidelines

## Agents with Known Issues

These agents have known compatibility issues with Ralph's review process:

### CCS/GLM

**Status**: ⚠️ Partial Compatibility - Automatic Workarounds Applied

**Known Issues**:
1. **Permission Errors**: GLM has known issues with file write permissions that can cause exit code 1 errors
2. **JSON Format Differences**: GLM may output JSON in a slightly different format than expected
3. **Tool Execution Failures**: Some tool calls may fail silently
4. **Prompt Complexity**: GLM may struggle with complex structured prompts

**Automatic Workarounds (Ralph v0.2.5+)**:
- **Universal Prompt**: Ralph automatically uses a simplified review prompt for GLM
- **Fast Fallback**: GLM exit code 1 errors now trigger immediate fallback (no indefinite retries)
- **Pre-flight Warning**: Ralph warns you before running review with GLM
- **Hardcoded print_flag fallback**: Ralph automatically adds `--print` flag for CCS agents even if missing from config

**Configuration Requirements**:
CCS agents require `print_flag = "--print"` in your `~/.config/ralph-workflow.toml`:

```toml
[ccs]
# REQUIRED: print_flag enables non-interactive mode for Claude CLI
print_flag = "--print"
output_flag = "--output-format=stream-json"
yolo_flag = "--dangerously-skip-permissions"
verbose_flag = "--verbose"
json_parser = "claude"
can_commit = true

[ccs_aliases]
glm = "ccs glm"
```

**Note**: As of Ralph v0.2.7+, when using **CCS alias resolution** (agents referenced as
`ccs/<alias>` or plain `ccs` via the unified config), Ralph will ensure there is a
non-empty print flag. If the unified-config CCS default `ccs.print_flag` is empty,
Ralph falls back to `--print` as a safety net.

This fallback is **not** a universal fix for arbitrary custom agents: if you define a
non-CCS agent (or a custom CCS-like agent) without a `print_flag`, Ralph will not
invent one for that agent. The built-in example `ralph-workflow/examples/agents.toml` already sets
`[agents.ccs].print_flag = "--print"`.

**Manual Workaround Configuration**:
```toml
[agents.ccs_glm]
name = "ccs/glm"
command = "ccs"
args = ["glm", "--output-format=stream-json", "--dangerously-skip-permissions", "<PROMPT>"]
json_parser = "claude"  # CCS always uses Claude parser (outputs stream-json format)
print_flag = "--print"  # Use Claude's --print (CCS reserves -p/--prompt for delegation)
```

> **IMPORTANT**: CCS (Claude Code Switcher) ALWAYS outputs Claude's stream-json format, regardless of which provider you're using (GLM, Gemini, etc.). The Claude parser is the correct parser for all CCS agents. If `ccs/glm` is not using the Claude parser, **this is a bug** and should be reported.

**Alternative - Use Different Reviewer**:
```bash
# Override the reviewer agent on the command line
ralph --reviewer-agent codex

# Or skip review entirely
RALPH_REVIEWER_REVIEWS=0 ralph
```

**Notes**:
- Universal prompt improves success rate but GLM may still fail
- Consider using GLM as developer only, not reviewer
- The `--dangerously-skip-permissions` flag is often required
- Exit code 1 errors with GLM are now classified as `AgentSpecificQuirk` (triggers fallback)

### GLM-Direct - Stream-JSON Workaround

**Status**: ✅ Usually Not Needed

**Issue with `ccs/glm`**:
Older Ralph versions could run `ccs glm -p ...` directly, which can cause CCS to intercept/reformat output (breaking stream-json parsing).
Additionally, CCS treats `-p` / `--prompt` as its own headless delegation mode, so `ccs <profile> -p ...` is not a safe way to pass Claude's print flag through CCS.

**Current behavior**:
Ralph can bypass the CCS wrapper only for the **GLM** alias (`ccs/glm`) **when env vars were successfully loaded** from CCS settings. In that case, Ralph invokes the `claude` binary directly (with the loaded env), which preserves Claude CLI flag passthrough and stream-json output.

For other CCS aliases (e.g., `ccs/gemini`, `ccs/codex`), Ralph does **not** bypass the wrapper and will run through `ccs`.

**Optional - `glm-direct` Agent**:
If you prefer an explicit non-CCS agent (or want `glm` without `ccs/`), you can still configure `glm-direct` to call `claude` directly with `ccs_profile = "glm"`.

**Configuration**:
```toml
[agents.glm-direct]
cmd = "claude"
output_flag = "--output-format=stream-json"
yolo_flag = "--dangerously-skip-permissions"
verbose_flag = "--verbose"
can_commit = true
json_parser = "claude"
ccs_profile = "glm"  # Auto-loads env vars from CCS
print_flag = "--print"
display_name = "GLM (Direct via Claude)"
```

**Usage**:
```bash
# Use glm-direct instead of ccs/glm
ralph --developer-agent glm-direct

# Add to a named chain for automatic fallback
# In ~/.config/ralph-workflow.toml:
[agent_chains]
developer = ["glm-direct", "claude", "codex"]
reviewer = ["claude"]

[agent_drains]
planning = "developer"
development = "developer"
analysis = "developer"
review = "reviewer"
fix = "reviewer"
```

**Requirements**:
- GLM profile must be configured in CCS (`ccs api create --preset glm`)
- CCS settings file at `~/.ccs/glm.settings.json` must exist
- Claude CLI must be installed

**Why This Works**:
1. Claude CLI with GLM env vars outputs proper stream-json format
2. No CCS delegation wrapper = no output formatting interference
3. Ralph gets raw `{"type":"stream_event","event":{"type":"text_delta","text":"Hello"}}` events
4. Real-time streaming works as expected

### ZhipuAI / ZAI

**Status**: ⚠️ Partial Compatibility - Automatic Workarounds Applied

**Known Issues**:
1. Similar to GLM (related model family)
2. May struggle with complex review guidelines
3. Exit code 1 errors common

**Automatic Workarounds**:
- Universal review prompt automatically applied
- Fast fallback on failures

**Workaround Configuration**:
```bash
# Use a different reviewer
ralph --reviewer-agent codex

# Or try generic parser
ralph --reviewer-json-parser generic
```

### Qwen / DeepSeek

**Status**: ⚠️ Experimental - Automatic Workarounds Applied

**Known Issues**:
1. These models may have weaker instruction-following capabilities
2. May not follow complex multi-section prompts reliably

**Automatic Workarounds**:
- Universal review prompt automatically applied
- Simplified output format with examples

**Recommendation**: Use for development, consider Claude/Codex for review

### Aider

**Status**: ⚠️ Limited Compatibility

**Known Issues**:
1. **Different Output Format**: Aider uses a generic text-based output format
2. **No Native JSON Streaming**: Requires generic parser
3. **Different Tool Semantics**: Tool handling differs from Claude/Codex

**Configuration**:
```toml
[agents.aider]
name = "aider"
command = "aider"
args = ["--yes", "<PROMPT>"]
json_parser = "generic"
```

**Notes**:
- Review output may be less structured
- May not populate all ISSUES.md fields correctly
- Consider using for development only

### Gemini CLI

**Status**: ⚠️ Experimental

**Configuration**:
```toml
[agents.gemini]
name = "gemini"
command = "gemini"
args = ["--json", "<PROMPT>"]
json_parser = "gemini"
```

**Notes**:
- Parser support is available but less mature
- May have issues with complex review guidelines
- Consider using `json_parser = "generic"` if issues arise

## Agent Chain and Fallback Behavior

Ralph uses an **agent chain** system for fault-tolerant execution. When an agent fails (due to XSD validation errors, authentication failures, or other issues), Ralph automatically falls back to the next agent in the chain.

### Agent Chain Configuration

Configure reusable named chains, then bind the built-in runtime drains in `~/.config/ralph-workflow.toml`:

```toml
[agent_chains]
developer = ["claude", "codex", "aider"]  # Primary -> Secondary -> Tertiary
reviewer = ["claude", "codex"]

[agent_drains]
planning = "developer"
development = "developer"
analysis = "developer"
review = "reviewer"
fix = "reviewer"
# Omit commit to inherit the resolved review/fix binding by default
```

### Fallback Behavior by Role

| Runtime Drain | Binding | Fallback If Omitted |
|--------------|---------|--------------------|
| **Planning / Development / Analysis** | `agent_drains.* -> agent_chains.<name>` | Analysis inherits the resolved planning/development chain |
| **Review / Fix** | `agent_drains.* -> agent_chains.<name>` | Fix should usually share the review chain unless you want a dedicated fix chain |
| **Commit** | `agent_drains.commit -> agent_chains.<name>` | Inherits the resolved review/fix binding |

### Commit Agent Fallback (Important)

When generating commit messages (`--generate-commit-msg`), Ralph uses this fallback order:

1. **First**: Check `agent_drains.commit` for a configured commit drain binding
2. **Second**: If omitted, inherit the resolved `review` / `fix` chain binding
3. **Third**: If both empty, use the context's reviewer agent

This design reflects that commit message generation is semantically similar to review tasks, so reviewer agents are natural fallbacks.

**Example**: With no commit agents configured:
```toml
[agent_chains]
developer = ["claude", "codex"]
reviewer = ["codex", "aider"]

[agent_drains]
planning = "developer"
development = "developer"
analysis = "developer"
review = "reviewer"
fix = "reviewer"
# commit omitted: inherits reviewer chain
```

Running `ralph --generate-commit-msg` will use: codex -> aider (from reviewer chain).

### XSD Retry Logic

When an agent produces invalid XML output, Ralph uses XSD retry logic:

1. **Within same session**: Re-prompts the same agent with the XSD error and last output
2. **After MAX_PLAN_INVALID_OUTPUT_RERUNS (2)**: Switches to next agent in chain
3. **After MAX_VALIDATION_RETRY_ATTEMPTS (10)**: Escalates to agent switch

The XSD retry prompt includes:
- The XSD schema for reference
- The previous invalid output
- The specific validation error

### Backoff Policy

Ralph applies exponential backoff between retry cycles:

```toml
[agent_chain]
retry_delay_ms = 1000       # Initial delay (1 second)
backoff_multiplier = 2.0    # Double each cycle
max_backoff_ms = 30000      # Cap at 30 seconds
max_cycles = 3              # Maximum retry cycles
```

## Configuration Recommendations

### Per-Agent Configuration

For agents that have known compatibility issues, Ralph provides several configuration options to improve success rates:

#### Option 1: Force Universal Prompt

Set the `RALPH_REVIEWER_UNIVERSAL_PROMPT` environment variable to force the simplified review prompt for any agent:

```bash
# Force universal prompt for all agents
RALPH_REVIEWER_UNIVERSAL_PROMPT=1 ralph

# Force universal prompt for a specific reviewer
RALPH_REVIEWER_UNIVERSAL_PROMPT=1 ralph --reviewer-agent ccs/glm
```

Or add to `~/.config/ralph-workflow.toml`:
```toml
[general]
force_universal_prompt = true
```

#### Option 2: Override JSON Parser

> **Note**: For CCS agents (ccs/glm, ccs/gemini, etc.), the Claude parser should always be used since CCS outputs Claude's stream-json format. If a CCS agent is not using the Claude parser, **this is a bug**.

The parser override option is primarily for non-CCS agents that have compatibility issues:

```bash
# Use generic parser with agents that don't output stream-json
ralph --reviewer-agent aider --reviewer-json-parser generic

# Or via environment variable
RALPH_REVIEWER_JSON_PARSER=generic ralph --reviewer-agent aider
```

#### Option 3: Use a Different Reviewer

The most reliable option is to use Claude Code or Codex as the reviewer while keeping GLM/CCS as the developer:

```bash
# Use GLM for development, Claude for review
ralph --developer-agent ccs/glm --reviewer-agent claude
```

#### Option 4: Skip Review Entirely

If the review process is not critical for your use case:

```bash
# Skip review phase
RALPH_REVIEWER_REVIEWS=0 ralph
```

### Context Level Recommendations

### JSON Parser Selection

The `json_parser` setting controls how Ralph interprets the agent's output:

| Parser | Best For | Notes |
|--------|----------|-------|
| `claude` | Claude Code | Native parser, most reliable |
| `codex` | OpenAI Codex | Native parser |
| `opencode` | OpenCode | Required for OpenCode |
| `gemini` | Gemini CLI | Native parser, experimental |
| `generic` | Any agent | Fallback for unsupported agents |

### Review Depth Settings

| Setting | Description | Recommended For |
|---------|-------------|-----------------|
| `standard` | Balanced review | Most agents and use cases |
| `comprehensive` | Thorough review with language-specific checks | Claude Code, Codex |
| `security` | Security-focused review | Codex, security audits |
| `incremental` | Review only changed files | Fast feedback cycles |

### Context Level Recommendations

| Setting | Description | When to Use |
|---------|-------------|-------------|
| `minimal` | Only changed files | Large codebases, slow agents |
| `normal` | Changed files + dependencies | Default setting |
| `full` | Entire codebase | Small projects, thorough reviews |

## Universal Review Prompt

**Available in**: Ralph v0.2.5+

The Universal Review Prompt is a simplified, agent-agnostic review prompt designed to work with AI models that have weaker instruction-following capabilities or known compatibility issues with complex structured prompts.

### When is it Used?

Ralph automatically uses the Universal Review Prompt when the reviewer agent is:
- `ccs/glm` or any agent containing "glm"
- ZhipuAI agents (containing "zhipuai" or "zai")
- Qwen agents
- DeepSeek agents

You'll see a log message when it's activated:
```
ℹ Using universal/simplified review prompt for agent 'ccs/glm' (better compatibility)
```

### How it Differs

| Feature | Standard Prompt | Universal Prompt |
|---------|----------------|------------------|
| Language | Technical terms (context contamination, isolation mode) | Simple direct language |
| Structure | Multi-section with numbered lists | Simple task description |
| Output Format | Implied from context | Explicit template with examples |
| Examples | None | Full example of ISSUES.md format |
| Severity Levels | Described in detail | Simple list with examples |

### Example Output Format

The Universal Prompt includes this explicit example:

```markdown
# Code Review Issues

## Critical Issues
- [ ] [src/main.rs:42] Null pointer dereference risk

## High Priority
- [ ] [src/auth.rs:15] Missing input validation

## Medium Priority
- [ ] [src/utils.rs:78] Function may return null

## Low Priority
- [ ] [src/config.rs:10] Missing documentation
```

And specifies: "If no issues found, write exactly: `No issues found.`"

### Customization

If you want to force the Universal Prompt for a different agent, you can:

1. **Environment variable**: Set `RALPH_REVIEWER_UNIVERSAL_PROMPT=1` to force universal prompt for all agents
2. **Config file**: Add `force_universal_prompt = true` to the `[general.execution]` section in `~/.config/ralph-workflow.toml` or `.agent/ralph-workflow.toml`
3. **Source code**: Modify the universal-review selection logic in the Python package under `ralph-workflow/ralph/`

Example:
```bash
# Force universal prompt for any agent
RALPH_REVIEWER_UNIVERSAL_PROMPT=1 ralph --reviewer-agent claude
```

## Why Do Some Agents Fail?

Understanding why certain AI agents struggle with the review process can help you choose the right agent for your needs.

### Technical Causes

1. **JSON Output Format Differences**
   - Different agents structure their JSON output differently
   - Ralph expects specific event formats (e.g., `text_delta`, `tool_use`)
   - The `generic` parser can handle many variations but may miss some events

2. **Tool Execution Behavior**
   - Review agents need to reliably produce the expected outputs (issues/fixes) in the configured format
   - The orchestrator may write workflow files on the agent’s behalf, but agents still need compatible tool/IO behavior
   - Some agents have permission issues or different tool semantics (notably some GLM/CCS setups)

3. **Prompt Complexity Handling**
   - AI models vary in their ability to follow complex, multi-section prompts
   - The Universal Review Prompt simplifies instructions for models with weaker instruction-following
   - Some models may ignore parts of complex prompts or misinterpret structured guidelines

4. **Context Window and Processing**
   - Reviewing code requires understanding large codebases
   - Models with smaller context windows may miss important details
   - The `reviewer_context` setting can help manage this

### How Ralph Handles These Issues

Ralph includes several automatic mitigations:

1. **Universal Review Prompt**: Automatically activates for GLM, ZhipuAI, Qwen, and DeepSeek
2. **Fast Fallback**: Known-problematic agents trigger quick fallback instead of retries
3. **Pre-flight Warnings**: Users are warned before running review with problematic agents
4. **Post-flight Validation**: ISSUES.md is validated after review to catch issues early
5. **Error Classification**: Exit codes and stderr are analyzed to determine recovery strategy

### Recommendation Matrix

| Use Case | Recommended Agent | Why |
|----------|------------------|-----|
| **Best Overall** | Claude Code (`claude`) | Excellent compatibility, reliable output |
| **Security Review** | Codex (`codex`) | Strong security analysis capabilities |
| **Cost-Effective** | CCS/GLM (`ccs/glm`) | Good for development, use different reviewer |
| **Testing Alternatives** | Any + `--reviewer-json-parser generic` | Generic parser works with most agents |

## Troubleshooting Guide

> **Note on Log Paths**: As of recent versions, Ralph uses per-run log directories at `.agent/logs-<run_id>/agents/`. 
> When checking agent logs, use `ls .agent/logs-*/agents/` to find the most recent run directory, then access logs like:
> `.agent/logs-2026-02-06_14-03-27.123Z/agents/reviewer_1.log`

### GLM/CCS Agent Fails with "When using --print, --output-format=stream-json requires --verbose"

**Symptoms**: Agent fails with error message about `--verbose` being required when using `--print` with `--output-format=stream-json`.

**Root Cause**: When the Claude CLI is invoked with `-p` (print flag) and `--output-format=stream-json`, it also requires `--verbose`. This was a bug in Ralph v0.2.7 and earlier where the `--verbose` flag was not automatically added when using a full path to the claude binary (e.g., `/usr/local/bin/claude`).

**Fixed in**: Ralph v0.2.8+

**Workarounds** (if using older version):

1. **Upgrade Ralph**: The fix has been applied - `requires_verbose_for_json` now correctly checks the file name portion of the path, not just the full path string.

2. **Use "claude" directly in PATH** (temporary workaround): Ensure `claude` is in PATH as just "claude" rather than using a full path.

**Verification**:
```bash
# Check that the command includes --verbose
ralph --reviewer-agent ccs/glm --verbosity debug 2>&1 | grep -i "verbose"

# Should see something like:
# Executing: /usr/local/bin/claude -p --output-format=stream-json --include-partial-messages --verbose --dangerously-skip-permissions <PROMPT>
```

### Review Agent Fails with Exit Code 1

**Symptoms**: Agent exits with code 1 repeatedly with "AgentSpecificQuirk" error message.

**Possible Causes**:
1. **Permission denied** (common with GLM/CCS)
2. **Tool execution failure**
3. **Agent-specific quirk**
4. **Prompt complexity** (agent can't follow complex instructions)

**Solutions**:

1. **Check if universal prompt is activated**:
   Look for this log message:
   ```
   ℹ Using universal/simplified review prompt for agent 'ccs/glm' (better compatibility)
   ```

2. **Check agent logs**:
   ```bash
   cat .agent/logs/reviewer_review_1_<agent>.log
   ```

3. **Try a different parser**:
   ```bash
   ralph --reviewer-json-parser generic
   ```

4. **Use a different reviewer agent**:
   ```bash
   ralph --reviewer-agent codex
   # or
   ralph --reviewer-agent claude
   ```

5. **Skip review entirely**:
   ```bash
   RALPH_REVIEWER_REVIEWS=0 ralph
   ```

6. **Enable debug logging**:
   ```bash
   ralph --verbosity debug
   ```

7. **Enable CCS-specific debug logging** (v0.2.8+):
   ```bash
   RALPH_CCS_DEBUG=1 ralph --reviewer-agent ccs/glm --verbosity debug
   ```
   This will show detailed information about:
   - Claude binary detection
   - Environment variable loading
   - Command construction and bypass logic

**Note**: As of Ralph v0.2.5, GLM and similar agents with exit code 1 errors now trigger immediate fallback to the next agent instead of retrying indefinitely.

### ISSUES.md Not Created After Review

**Symptoms**: Review completes but no ISSUES.md file is created.

**Possible Causes**:
1. **Agent failed silently**
2. **Parser ignored all events**
3. **Agent used different output format**

**Solutions**:

1. **Check pre-flight validation output** - Ralph now warns you before running review with problematic agents:
   ```
   ⚠ Note: Agent 'ccs/glm' may have compatibility issues with review tasks.
   ℹ If review fails, consider these workarounds:
   ```

2. **Check post-flight validation** - Look for:
   ```
   ⚠ Post-flight check: ISSUES.md not found after review.
   ```

3. **Review agent logs** for errors:
   ```bash
   cat .agent/logs/reviewer_review_1_<agent>.log
   ```

4. **Try with `--verbosity debug`** for more diagnostic information

5. **Switch to a known-compatible agent** like Claude or Codex

### Parser Ignores Many Events

**Symptoms**: Warning message "Parser ignored >50% of events".

**Possible Causes**:
1. **Wrong parser selected**
2. **Agent outputs unexpected JSON format**
3. **Agent-specific quirk**

**Solutions**:
1. **Check agent compatibility** in this guide
2. **Try generic parser**:
   ```toml
   json_parser = "generic"
   ```
3. **Check raw agent output**:
   ```bash
   cat .agent/logs/reviewer_review_1_<agent>.log
   ```

### Review Finds No Issues But Should

**Symptoms**: ISSUES.md created but empty or declares "no issues found" when issues exist.

**Possible Causes**:
1. **Context too limited** (`reviewer_context = "minimal"`)
2. **Review depth too shallow**
3. **Agent not following guidelines**

**Solutions**:
1. **Increase context**:
   ```toml
   reviewer_context = "normal"  # or "full"
   ```

2. **Use comprehensive review**:
   ```toml
   review_depth = "comprehensive"
   ```

3. **Try a different agent** known for thorough reviews

## Contributing

If you test Ralph with an agent not listed here, please contribute your findings:

1. **Test the agent** with both development and review roles
2. **Document any issues** encountered
3. **Share working configurations** (anonymized)
4. **Submit a PR** to update this guide

## Additional Resources

- **Main README**: [../README.md](../README.md)
- **Configuration Guide**: See `ralph --help` for CLI options
- **Issue Tracker**: Report compatibility issues on GitHub

---

Last updated: 2026-01-28
