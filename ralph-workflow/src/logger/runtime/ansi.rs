//! ANSI code handling for logger - uses interior mutability for caching.
//
//! This module satisfies the dylint boundary-module check for code that uses
//! LazyLock for compile-once initialization.

/// Compiled regex for stripping ANSI escape sequences.
///
/// Uses LazyLock for thread-safe, compile-once initialization.
pub static ANSI_RE: std::sync::LazyLock<Result<regex::Regex, regex::Error>> =
    std::sync::LazyLock::new(|| regex::Regex::new(r"\x1b\[[0-9;]*m"));
