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
    // Get the top-level table
    let table = match value.as_table() {
        Some(t) => t,
        None => return (KeyLocationList::new(), KeyLocationList::new()),
    };

    // Separate valid sections from unknown ones - build unknown and deprecated separately
    let base_unknown = table
        .iter()
        .filter(|(key, _value)| {
            !matches!(
                key.as_str(),
                "general"
                    | "ccs"
                    | "agents"
                    | "ccs_aliases"
                    | "agent_chain"
                    | "agent_chains"
                    | "agent_drains"
            )
        })
        .map(|(key, _)| (key.clone(), String::new()));

    let (section_unknown, section_deprecated): (KeyLocationList, KeyLocationList) = table
        .iter()
        .filter(|(key, _)| matches!(key.as_str(), "general"))
        .map(|(key, value)| {
            let prefix = format!("{key}.");
            check_section(key.as_str(), value, &prefix)
        })
        .fold(
            (KeyLocationList::new(), KeyLocationList::new()),
            |(mut unks, mut deps), (unk, dep)| {
                unks.extend(unk);
                deps.extend(dep);
                (unks, deps)
            },
        );

    let unknown = base_unknown.chain(section_unknown).collect();
    let deprecated = section_deprecated;

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
    let table = match value.as_table() {
        Some(t) => t,
        None => return (KeyLocationList::new(), KeyLocationList::new()),
    };

    match section {
        "general" => {
            let (deprecated_keys, unknown_keys): (Vec<String>, Vec<String>) = table
                .keys()
                .cloned()
                .partition(|key| DEPRECATED_GENERAL_KEYS.contains(&key.as_str()));

            let deprecated: KeyLocationList = deprecated_keys
                .into_iter()
                .map(|key| (key, prefix.to_string()))
                .collect();

            let unknown: KeyLocationList = unknown_keys
                .into_iter()
                .filter(|key| !VALID_GENERAL_KEYS.contains(&key.as_str()))
                .map(|key| (key, prefix.to_string()))
                .collect();

            (unknown, deprecated)
        }
        "ccs" => {
            let unknown: KeyLocationList = table
                .keys()
                .filter(|key| !VALID_CCS_KEYS.contains(&key.as_str()))
                .map(|key| (key.clone(), prefix.to_string()))
                .collect();
            (unknown, KeyLocationList::new())
        }
        "agents" => {
            // agents is a map of agent names to configs
            // We don't validate agent names (they're user-defined)
            // But we can validate the keys within each agent config
            let unknown: KeyLocationList = table
                .iter()
                .filter_map(|(agent_name, agent_value)| {
                    agent_value.as_table().map(|agent_table| {
                        agent_table
                            .keys()
                            .filter(|key| !VALID_AGENT_CONFIG_KEYS.contains(&key.as_str()))
                            .map(|key| (key.clone(), format!("{prefix}{agent_name}.")))
                            .collect::<KeyLocationList>()
                    })
                })
                .flatten()
                .collect();
            (unknown, KeyLocationList::new())
        }
        "ccs_aliases" => {
            // ccs_aliases is a map of alias names to configs
            // We don't validate alias names (they're user-defined)
            let unknown: KeyLocationList = table
                .iter()
                .filter_map(|(alias_name, alias_value)| {
                    alias_value.as_table().map(|alias_table| {
                        alias_table
                            .keys()
                            .filter(|key| !VALID_CCS_ALIAS_CONFIG_KEYS.contains(&key.as_str()))
                            .map(|key| (key.clone(), format!("{prefix}{alias_name}.")))
                            .collect::<KeyLocationList>()
                    })
                })
                .flatten()
                .collect();
            (unknown, KeyLocationList::new())
        }
        "agent_chain" => {
            // agent_chain has developer and reviewer keys
            let unknown: KeyLocationList = table
                .keys()
                .filter(|key| !VALID_AGENT_CHAIN_KEYS.contains(&key.as_str()))
                .map(|key| (key.clone(), prefix.to_string()))
                .collect();
            (unknown, KeyLocationList::new())
        }
        "agent_drains" => {
            let unknown: KeyLocationList = table
                .keys()
                .filter(|key| !VALID_AGENT_DRAIN_KEYS.contains(&key.as_str()))
                .map(|key| (key.clone(), prefix.to_string()))
                .collect();
            (unknown, KeyLocationList::new())
        }
        _ => (KeyLocationList::new(), KeyLocationList::new()),
    }
}
