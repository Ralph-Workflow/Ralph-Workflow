//! Configuration loading composition functions.
//!
//! This module provides composition functions that wire pure domain
//! functions to effectful capabilities.

use crate::runtime::environment::Environment;

/// Load a configuration value from the environment with a default.
pub fn load_config_with_default<T>(env: &dyn Environment, key: &str, default: T) -> T
where
    T: std::str::FromStr,
    <T as std::str::FromStr>::Err: std::fmt::Debug,
{
    env.var(key).and_then(|v| v.parse().ok()).unwrap_or(default)
}
