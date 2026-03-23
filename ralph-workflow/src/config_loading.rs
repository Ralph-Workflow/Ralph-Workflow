//! Configuration loading composition functions.
//!
//! This module provides composition functions that wire pure domain
//! functions to effectful capabilities.

/// Load a configuration value from the environment with a default.
pub fn load_config_with_default<T>(
    get_env_var: impl Fn(&str) -> Option<String>,
    key: &str,
    default: T,
) -> T
where
    T: std::str::FromStr,
    <T as std::str::FromStr>::Err: std::fmt::Debug,
{
    get_env_var(key)
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}
