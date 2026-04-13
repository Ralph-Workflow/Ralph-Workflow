//! Fallback chain merge helpers.
//!
//! This module provides the internal helper functions for merging
//! [`FallbackConfig`](crate::agents::fallback::FallbackConfig) instances
//! during unified config loading and merging.

/// Merge two `FallbackConfig` instances with per-key granularity.
///
/// The `is_local_field_present` predicate determines whether a given field name
/// was explicitly set in the local config. For TOML-based merging, this checks
/// raw TOML key presence. For programmatic merges (no key-presence info), callers
/// pass a predicate that always returns true; in that path, empty local chains are
/// treated as "not set" and fall through to global values.
///
/// Chain lists (developer, reviewer, commit, analysis) use:
/// - For TOML: presence in raw TOML is authoritative (including explicit empty lists)
/// - For programmatic: non-empty local → use local, empty local → use global
///
/// Scalar metadata fields (`max_retries`, etc.) follow the same presence logic.
pub(super) fn hardcoded_fallback_defaults() -> crate::agents::fallback::FallbackConfig {
    crate::agents::fallback::FallbackConfig {
        developer: vec![
            "claude".to_string(),
            "codex".to_string(),
            "opencode".to_string(),
        ],
        reviewer: vec!["codex".to_string(), "claude".to_string()],
        commit: vec![
            "claude".to_string(),
            "codex".to_string(),
            "opencode".to_string(),
        ],
        ..Default::default()
    }
}

pub(super) fn built_in_fallback_defaults_with<E>(
    registry_loader: impl FnOnce() -> Result<crate::agents::AgentRegistry, E>,
) -> crate::agents::fallback::FallbackConfig
where
    E: std::fmt::Display,
{
    registry_loader().map_or_else(
        |_error| hardcoded_fallback_defaults(),
        |registry| registry.fallback_config(),
    )
}

pub(super) fn built_in_fallback_defaults() -> crate::agents::fallback::FallbackConfig {
    built_in_fallback_defaults_with(crate::agents::AgentRegistry::new)
}

pub(super) fn merge_fallback_configs(
    global: Option<&crate::agents::fallback::FallbackConfig>,
    local: Option<&crate::agents::fallback::FallbackConfig>,
    is_local_field_present: impl Fn(&str) -> bool,
    use_toml_presence_for_lists: bool,
) -> Option<crate::agents::fallback::FallbackConfig> {
    use crate::agents::fallback::FallbackConfig;

    match (global, local) {
        (Some(g), Some(l)) => {
            let merge_chain =
                |field: &str, local_chain: &[String], global_chain: &[String]| -> Vec<String> {
                    if use_toml_presence_for_lists {
                        if is_local_field_present(field) {
                            local_chain.to_vec()
                        } else {
                            global_chain.to_vec()
                        }
                    } else if is_local_field_present(field) && !local_chain.is_empty() {
                        local_chain.to_vec()
                    } else {
                        global_chain.to_vec()
                    }
                };

            // Merge provider_fallback maps (local entries override global) using iterator chain
            let provider_fallback: std::collections::HashMap<_, _> = g
                .provider_fallback
                .iter()
                .chain(l.provider_fallback.iter())
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect();

            Some(FallbackConfig {
                developer: merge_chain("developer", &l.developer, &g.developer),
                reviewer: merge_chain("reviewer", &l.reviewer, &g.reviewer),
                commit: merge_chain("commit", &l.commit, &g.commit),
                analysis: merge_chain("analysis", &l.analysis, &g.analysis),
                planning: merge_chain("planning", &l.planning, &g.planning),
                fix: merge_chain("fix", &l.fix, &g.fix),
                provider_fallback,
                max_retries: if is_local_field_present("max_retries") {
                    l.max_retries
                } else {
                    g.max_retries
                },
                retry_delay_ms: if is_local_field_present("retry_delay_ms") {
                    l.retry_delay_ms
                } else {
                    g.retry_delay_ms
                },
                backoff_multiplier: if is_local_field_present("backoff_multiplier") {
                    l.backoff_multiplier
                } else {
                    g.backoff_multiplier
                },
                max_backoff_ms: if is_local_field_present("max_backoff_ms") {
                    l.max_backoff_ms
                } else {
                    g.max_backoff_ms
                },
                max_cycles: if is_local_field_present("max_cycles") {
                    l.max_cycles
                } else {
                    g.max_cycles
                },
                legacy_role_keys_present: g.has_legacy_role_key_presence()
                    || l.has_legacy_role_key_presence(),
            })
        }
        (None, Some(l)) => {
            if use_toml_presence_for_lists {
                let defaults = built_in_fallback_defaults();
                Some(FallbackConfig {
                    developer: if is_local_field_present("developer") {
                        l.developer.clone()
                    } else {
                        defaults.developer
                    },
                    reviewer: if is_local_field_present("reviewer") {
                        l.reviewer.clone()
                    } else {
                        defaults.reviewer
                    },
                    commit: if is_local_field_present("commit") {
                        l.commit.clone()
                    } else {
                        defaults.commit
                    },
                    analysis: if is_local_field_present("analysis") {
                        l.analysis.clone()
                    } else {
                        defaults.analysis
                    },
                    planning: if is_local_field_present("planning") {
                        l.planning.clone()
                    } else {
                        defaults.planning
                    },
                    fix: if is_local_field_present("fix") {
                        l.fix.clone()
                    } else {
                        defaults.fix
                    },
                    provider_fallback: l.provider_fallback.clone(),
                    max_retries: if is_local_field_present("max_retries") {
                        l.max_retries
                    } else {
                        defaults.max_retries
                    },
                    retry_delay_ms: if is_local_field_present("retry_delay_ms") {
                        l.retry_delay_ms
                    } else {
                        defaults.retry_delay_ms
                    },
                    backoff_multiplier: if is_local_field_present("backoff_multiplier") {
                        l.backoff_multiplier
                    } else {
                        defaults.backoff_multiplier
                    },
                    max_backoff_ms: if is_local_field_present("max_backoff_ms") {
                        l.max_backoff_ms
                    } else {
                        defaults.max_backoff_ms
                    },
                    max_cycles: if is_local_field_present("max_cycles") {
                        l.max_cycles
                    } else {
                        defaults.max_cycles
                    },
                    legacy_role_keys_present: l.has_legacy_role_key_presence(),
                })
            } else {
                Some(l.clone())
            }
        }
        (Some(g), None) => Some(g.clone()),
        (None, None) => None,
    }
}

#[cfg(test)]
mod tests {
    use super::{
        built_in_fallback_defaults, built_in_fallback_defaults_with, hardcoded_fallback_defaults,
        merge_fallback_configs,
    };
    use crate::agents::fallback::FallbackConfig;

    #[test]
    fn test_merge_fallback_configs_local_only_uses_built_in_defaults_for_missing_toml_keys() {
        let local = FallbackConfig {
            developer: vec!["codex".to_string()],
            ..Default::default()
        };

        let merged = merge_fallback_configs(None, Some(&local), |field| field == "developer", true)
            .expect("merged fallback config should exist");

        let builtins = built_in_fallback_defaults();

        assert_eq!(merged.developer, vec!["codex"]);
        assert_eq!(
            merged.reviewer, builtins.reviewer,
            "missing local reviewer should inherit built-in defaults"
        );
        assert_eq!(
            merged.commit, builtins.commit,
            "missing local commit should inherit built-in defaults"
        );
        assert_eq!(
            merged.analysis, builtins.analysis,
            "missing local analysis should inherit built-in defaults"
        );
    }

    #[test]
    fn test_built_in_fallback_defaults_registry_failure_uses_hardcoded_non_empty_defaults() {
        let fallback = built_in_fallback_defaults_with(|| {
            Err(anyhow::anyhow!("simulated built-in registry load failure"))
        });
        let expected = hardcoded_fallback_defaults();

        assert_eq!(fallback.developer, expected.developer);
        assert_eq!(fallback.reviewer, expected.reviewer);
        assert_eq!(fallback.commit, expected.commit);
        assert!(
            !fallback.developer.is_empty(),
            "hardcoded developer fallback must never be empty"
        );
        assert!(
            !fallback.reviewer.is_empty(),
            "hardcoded reviewer fallback must never be empty"
        );
    }
}
