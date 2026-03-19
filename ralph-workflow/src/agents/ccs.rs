//! CCS (Claude Code Switch) Alias Resolution
//!
//! This module provides support for resolving CCS aliases to agent configurations.
//! CCS is a universal AI profile manager that supports multiple Claude accounts,
//! Gemini, Copilot, `OpenRouter`, and other providers.
//!
//! # Direct Claude Execution for CCS GLM Only
//!
//! **IMPORTANT**: This module bypasses the `ccs` wrapper command only for `ccs/glm`.
//!
//! ## Why?
//!
//! The `ccs` wrapper command does not pass through all Claude CLI flags properly
//! (especially streaming-related flags like `--include-partial-messages`). For GLM,
//! Ralph also needs to inject Anthropic-compatible env vars from CCS settings.
//!
//! For other CCS profiles/providers (e.g. Gemini, Codex), CCS must initialize
//! provider-specific state itself, so Ralph runs `ccs ...` directly and does not
//! inject GLM/Anthropic env vars.
//!
//! ## How?
//!
//! For `ccs/glm`, instead of running `ccs glm --print --output-format=stream-json ...`, we run:
//! ```bash
//! ANTHROPIC_BASE_URL="..." \
//! ANTHROPIC_AUTH_TOKEN="..." \
//! ANTHROPIC_MODEL="..." \
//! claude --print --output-format=stream-json ...
//! ```
//!
//! The environment variables are loaded from CCS' settings files using the same
//! resolution rules CCS uses (via `~/.ccs/config.json` / `~/.ccs/config.yaml` and
//! common settings filenames like `~/.ccs/{profile}.settings.json`). This avoids
//! running the `ccs` wrapper while still using CCS-managed credentials.
//!
//! ## Fallback
//!
//! If the `claude` binary is not found in PATH (or env vars can't be loaded), the
//! original `ccs` command is used.
//!
//! # Usage
//!
//! Agents can be specified using `ccs/alias` syntax:
//! - `ccs/work` - Uses the "work" profile from CCS config
//! - `ccs/personal` - Uses the "personal" profile
//! - `ccs/gemini` - Uses CCS with Gemini provider
//! - `ccs` - Uses the default CCS profile
//!
//! # Configuration
//!
//! CCS aliases are defined in `~/.config/ralph-workflow.toml`:
//!
//! ```toml
//! [ccs]
//! # Defaults applied to all CCS aliases unless overridden per-alias.
//! # If your CCS version doesn't support these Claude CLI flags, set them to "".
//! output_flag = "--output-format=stream-json"
//! verbose_flag = "--verbose"
//! # YOLO (autonomous) mode: enabled by default (skip permission/confirmation prompts).
//! # Set to "" to disable and require confirmations.
//! yolo_flag = "--dangerously-skip-permissions"
//! json_parser = "claude"
//!
//! [ccs_aliases]
//! work = "ccs work" # shorthand
//! personal = { cmd = "ccs personal" } # explicit table form
//! gemini = { cmd = "ccs gemini", output_flag = "", verbose_flag = "", json_parser = "generic" }
//! ```

use super::ccs_env::{
    find_ccs_profile_suggestions, find_claude_binary, load_ccs_env_vars, CcsEnvVarsError,
};
use super::config::AgentConfig;
use super::parser::JsonParserType;
use crate::common::split_command;
use crate::config::{CcsAliasConfig, CcsConfig};
use std::collections::HashMap;
use std::path::Path;

// Sub-modules included via include!() macro
include!("ccs/parsing.rs");
include!("ccs/configuration.rs");

#[cfg(test)]
mod io_tests {
    include!("ccs/io_tests.rs");
}
