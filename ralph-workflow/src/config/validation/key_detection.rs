//! TOML structure traversal for unknown and deprecated key detection.
//!
//! This module walks the parsed TOML structure to detect keys that don't
//! match the expected configuration schema.

use super::keys::{
    DEPRECATED_GENERAL_KEYS, VALID_AGENT_CHAIN_KEYS, VALID_AGENT_CONFIG_KEYS,
    VALID_AGENT_DRAIN_KEYS, VALID_CCS_ALIAS_CONFIG_KEYS, VALID_CCS_KEYS, VALID_GENERAL_KEYS,
};

/// Type alias for a list of (`key_name`, location) pairs.
/// Used for tracking unknown and deprecated keys found during validation.
pub type KeyLocationList = Vec<(String, String)>;

/// Detect unknown keys and deprecated keys in a parsed TOML value.
///
/// Returns a tuple of:
/// - `KeyLocationList` for unknown keys
/// - `KeyLocationList` for deprecated keys
///
/// The location helps identify which section the key is in (e.g., "general.", "agents.claude.").
pub fn detect_unknown_and_deprecated_keys(
    value: &toml::Value,
) -> (KeyLocationList, KeyLocationList) {
    let mut unknown = Vec::new();
    let mut deprecated = Vec::new();

    // Get the top-level table
    if let Some(table) = value.as_table() {
        for (key, value) in table {
            match key.as_str() {
                // Valid top-level sections
                "general" | "ccs" | "agents" | "ccs_aliases" | "agent_chain" | "agent_chains"
                | "agent_drains" => {
                    // Recursively check subsections
                    let (section_unknown, section_deprecated) =
                        check_section(key.as_str(), value, &format!("{key}."));
                    unknown.extend(section_unknown);
                    deprecated.extend(section_deprecated);
                }
                // Unknown top-level section
                _ => {
                    unknown.push((key.clone(), String::new()));
                }
            }
        }
    }

    (unknown, deprecated)
}

/// Check a section for unknown and deprecated keys.
///
/// Returns a tuple of:
/// - `KeyLocationList` for unknown keys
/// - `KeyLocationList` for deprecated keys
///
/// The location includes the section prefix.
fn check_section(
    section: &str,
    value: &toml::Value,
    prefix: &str,
) -> (KeyLocationList, KeyLocationList) {
    let mut unknown = Vec::new();
    let mut deprecated = Vec::new();

    match section {
        "general" => {
            if let Some(table) = value.as_table() {
                for key in table.keys() {
                    let key_str = key.as_str();
                    if DEPRECATED_GENERAL_KEYS.contains(&key_str) {
                        deprecated.push((key.clone(), prefix.to_string()));
                    } else if !VALID_GENERAL_KEYS.contains(&key_str) {
                        unknown.push((key.clone(), prefix.to_string()));
                    }
                }
            }
        }
        "ccs" => {
            if let Some(table) = value.as_table() {
                for key in table.keys() {
                    if !VALID_CCS_KEYS.contains(&key.as_str()) {
                        unknown.push((key.clone(), prefix.to_string()));
                    }
                }
            }
        }
        "agents" => {
            // agents is a map of agent names to configs
            // We don't validate agent names (they're user-defined)
            // But we can validate the keys within each agent config
            if let Some(table) = value.as_table() {
                for (agent_name, agent_value) in table {
                    if let Some(agent_table) = agent_value.as_table() {
                        for key in agent_table.keys() {
                            if !VALID_AGENT_CONFIG_KEYS.contains(&key.as_str()) {
                                unknown.push((key.clone(), format!("{prefix}{agent_name}.")));
                            }
                        }
                    }
                }
            }
        }
        "ccs_aliases" => {
            // ccs_aliases is a map of alias names to configs
            // We don't validate alias names (they're user-defined)
            if let Some(table) = value.as_table() {
                for (alias_name, alias_value) in table {
                    if let Some(alias_table) = alias_value.as_table() {
                        for key in alias_table.keys() {
                            if !VALID_CCS_ALIAS_CONFIG_KEYS.contains(&key.as_str()) {
                                unknown.push((key.clone(), format!("{prefix}{alias_name}.")));
                            }
                        }
                    }
                }
            }
        }
        "agent_chain" => {
            // agent_chain has developer and reviewer keys
            if let Some(table) = value.as_table() {
                for key in table.keys() {
                    if !VALID_AGENT_CHAIN_KEYS.contains(&key.as_str()) {
                        unknown.push((key.clone(), prefix.to_string()));
                    }
                }
            }
        }
        "agent_drains" => {
            if let Some(table) = value.as_table() {
                for key in table.keys() {
                    if !VALID_AGENT_DRAIN_KEYS.contains(&key.as_str()) {
                        unknown.push((key.clone(), prefix.to_string()));
                    }
                }
            }
        }
        _ => {
            // Unknown section - should have been caught at top level
        }
    }

    (unknown, deprecated)
}
