//! Agent registry for agent lookup and management.
//!
//! The registry holds all configured agents and provides methods for
//! looking up agents by name, validating agent chains, and checking
//! agent availability.
//!
//! # CCS (Claude Code Switch) Support
//!
//! The registry supports CCS aliases using `ccs/alias` syntax.
//! CCS aliases are resolved on-the-fly to generate `AgentConfig` instances.
//!
//! ```ignore
//! // Using CCS aliases in agent chains
//! [ccs_aliases]
//! work = "ccs work"
//! personal = "ccs personal"
//!
//! [agent_chains]
//! developer = ["ccs/work", "claude"]
//!
//! [agent_drains]
//! planning = "developer"
//! development = "developer"
//! ```
//!
//! # `OpenCode` Dynamic Provider/Model Support
//!
//! The registry supports `OpenCode` dynamic provider/model using `opencode/provider/model` syntax.
//! Provider/model combinations are validated against the `OpenCode` API catalog.
//!
//! ```ignore
//! // Using OpenCode dynamic provider/model in agent chains
//! [agent_chains]
//! developer = ["opencode/anthropic/claude-sonnet-4-5", "claude"]
//! reviewer = ["opencode/openai/gpt-4", "codex"]
//!
//! [agent_drains]
//! planning = "developer"
//! development = "developer"
//! review = "reviewer"
//! fix = "reviewer"
//! ```
use super::ccs::CcsAliasResolver;
use super::config::{AgentConfig, AgentConfigError, AgentsConfigFile, DEFAULT_AGENTS_TOML};
use super::fallback::{AgentRole, FallbackConfig};
use super::opencode_resolver::OpenCodeResolver;
use super::parser::JsonParserType;
use super::retry_timer::{production_timer, RetryTimerProvider};
use crate::agents::opencode_api::ApiCatalog;
use crate::config::{CcsAliasConfig, CcsConfig};
use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

include!("registry/management.rs");
include!("registry/loading.rs");

#[cfg(test)]
mod tests {
    include!("registry/tests.rs");
}
