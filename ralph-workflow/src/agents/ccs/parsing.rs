// CCS output parsing logic - parsing Claude Code responses, extracting structured data

/// CCS alias prefix for agent names.
pub const CCS_PREFIX: &str = "ccs/";

/// Parse a CCS agent reference and extract the alias name.
///
/// Returns `Some(alias)` if the agent name matches `ccs/alias` pattern,
/// or `Some("")` if it's just `ccs` (for default profile).
/// Returns `None` if the name doesn't match the CCS pattern.
///
/// # Examples
///
/// ```ignore
/// assert_eq!(parse_ccs_ref("ccs/work"), Some("work"));
/// assert_eq!(parse_ccs_ref("ccs"), Some(""));
/// assert_eq!(parse_ccs_ref("claude"), None);
/// ```
#[must_use]
pub fn parse_ccs_ref(agent_name: &str) -> Option<&str> {
    if agent_name == "ccs" {
        Some("")
    } else if let Some(alias) = agent_name.strip_prefix(CCS_PREFIX) {
        Some(alias)
    } else {
        None
    }
}

/// Check if an agent name is a CCS reference.
#[must_use]
pub fn is_ccs_ref(agent_name: &str) -> bool {
    parse_ccs_ref(agent_name).is_some()
}

/// Check if a command appears to be the CCS executable.
///
/// This is a heuristic check based on the file name of the command.
/// Returns `true` if the file name is `ccs` or `ccs.exe`.
fn looks_like_ccs_executable(cmd0: &str) -> bool {
    Path::new(cmd0)
        .file_name()
        .and_then(|n| n.to_str())
        .is_some_and(|n| n == "ccs" || n == "ccs.exe")
}

/// Extract the CCS profile name from a CCS command.
///
/// Parses a CCS command string to extract the profile name.
/// Supports common patterns like `ccs <profile>` and `ccs api <profile>`.
///
/// Returns `Some(profile_name)` if a profile is found, `None` otherwise.
pub(super) fn ccs_profile_from_command(original_cmd: &str) -> Option<String> {
    let parts = split_command(original_cmd).ok()?;
    if !parts.first().is_some_and(|p| looks_like_ccs_executable(p)) {
        return None;
    }
    // Common patterns:
    // - `ccs <profile>`
    // - `ccs api <profile>`
    if parts.get(1).is_some_and(|p| p == "api") {
        parts.get(2).cloned()
    } else {
        parts.get(1).cloned()
    }
}

fn choose_best_profile_guess<'a>(input: &str, suggestions: &'a [String]) -> Option<&'a str> {
    if suggestions.is_empty() {
        return None;
    }
    let input_lower = input.to_lowercase();
    if let Some(exact) = suggestions
        .iter()
        .find(|s| s.to_lowercase() == input_lower)
        .map(std::string::String::as_str)
    {
        return Some(exact);
    }
    if suggestions.len() == 1 {
        return Some(suggestions.first()?.as_str());
    }
    if let Some(starts) = suggestions
        .iter()
        .find(|s| s.to_lowercase().starts_with(&input_lower))
        .map(std::string::String::as_str)
    {
        return Some(starts);
    }
    Some(suggestions.first()?.as_str())
}

pub(super) fn load_ccs_env_vars_with_guess(
    profile: &str,
) -> Result<(HashMap<String, String>, Option<String>), CcsEnvVarsError> {
    match load_ccs_env_vars(profile) {
        Ok(vars) => Ok((vars, None)),
        Err(err @ CcsEnvVarsError::ProfileNotFound { .. }) => {
            let suggestions = find_ccs_profile_suggestions(profile);
            let Some(best) = choose_best_profile_guess(profile, &suggestions) else {
                return Err(err);
            };
            let vars = load_ccs_env_vars(best)?;
            Ok((vars, Some(best.to_string())))
        }
        Err(err) => Err(err),
    }
}

#[cfg(test)]
mod proptest_parsers {
    use super::{parse_ccs_ref, CCS_PREFIX};
    use proptest::prelude::*;

    proptest! {
        /// `parse_ccs_ref` must never panic on any string input.
        #[test]
        fn parse_ccs_ref_never_panics(s in ".*") {
            let _ = parse_ccs_ref(&s);
        }

        /// Exact `"ccs"` always returns `Some("")`.
        #[test]
        fn parse_ccs_ref_exact_ccs_returns_empty(s in Just("ccs".to_string())) {
            prop_assert_eq!(parse_ccs_ref(&s), Some(""));
        }

        /// A name starting with `"ccs/"` followed by a non-empty alias returns `Some(alias)`.
        #[test]
        fn parse_ccs_ref_ccs_slash_alias_returns_alias(alias in "[a-zA-Z][a-zA-Z0-9_-]{0,20}") {
            let name = format!("{CCS_PREFIX}{alias}");
            let result = parse_ccs_ref(&name);
            prop_assert_eq!(result, Some(alias.as_str()));
        }

        /// A name not starting with `"ccs"` always returns `None`.
        #[test]
        fn parse_ccs_ref_non_ccs_returns_none(s in "[^c].*|c[^c].*|cc[^s].*") {
            prop_assert_eq!(parse_ccs_ref(&s), None);
        }
    }
}
