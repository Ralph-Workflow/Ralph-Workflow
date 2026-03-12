# RFC-001: Enhanced OpenCode Integration for Ralph Workflow Orchestrator

**RFC Number**: RFC-001
**Title**: Enhanced OpenCode Integration for Ralph Workflow Orchestrator
**Status**: Draft
**Author**: Analysis based on codebase review
**Created**: 2026-01-14

> NOTE: RFCs are historical documents. This RFC may not reflect current behavior.
> For up-to-date architecture docs, see `../architecture/event-loop-and-reducers.md` and `../architecture/effect-system.md`.

---

## Abstract

This RFC proposes a comprehensive enhancement to OpenCode support in Ralph, elevating it from "Good" compatibility to "Excellent" parity with Claude Code and Codex. The proposal addresses gaps in parser sophistication, error handling, provider management, and reviewer role performance.

---

## Motivation

OpenCode is a significant agent in the Ralph ecosystem because:

1. **Multi-Provider Gateway**: Supports 75+ providers through a single interface (Anthropic, OpenAI, Google, Groq, DeepSeek, xAI, local models, etc.)
2. **Cost Flexibility**: Enables free-tier access (GLM), cost-optimized tiers (Z.AI Coding Plan), and BYOK options
3. **Unified Experience**: Single authentication and billing through OpenCode Zen

However, current OpenCode support has notable gaps compared to Claude Code:

| Feature | Claude | OpenCode |
|---------|--------|----------|
| Reviewer compatibility | Excellent | Good |
| Event types handled | 10+ | 4 |
| GLM quirk handling | Yes | No |
| Delta rendering | Sophisticated | Basic |
| Universal prompt | Automatic | None |
| Error classification | Rich | Limited |

---

## Current State Analysis

### Parser Limitations (`opencode.rs`)

1. **Limited Event Types**: Only handles `step_start`, `step_finish`, `tool_use`, `text`
2. **No GLM-Specific Handling**: Unlike Claude parser's `is_glm_agent()` for snapshot-as-delta detection
3. **Basic Streaming**: Uses simple text accumulation vs. Claude's `DeltaDisplayFormatter`/`DeltaRenderer`
4. **No Thinking/Reasoning Display**: Claude shows thinking deltas; OpenCode doesn't

### Provider Ecosystem Gaps

1. **75+ providers defined** but no runtime provider detection from output
2. **No provider-specific error patterns** for rate limits, quota exhaustion
3. **No automatic model fallback** within a single session

### Reviewer Role Weaknesses

1. **No universal prompt activation** for OpenCode-routed GLM/Qwen/DeepSeek
2. **No pre-flight compatibility warnings** like CCS/GLM agents receive
3. **Missing post-flight validation hooks** specific to OpenCode

---

## Proposed Changes

### Phase 1: Parser Enhancements

#### 1.1 Extended Event Support

Add support for additional OpenCode event types:

```
- thinking_start / thinking_delta / thinking_stop
- error
- rate_limit
- context_exhausted
- tool_result
- file_edit (with diff display)
- search_result
```

#### 1.2 Provider-Aware Parsing

Detect the underlying provider from OpenCode's output metadata:

```rust
pub fn detect_provider(&self, event: &OpenCodeEvent) -> Option<OpenCodeProviderType> {
    // Extract from sessionID pattern, model field, or metadata
}
```

This enables:
- Provider-specific quirk handling
- Appropriate truncation limits per provider
- Cost estimation per provider

#### 1.3 GLM/Qwen/DeepSeek Quirk Handling

Port Claude parser's GLM handling to OpenCode:

```rust
fn is_quirky_provider(&self) -> bool {
    matches!(self.detected_provider,
        Some(OpenCodeProviderType::ZaiGlm) |
        Some(OpenCodeProviderType::ZenGlm) |
        Some(OpenCodeProviderType::DeepSeek) |
        Some(OpenCodeProviderType::Qwen)
    )
}
```

#### 1.4 Sophisticated Delta Rendering

Integrate with existing `DeltaDisplayFormatter` infrastructure:

```rust
// Current: Basic text accumulation
let preview = truncate_text(accumulated_text, limit);

// Proposed: Use shared delta renderer
let renderer = TextDeltaRenderer::new(c, prefix, self.verbosity);
renderer.render_delta(text, &mut session)?;
```

### Phase 2: Error Classification & Recovery

#### 2.1 OpenCode-Specific Error Patterns

Define error patterns for common OpenCode failures:

```rust
pub enum OpenCodeErrorKind {
    ProviderRateLimit { provider: String, retry_after: Option<Duration> },
    QuotaExhausted { provider: String },
    AuthenticationExpired { provider: String },
    ModelUnavailable { model: String },
    ContextLengthExceeded { max: usize, requested: usize },
    ProviderTimeout { provider: String },
}
```

#### 2.2 Automatic Provider Fallback

When a provider fails, automatically try the next in the provider chain:

```toml
# Configuration
[provider_fallback]
opencode = [
    "-m opencode/glm-4.7-free",      # Try free tier first
    "-m zai/glm-4.7",                # Then Z.AI direct
    "-m anthropic/claude-sonnet-4",  # Then direct API
]
```

#### 2.3 Graceful Degradation

When OpenCode-specific features fail, fall back gracefully:

```rust
// If JSON parsing fails, try line-by-line
// If provider detection fails, use generic handling
// If streaming fails, buffer and display on completion
```

### Phase 3: Reviewer Role Parity

#### 3.1 Universal Prompt for OpenCode-Routed Models

Extend `should_use_universal_prompt()` to detect OpenCode providers:

```rust
fn should_use_universal_prompt(agent_name: &str) -> bool {
    let name = agent_name.to_lowercase();

    // Existing checks
    if name.contains("glm") || name.contains("qwen") || name.contains("deepseek") {
        return true;
    }

    // NEW: OpenCode provider aliases
    if name.starts_with("opencode-") {
        let provider = name.strip_prefix("opencode-").unwrap_or("");
        return matches!(provider,
            "zai-glm" | "zen-glm" | "deepseek" | "qwen" | "groq" |
            "ollama" | "lmstudio" | "llamacpp"  // Local models
        );
    }

    false
}
```

#### 3.2 Pre-Flight Compatibility Warnings

Add warnings before review with OpenCode agents:

```
⚠ Note: Agent 'opencode-zen-glm' uses GLM via OpenCode.
ℹ GLM models may have compatibility issues with structured review prompts.
ℹ Universal review prompt will be used automatically.
```

#### 3.3 Post-Flight Validation

Add OpenCode-specific validation:

```rust
fn validate_opencode_review_output(issues_path: &Path) -> ReviewValidation {
    // Check for common GLM output patterns that indicate failure
    // Detect truncated/incomplete reviews
    // Identify provider-specific formatting issues
}
```

### Phase 4: Observability & Debugging

#### 4.1 Provider Metrics

Track per-provider statistics:

```rust
struct ProviderMetrics {
    requests: u64,
    successes: u64,
    failures: u64,
    rate_limits: u64,
    avg_latency_ms: f64,
    total_cost: f64,
    tokens_used: TokenUsage,
}
```

#### 4.2 Debug Mode Enhancements

Add `RALPH_OPENCODE_DEBUG=1` for detailed diagnostics:

```
[OpenCode DEBUG] Provider detected: zai/glm-4.7
[OpenCode DEBUG] Session: ses_abc123, Snapshot: 5d36aa03
[OpenCode DEBUG] Event: tool_use (read), Status: completed, Latency: 234ms
[OpenCode DEBUG] Cost this session: $0.0012, Tokens: in=1234 out=567
```

#### 4.3 Health Monitor Integration

Extend `HealthMonitor` for OpenCode-specific patterns:

```rust
impl HealthMonitor {
    fn check_opencode_health(&self) -> Option<HealthWarning> {
        // Detect: high ignore rate, repeated rate limits,
        // provider switching, token exhaustion trends
    }
}
```

### Phase 5: Configuration Simplification

#### 5.1 Smart Provider Selection

Auto-configure based on available authentication:

```rust
fn suggest_opencode_providers() -> Vec<String> {
    let mut providers = vec![];

    if has_opencode_zen_auth() {
        providers.push("opencode/glm-4.7-free");
        providers.push("opencode/claude-sonnet-4");
    }
    if has_zai_auth() {
        providers.push("zai/glm-4.7");
    }
    if std::env::var("ANTHROPIC_API_KEY").is_ok() {
        providers.push("anthropic/claude-sonnet-4");
    }
    // ... etc

    providers
}
```

#### 5.2 Provider Presets

Add preset configurations:

```bash
ralph --preset opencode-free      # Uses free tiers only
ralph --preset opencode-premium   # Uses best models
ralph --preset opencode-local     # Uses local models only
ralph --preset opencode-hybrid    # Free -> Premium fallback
```

#### 5.3 Single-Command Setup

```bash
ralph opencode setup  # Interactive provider configuration
ralph opencode test   # Verify all configured providers work
ralph opencode status # Show auth status per provider
```

---

## Implementation Priority

| Phase | Effort | Impact | Priority |
|-------|--------|--------|----------|
| 1.3 GLM Quirk Handling | Low | High | P0 |
| 3.1 Universal Prompt | Low | High | P0 |
| 2.1 Error Patterns | Medium | High | P1 |
| 1.1 Extended Events | Medium | Medium | P1 |
| 2.2 Provider Fallback | Medium | High | P1 |
| 4.2 Debug Mode | Low | Medium | P2 |
| 1.4 Delta Rendering | High | Medium | P2 |
| 5.2 Provider Presets | Low | Medium | P2 |
| 1.2 Provider Detection | Medium | Medium | P3 |
| 4.1 Provider Metrics | High | Low | P3 |

---

## Success Criteria

1. **Reviewer Compatibility**: OpenCode achieves "Excellent" rating for reviewer role
2. **Error Recovery**: 90%+ of transient failures recovered via automatic fallback
3. **Feature Parity**: All Claude parser features available for OpenCode
4. **User Experience**: Zero-config setup for common OpenCode use cases
5. **Observability**: Clear diagnostics when OpenCode agents fail

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| OpenCode NDJSON format changes | Version detection, graceful fallback to generic |
| Provider-specific quirks multiply | Centralized quirk registry, community contributions |
| Performance overhead from detection | Lazy/cached provider detection |
| Configuration complexity increases | Presets, smart defaults, `ralph opencode setup` wizard |

---

## Alternatives Considered

1. **Recommend Claude/Codex for review**: Already documented, but doesn't leverage OpenCode's cost/provider benefits
2. **Generic parser for OpenCode**: Loses streaming and tool tracking capabilities
3. **Wait for OpenCode upstream fixes**: External dependency, uncertain timeline

---

## Implementation Status

### Phase 2: Error Classification & Recovery [PARTIALLY IMPLEMENTED]

#### 2.1 OpenCode-Specific Error Patterns [IMPLEMENTED - 2026-02-12]

**Status**: ✅ Completed

**Implementation Details**:

OpenCode usage limit detection has been comprehensively implemented with the following coverage:

1. **Structured Error Codes** (JSON):
   - `usage_limit_exceeded` - Generic usage limit (OpenCode gateway)
   - `quota_exceeded` - Provider quota exhaustion
   - `usage_limit_reached` - Alternative usage limit code
   - `insufficient_quota` - OpenAI quota exhaustion (verified in OpenCode source)
   - `rate_limit_exceeded` - Standard rate limit error

2. **Message-Based Patterns** (stderr/stdout):
   - "usage limit has been reached [retryin]" - OpenCode full phrase with timeout suffix
   - "usage limit reached" - Shorter variant
   - "usage limit exceeded" - Alternative wording
   - "OpenCode Zen usage limit" - OpenCode Zen branded errors
   - "opencode usage limit" - OpenCode gateway errors
   - Provider-forwarded: "<provider>: usage limit" (e.g., "anthropic: usage limit reached")
   - Bare "usage limit" with API error context (error prefix, punctuation, HTTP status)
   - "Quota exceeded" - OpenCode reformatted OpenAI errors
   - "insufficient_quota" - Direct OpenAI error messages

3. **Dual-Source Detection**:
   - Errors extracted from both stderr (traditional) and stdout (OpenCode JSON logs)
   - Critical for OpenCode which emits errors as JSON to stdout via session.error events

4. **False Positive Prevention**:
   - Filename exclusion: Patterns like "usage_limit.rs file not found" are correctly excluded
   - Context requirements: Bare "usage limit" requires API error context markers
   - File extension detection: Generic regex pattern covers all common programming languages

**Verified Sources**:
- OpenCode repository: https://github.com/anomalyco/opencode
- `/packages/opencode/src/cli/cmd/run.ts` - Error emission via session.error events
- `/packages/opencode/src/session/message-v2.ts` - Error format definitions (MessageV2.APIError, etc.)
- `/packages/opencode/src/provider/error.ts` - Error code parsing (insufficient_quota handling)

**Testing**:
- Comprehensive unit tests in `ralph-workflow/src/reducer/fault_tolerant_executor/tests/rate_limit_patterns.rs`
- Covers 40+ OpenCode error patterns including structured codes, message variants, provider-specific formats
- Negative tests prevent false positives from filenames, comments, non-error contexts

**Observability**:
- Debug logging: Extracted stdout errors from JSON logfiles
- Debug logging: Structured error codes detected
- Debug logging: Provider-specific error formats
- Info logging: Rate limit detection with error source (stdout vs stderr) and message preview

**Files Modified**:
- `ralph-workflow/src/reducer/fault_tolerant_executor/error_classification.rs`
- `ralph-workflow/src/pipeline/prompt/streaming.rs`
- `ralph-workflow/src/reducer/fault_tolerant_executor/mod.rs`
- `ralph-workflow/src/reducer/fault_tolerant_executor/tests/rate_limit_patterns.rs`

**Last Verified**: 2026-02-12

#### 2.2 Automatic Provider Fallback [NOT IMPLEMENTED]

**Status**: ❌ Not Started

This feature requires additional design work for multi-provider fallback chains.

#### 2.3 Graceful Degradation [PARTIALLY IMPLEMENTED]

**Status**: 🔶 Partially Complete

The codebase already includes graceful degradation for JSON parsing failures and streaming errors,
but provider-specific degradation strategies are not yet implemented.

---

## References

- Current opencode parser: `ralph-workflow/src/json_parser/opencode.rs`
- Claude parser (reference): `ralph-workflow/src/json_parser/claude.rs`
- Agent compatibility guide: `docs/agent-compatibility.md`
- Provider configuration: `ralph-workflow/examples/agents.toml`

---

## Open Questions

1. Should OpenCode get its own compatibility document like `docs/opencode-providers.md`?
2. Should provider fallback be automatic or require explicit configuration?
3. Should Ralph track OpenCode session history for cost reporting?
4. Is there appetite for an `opencode` subcommand in Ralph CLI?

---

*End of RFC*
