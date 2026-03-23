//! Environment variable parsing helpers.
//!
//! The unified config loader (`crate::config::loader`) owns the full
//! configuration-loading flow; this module keeps only shared helpers.

/// Parse a boolean from an environment variable value.
///
/// Accepts common truthy and falsy values:
/// - Truthy: "1", "true", "yes", "y", "on"
/// - Falsy: "0", "false", "no", "n", "off"
///
/// # Arguments
///
/// * `value` - The string value to parse
///
/// # Returns
///
/// Returns `Some(true)` for truthy values, `Some(false)` for falsy values,
/// and `None` for empty or unrecognized values.
#[must_use]
pub fn parse_env_bool(value: &str) -> Option<bool> {
    let normalized = value.trim().to_ascii_lowercase();
    match normalized.as_str() {
        "1" | "true" | "yes" | "y" | "on" => Some(true),
        "0" | "false" | "no" | "n" | "off" => Some(false),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_env_bool() {
        assert_eq!(parse_env_bool("1"), Some(true));
        assert_eq!(parse_env_bool("true"), Some(true));
        assert_eq!(parse_env_bool(" TRUE "), Some(true));
        assert_eq!(parse_env_bool("on"), Some(true));
        assert_eq!(parse_env_bool("yes"), Some(true));

        assert_eq!(parse_env_bool("0"), Some(false));
        assert_eq!(parse_env_bool("false"), Some(false));
        assert_eq!(parse_env_bool(" FALSE "), Some(false));
        assert_eq!(parse_env_bool("off"), Some(false));
        assert_eq!(parse_env_bool("no"), Some(false));

        assert_eq!(parse_env_bool(""), None);
        assert_eq!(parse_env_bool("maybe"), None);
    }
}

#[cfg(test)]
mod proptest_parsers {
    use super::parse_env_bool;
    use proptest::prelude::*;

    proptest! {
        /// `parse_env_bool` must never panic on any string input.
        #[test]
        fn parse_env_bool_never_panics(s in ".*") {
            let _ = parse_env_bool(&s);
        }

        /// Known truthy strings always return `Some(true)` regardless of surrounding whitespace.
        #[test]
        fn parse_env_bool_truthy_values(
            val in prop_oneof!["1", "true", "yes", "y", "on",
                               "TRUE", "YES", "ON", "True", "Yes"],
            prefix in "[ \t]*",
            suffix in "[ \t]*",
        ) {
            let padded = format!("{prefix}{val}{suffix}");
            prop_assert_eq!(parse_env_bool(&padded), Some(true));
        }

        /// Known falsy strings always return `Some(false)` regardless of surrounding whitespace.
        #[test]
        fn parse_env_bool_falsy_values(
            val in prop_oneof!["0", "false", "no", "n", "off",
                               "FALSE", "NO", "OFF", "False", "No"],
            prefix in "[ \t]*",
            suffix in "[ \t]*",
        ) {
            let padded = format!("{prefix}{val}{suffix}");
            prop_assert_eq!(parse_env_bool(&padded), Some(false));
        }

        /// Result is always `Some(true)`, `Some(false)`, or `None` — never anything else.
        #[test]
        fn parse_env_bool_result_is_valid(s in ".*") {
            let result = parse_env_bool(&s);
            prop_assert!(result == Some(true) || result == Some(false) || result.is_none());
        }
    }
}
