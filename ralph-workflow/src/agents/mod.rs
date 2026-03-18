//! Agent Abstraction Module
//!
//! Provides a pluggable agent system for different AI coding assistants
//! (Claude, Codex, `OpenCode`, Goose, Cline, CCS, etc.)
//!
//! # Key Types
//!
//! - [`AgentRegistry`] - Registry for looking up and managing agent configurations
//! - [`AgentConfig`] - Configuration for a single agent (command, flags, parser type)
//! - [`AgentErrorKind`] - Error classification for fault-tolerant execution
//! - [`JsonParserType`] - Parser selection for agent NDJSON output (Claude, Codex, Gemini, `OpenCode`, Generic)
//!
//! For detailed compatibility information, see
//! [`docs/agent-compatibility.md`](https://codeberg.org/mistlight/RalphWithReviewer/src/branch/main/docs/agent-compatibility.md).
//!
//! ## Module Structure
//!
//! - `ccs` - CCS (Claude Code Switch) alias resolution
//! - `config` - Agent configuration types and TOML parsing
//! - `error` - Error classification for fault-tolerant execution
//! - `fallback` - Fallback chain configuration for agent switching
//! - `parser` - JSON parser type definitions
//! - `providers` - `OpenCode` provider types and authentication
//! - `registry` - Agent registry for agent lookup and management
//!
//! ## Configuration
//!
//! Agents can be configured via (in order of increasing priority):
//! 1. Built-in defaults (claude, codex, opencode, ccs, aider, goose, cline, continue, amazon-q, gemini)
//! 2. Unified config file (`~/.config/ralph-workflow.toml`)
//! 3. Environment variables (`RALPH_DEVELOPER_CMD`, `RALPH_REVIEWER_CMD`)
//! 4. Programmatic registration via `AgentRegistry::register()`
//!
//! ## CCS (Claude Code Switch) Support
//!
//! CCS aliases can be defined in the unified config and used with `ccs/alias` syntax:
//! ```toml
//! [ccs_aliases]
//! work = "ccs work"
//! personal = "ccs personal"
//! gemini = "ccs gemini"
//!
//! [agent_chains]
//! developer = ["ccs/work", "claude"]
//!
//! [agent_drains]
//! planning = "developer"
//! development = "developer"
//! ```
//!
//! ## Agent Switching / Fallback
//!
//! Configure fallback agents for automatic switching when primary agent fails:
//! ```toml
//! [agent_chains]
//! developer = ["claude", "codex", "goose"]
//! reviewer = ["codex", "claude"]
//!
//! [agent_drains]
//! planning = "developer"
//! development = "developer"
//! review = "reviewer"
//! fix = "reviewer"
//! commit = "reviewer"
//! analysis = "developer"
//!
//! [agent_chain]
//! max_retries = 3
//! retry_delay_ms = 1000
//! ```
//!
//! ## Example TOML Configuration
//!
//! ```toml
//! [agents.myagent]
//! cmd = "my-ai-tool run"
//! output_flag = "--json-stream"
//! yolo_flag = "--auto-fix"
//! verbose_flag = "--verbose"
//! can_commit = true
//! json_parser = "claude"  # Use Claude's JSON parser
//! ```

pub mod cache_environment;
#[cfg(any(test, feature = "test-utils"))]
pub mod ccs;
#[cfg(not(any(test, feature = "test-utils")))]
mod ccs;
mod ccs_env;
pub mod ccs_environment;
pub mod ccs_filesystem;
mod config;
mod error;
pub mod fallback;
pub mod network;
pub mod opencode_api;
mod opencode_resolver;
pub mod parser;
mod providers;
mod registry;
mod retry_timer;
pub mod runtime;
pub mod validation;

// Re-export I/O implementations for backwards compatibility
pub use cache_environment::CacheEnvironment;
pub use cache_environment::RealCacheEnvironment;
pub use ccs_env::{CcsEnvironment, CcsFilesystem};
pub use ccs_environment::RealCcsEnvironment;
pub use ccs_filesystem::RealCcsFilesystem;
pub use network::{fetch_api_catalog_json, get_env_var};
pub use runtime::{production_timer, ProductionRetryTimer, RetryTimerProvider};

// Re-export public types for crate-level access
pub use ccs::is_ccs_ref;
pub use config::{
    AgentConfig, AgentConfigBuilder, AgentsConfigFile, ConfigInitResult, ConfigSource,
};
pub use error::{contains_glm_model, is_glm_like_agent, AgentErrorKind};
pub use fallback::{AgentDrain, AgentRole, DrainMode};
pub use parser::JsonParserType;
pub use providers::{
    auth_failure_advice, strip_model_flag_prefix, validate_model_flag, OpenCodeProviderType,
};
pub use registry::AgentRegistry;

#[cfg(test)]
mod tests {
    use super::fallback::FallbackConfig;
    use super::*;

    #[test]
    fn test_module_exports() {
        // Verify all expected types are accessible through the module
        let _ = AgentRegistry::new().unwrap();
        let _ = FallbackConfig::default();
        let _ = AgentErrorKind::Permanent;
        let _ = AgentRole::Developer;
        let _ = JsonParserType::Claude;
        let _ = OpenCodeProviderType::OpenCodeZen;
    }
}
