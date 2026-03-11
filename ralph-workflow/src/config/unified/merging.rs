//! Configuration merging logic.
//!
//! This module provides the `merge_with` and `merge_with_content` methods on
//! [`UnifiedConfig`] for combining global and local configurations.

use super::fallback_merge::{built_in_fallback_defaults, merge_fallback_configs};
use super::types::UnifiedConfig;

impl UnifiedConfig {
    /// Merge local config into self (global), returning merged config.
    ///
    /// Local values override global values with these semantics:
    /// - Scalar values: local replaces global when explicitly present in TOML
    /// - Maps (agents, `ccs_aliases)`: local entries merge with global (local wins on collision)
    /// - Agent chain: merged per-key (local chain entries override global, missing chains fall through)
    /// - Optional values: local Some(_) replaces global, local None preserves global
    /// - CCS string values: empty string ("") means disabled, missing means use global
    ///
    /// This is a pure function - no I/O, cannot fail.
    ///
    /// IMPORTANT: This uses default-comparison heuristic and is primarily for tests.
    /// For real TOML-based configs, use `merge_with_content` for proper presence tracking.
    ///
    /// # Arguments
    ///
    /// * `local` - The local configuration to merge into this global configuration
    ///
    /// # Returns
    ///
    /// A new `UnifiedConfig` with local values merged into global values.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use ralph_workflow::config::unified::UnifiedConfig;
    ///
    /// let global = UnifiedConfig::default();
    /// let mut local = UnifiedConfig::default();
    /// local.general.verbosity = 4;
    ///
    /// let merged = global.merge_with(&local);
    /// assert_eq!(merged.general.verbosity, 4);
    /// ```
    #[must_use]
    pub fn merge_with(&self, local: &Self) -> Self {
        use super::types::{
            CcsConfig, GeneralBehaviorFlags, GeneralConfig, GeneralExecutionFlags,
            GeneralWorkflowFlags,
        };

        // Merge CCS config - empty string means use global
        fn merge_ccs_string(local: &str, global: &str) -> String {
            if local.is_empty() {
                global.to_string()
            } else {
                local.to_string()
            }
        }

        // For programmatically-constructed configs, we use default comparison
        // NOTE: This has known issues with booleans and default-valued fields (Issue #2)
        // but is kept for backward compatibility with tests
        let defaults = GeneralConfig::default();

        // Merge general config - override if local differs from default
        let general = GeneralConfig {
            verbosity: if local.general.verbosity == defaults.verbosity {
                self.general.verbosity
            } else {
                local.general.verbosity
            },
            behavior: GeneralBehaviorFlags {
                interactive: if local.general.behavior.interactive == defaults.behavior.interactive
                {
                    self.general.behavior.interactive
                } else {
                    local.general.behavior.interactive
                },
                auto_detect_stack: if local.general.behavior.auto_detect_stack
                    == defaults.behavior.auto_detect_stack
                {
                    self.general.behavior.auto_detect_stack
                } else {
                    local.general.behavior.auto_detect_stack
                },
                strict_validation: if local.general.behavior.strict_validation
                    == defaults.behavior.strict_validation
                {
                    self.general.behavior.strict_validation
                } else {
                    local.general.behavior.strict_validation
                },
            },
            workflow: GeneralWorkflowFlags {
                checkpoint_enabled: if local.general.workflow.checkpoint_enabled
                    == defaults.workflow.checkpoint_enabled
                {
                    self.general.workflow.checkpoint_enabled
                } else {
                    local.general.workflow.checkpoint_enabled
                },
            },
            execution: GeneralExecutionFlags {
                force_universal_prompt: if local.general.execution.force_universal_prompt
                    == defaults.execution.force_universal_prompt
                {
                    self.general.execution.force_universal_prompt
                } else {
                    local.general.execution.force_universal_prompt
                },
                isolation_mode: if local.general.execution.isolation_mode
                    == defaults.execution.isolation_mode
                {
                    self.general.execution.isolation_mode
                } else {
                    local.general.execution.isolation_mode
                },
            },
            developer_iters: if local.general.developer_iters == defaults.developer_iters {
                self.general.developer_iters
            } else {
                local.general.developer_iters
            },
            reviewer_reviews: if local.general.reviewer_reviews == defaults.reviewer_reviews {
                self.general.reviewer_reviews
            } else {
                local.general.reviewer_reviews
            },
            developer_context: if local.general.developer_context == defaults.developer_context {
                self.general.developer_context
            } else {
                local.general.developer_context
            },
            reviewer_context: if local.general.reviewer_context == defaults.reviewer_context {
                self.general.reviewer_context
            } else {
                local.general.reviewer_context
            },
            review_depth: if local.general.review_depth == defaults.review_depth {
                self.general.review_depth.clone()
            } else {
                local.general.review_depth.clone()
            },
            prompt_path: local
                .general
                .prompt_path
                .clone()
                .or_else(|| self.general.prompt_path.clone()),
            templates_dir: local
                .general
                .templates_dir
                .clone()
                .or_else(|| self.general.templates_dir.clone()),
            git_user_name: local
                .general
                .git_user_name
                .clone()
                .or_else(|| self.general.git_user_name.clone()),
            git_user_email: local
                .general
                .git_user_email
                .clone()
                .or_else(|| self.general.git_user_email.clone()),
            max_dev_continuations: if local.general.max_dev_continuations
                == defaults.max_dev_continuations
            {
                self.general.max_dev_continuations
            } else {
                local.general.max_dev_continuations
            },
            max_xsd_retries: if local.general.max_xsd_retries == defaults.max_xsd_retries {
                self.general.max_xsd_retries
            } else {
                local.general.max_xsd_retries
            },
            max_same_agent_retries: if local.general.max_same_agent_retries
                == defaults.max_same_agent_retries
            {
                self.general.max_same_agent_retries
            } else {
                local.general.max_same_agent_retries
            },
            execution_history_limit: if local.general.execution_history_limit
                == defaults.execution_history_limit
            {
                self.general.execution_history_limit
            } else {
                local.general.execution_history_limit
            },
        };

        let ccs = CcsConfig {
            output_flag: merge_ccs_string(&local.ccs.output_flag, &self.ccs.output_flag),
            yolo_flag: merge_ccs_string(&local.ccs.yolo_flag, &self.ccs.yolo_flag),
            verbose_flag: merge_ccs_string(&local.ccs.verbose_flag, &self.ccs.verbose_flag),
            print_flag: merge_ccs_string(&local.ccs.print_flag, &self.ccs.print_flag),
            streaming_flag: merge_ccs_string(&local.ccs.streaming_flag, &self.ccs.streaming_flag),
            json_parser: merge_ccs_string(&local.ccs.json_parser, &self.ccs.json_parser),
            session_flag: merge_ccs_string(&local.ccs.session_flag, &self.ccs.session_flag),
            can_commit: if local.ccs.can_commit == CcsConfig::default().can_commit {
                self.ccs.can_commit
            } else {
                local.ccs.can_commit
            },
        };

        // Merge agents map (local entries override global entries)
        let mut agents = self.agents.clone();
        for (key, value) in &local.agents {
            agents.insert(key.clone(), value.clone());
        }

        // Merge CCS aliases map (local entries override global entries)
        let mut ccs_aliases = self.ccs_aliases.clone();
        for (key, value) in &local.ccs_aliases {
            ccs_aliases.insert(key.clone(), value.clone());
        }

        let mut agent_chains = self.agent_chains.clone();
        for (key, value) in &local.agent_chains {
            agent_chains.insert(key.clone(), value.clone());
        }

        let mut agent_drains = self.agent_drains.clone();
        for (key, value) in &local.agent_drains {
            agent_drains.insert(key.clone(), value.clone());
        }

        // Agent chain: merge per-key (local non-empty chains override global)
        let agent_chain = merge_fallback_configs(
            self.agent_chain.as_ref(),
            local.agent_chain.as_ref(),
            |_field| true, // For programmatic merge, all fields are "present"
            false,
        );

        Self {
            general,
            ccs,
            agents,
            ccs_aliases,
            agent_chains,
            agent_drains,
            agent_chain,
        }
    }

    /// Merge local config content (TOML string) into self (global).
    ///
    /// This version tracks which fields are actually present in the TOML source
    /// to distinguish "not set" from "set to default value".
    ///
    /// # Arguments
    ///
    /// * `local_content` - The raw TOML content of the local config
    /// * `local_parsed` - The parsed local config (already deserialized)
    ///
    /// # Returns
    ///
    /// A new `UnifiedConfig` with local values merged into global values, using
    /// presence-based tracking to avoid false overrides of default values.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use ralph_workflow::config::unified::UnifiedConfig;
    ///
    /// let global = UnifiedConfig::default();
    /// let local_toml = r#"
    ///     [general]
    ///     verbosity = 4
    /// "#;
    /// let local = UnifiedConfig::load_from_content(local_toml).unwrap();
    ///
    /// let merged = global.merge_with_content(local_toml, &local);
    /// assert_eq!(merged.general.verbosity, 4);
    /// ```
    #[must_use]
    pub fn merge_with_content(&self, local_content: &str, local_parsed: &Self) -> Self {
        use super::types::{
            CcsConfig, GeneralBehaviorFlags, GeneralConfig, GeneralExecutionFlags,
            GeneralWorkflowFlags,
        };

        // Parse raw TOML to check field presence
        let local_toml: toml::Value = toml::from_str(local_content)
            .unwrap_or_else(|_| toml::Value::Table(toml::map::Map::default()));

        // Helper to check if a field is present in the TOML
        let general_table = local_toml.get("general");
        let behavior_table = general_table.and_then(|g| g.get("behavior"));

        // NOTE: workflow and execution fields are flattened into [general], not separate tables.
        // So we check for them at the [general] level, not [general.workflow] or [general.execution].
        let has_field = |key: &str| -> bool { general_table.and_then(|g| g.get(key)).is_some() };
        let has_behavior_field =
            |key: &str| -> bool { behavior_table.and_then(|b| b.get(key)).is_some() };

        // Merge general config with presence-based override detection
        // Only override if field was explicitly present in local TOML
        let general = GeneralConfig {
            verbosity: if has_field("verbosity") {
                local_parsed.general.verbosity
            } else {
                self.general.verbosity
            },
            behavior: GeneralBehaviorFlags {
                interactive: if has_behavior_field("interactive") {
                    local_parsed.general.behavior.interactive
                } else {
                    self.general.behavior.interactive
                },
                auto_detect_stack: if has_behavior_field("auto_detect_stack") {
                    local_parsed.general.behavior.auto_detect_stack
                } else {
                    self.general.behavior.auto_detect_stack
                },
                strict_validation: if has_behavior_field("strict_validation") {
                    local_parsed.general.behavior.strict_validation
                } else {
                    self.general.behavior.strict_validation
                },
            },
            workflow: GeneralWorkflowFlags {
                checkpoint_enabled: if has_field("checkpoint_enabled") {
                    local_parsed.general.workflow.checkpoint_enabled
                } else {
                    self.general.workflow.checkpoint_enabled
                },
            },
            execution: GeneralExecutionFlags {
                force_universal_prompt: if has_field("force_universal_prompt") {
                    local_parsed.general.execution.force_universal_prompt
                } else {
                    self.general.execution.force_universal_prompt
                },
                isolation_mode: if has_field("isolation_mode") {
                    local_parsed.general.execution.isolation_mode
                } else {
                    self.general.execution.isolation_mode
                },
            },
            developer_iters: if has_field("developer_iters") {
                local_parsed.general.developer_iters
            } else {
                self.general.developer_iters
            },
            reviewer_reviews: if has_field("reviewer_reviews") {
                local_parsed.general.reviewer_reviews
            } else {
                self.general.reviewer_reviews
            },
            developer_context: if has_field("developer_context") {
                local_parsed.general.developer_context
            } else {
                self.general.developer_context
            },
            reviewer_context: if has_field("reviewer_context") {
                local_parsed.general.reviewer_context
            } else {
                self.general.reviewer_context
            },
            review_depth: if has_field("review_depth") {
                local_parsed.general.review_depth.clone()
            } else {
                self.general.review_depth.clone()
            },
            prompt_path: local_parsed
                .general
                .prompt_path
                .clone()
                .or_else(|| self.general.prompt_path.clone()),
            templates_dir: local_parsed
                .general
                .templates_dir
                .clone()
                .or_else(|| self.general.templates_dir.clone()),
            git_user_name: local_parsed
                .general
                .git_user_name
                .clone()
                .or_else(|| self.general.git_user_name.clone()),
            git_user_email: local_parsed
                .general
                .git_user_email
                .clone()
                .or_else(|| self.general.git_user_email.clone()),
            max_dev_continuations: if has_field("max_dev_continuations") {
                local_parsed.general.max_dev_continuations
            } else {
                self.general.max_dev_continuations
            },
            max_xsd_retries: if has_field("max_xsd_retries") {
                local_parsed.general.max_xsd_retries
            } else {
                self.general.max_xsd_retries
            },
            max_same_agent_retries: if has_field("max_same_agent_retries") {
                local_parsed.general.max_same_agent_retries
            } else {
                self.general.max_same_agent_retries
            },
            execution_history_limit: if has_field("execution_history_limit") {
                local_parsed.general.execution_history_limit
            } else {
                self.general.execution_history_limit
            },
        };

        // Merge CCS config with presence-based semantics
        // Check if CCS fields are present in local TOML
        let ccs_table = local_toml.get("ccs");
        let has_ccs_field = |key: &str| -> bool { ccs_table.and_then(|c| c.get(key)).is_some() };

        let ccs = CcsConfig {
            output_flag: if has_ccs_field("output_flag") {
                local_parsed.ccs.output_flag.clone()
            } else {
                self.ccs.output_flag.clone()
            },
            yolo_flag: if has_ccs_field("yolo_flag") {
                local_parsed.ccs.yolo_flag.clone()
            } else {
                self.ccs.yolo_flag.clone()
            },
            verbose_flag: if has_ccs_field("verbose_flag") {
                local_parsed.ccs.verbose_flag.clone()
            } else {
                self.ccs.verbose_flag.clone()
            },
            print_flag: if has_ccs_field("print_flag") {
                local_parsed.ccs.print_flag.clone()
            } else {
                self.ccs.print_flag.clone()
            },
            streaming_flag: if has_ccs_field("streaming_flag") {
                local_parsed.ccs.streaming_flag.clone()
            } else {
                self.ccs.streaming_flag.clone()
            },
            json_parser: if has_ccs_field("json_parser") {
                local_parsed.ccs.json_parser.clone()
            } else {
                self.ccs.json_parser.clone()
            },
            session_flag: if has_ccs_field("session_flag") {
                local_parsed.ccs.session_flag.clone()
            } else {
                self.ccs.session_flag.clone()
            },
            can_commit: if has_ccs_field("can_commit") {
                local_parsed.ccs.can_commit
            } else {
                self.ccs.can_commit
            },
        };

        // Merge agents map (local entries override global entries)
        let mut agents = self.agents.clone();
        for (key, value) in &local_parsed.agents {
            agents.insert(key.clone(), value.clone());
        }

        // Merge CCS aliases map (local entries override global entries)
        let mut ccs_aliases = self.ccs_aliases.clone();
        for (key, value) in &local_parsed.ccs_aliases {
            ccs_aliases.insert(key.clone(), value.clone());
        }

        let mut agent_chains = self.agent_chains.clone();
        for (key, value) in &local_parsed.agent_chains {
            agent_chains.insert(key.clone(), value.clone());
        }

        let mut agent_drains = self.agent_drains.clone();
        for (key, value) in &local_parsed.agent_drains {
            agent_drains.insert(key.clone(), value.clone());
        }

        let chain_table = local_toml.get("agent_chain");
        // Agent chain: merge per-key (local chain entries override global/defaults).
        // Named chains/drains must not implicitly materialize the legacy schema.
        let agent_chain = if chain_table.is_some()
            || self.agent_chain.is_some()
            || local_parsed.agent_chain.is_some()
        {
            let built_in_chain = built_in_fallback_defaults();
            merge_fallback_configs(
                Some(self.agent_chain.as_ref().unwrap_or(&built_in_chain)),
                local_parsed.agent_chain.as_ref(),
                |field| chain_table.and_then(|c| c.get(field)).is_some(),
                true,
            )
        } else {
            None
        };

        Self {
            general,
            ccs,
            agents,
            ccs_aliases,
            agent_chains,
            agent_drains,
            agent_chain,
        }
    }
}
