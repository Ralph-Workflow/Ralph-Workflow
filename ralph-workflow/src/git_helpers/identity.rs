//! Git identity resolution with fallback chain.
//!
//! This module provides a comprehensive git identity resolution system that:
//! 1. Works with git config as the primary source (via libgit2 in caller)
//! 2. Adds Ralph-specific configuration options (config file, env vars, CLI args)
//! 3. Implements sensible fallbacks (system username, default values)
//! 4. Provides clear error messages when identity cannot be determined
//!
//! # Priority Chain
//!
//! The identity is resolved in the following order (matches standard git behavior):
//! 1. Git config (via libgit2) - primary source (local .git/config, then global ~/.gitconfig)
//! 2. Explicit CLI args - only used when git config is missing
//! 3. Environment variables (`RALPH_GIT_USER_NAME`, `RALPH_GIT_USER_EMAIL`) - fallback
//! 4. Ralph config file (`[general]` section with `git_user_name`, `git_user_email`)
//! 5. System username + derived email (sane fallback)
//! 6. Default values ("Ralph Workflow", "ralph@localhost") - last resort

#![deny(unsafe_code)]

use crate::executor::ProcessExecutor;
use crate::git_helpers::runtime::{get_system_hostname, get_system_username};

#[cfg(test)]
use crate::executor::RealProcessExecutor;

/// Git user identity information.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GitIdentity {
    /// The user's name for git commits.
    pub name: String,
    /// The user's email for git commits.
    pub email: String,
}

impl GitIdentity {
    /// Create a new `GitIdentity` with the given name and email.
    #[must_use]
    pub const fn new(name: String, email: String) -> Self {
        Self { name, email }
    }

    /// Validate that the identity is well-formed.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn validate(&self) -> Result<(), String> {
        validate_git_identity_fields(&self.name, &self.email)
    }
}

/// Pure policy: validate git identity name and email fields.
pub fn validate_git_identity_fields(name: &str, email: &str) -> Result<(), String> {
    if name.trim().is_empty() {
        return Err("Git user name cannot be empty".to_string());
    }
    if email.trim().is_empty() {
        return Err("Git user email cannot be empty".to_string());
    }
    let email = email.trim();
    if !email.contains('@') {
        return Err(format!("Invalid email format: '{email}'"));
    }
    let parts: Vec<&str> = email.split('@').collect();
    if parts.len() != 2 {
        return Err(format!("Invalid email format: '{email}'"));
    }
    if parts[0].trim().is_empty() {
        return Err(format!(
            "Invalid email format: '{email}' (missing local part)"
        ));
    }
    if parts[1].trim().is_empty() || !parts[1].contains('.') {
        return Err(format!("Invalid email format: '{email}' (invalid domain)"));
    }
    Ok(())
}

/// Pure policy: choose username from available sources.
pub fn choose_username(env_username: Option<String>, whoami_output: Option<String>) -> String {
    env_username
        .filter(|u| !u.is_empty())
        .or_else(|| whoami_output.map(|o| o.trim().to_string()))
        .filter(|u| !u.is_empty())
        .unwrap_or_else(|| "Unknown User".to_string())
}

/// Pure policy: choose hostname from available sources.
pub fn choose_hostname(
    env_hostname: Option<String>,
    hostname_output: Option<String>,
) -> Option<String> {
    env_hostname
        .filter(|h| !h.is_empty())
        .or_else(|| hostname_output.map(|h| h.trim().to_string()))
        .filter(|h| !h.is_empty())
}
    if email.trim().is_empty() {
        return Err("Git user email cannot be empty".to_string());
    }
    let email = email.trim();
    if !email.contains('@') {
        return Err(format!("Invalid email format: '{email}'"));
    }
    let parts: Vec<&str> = email.split('@').collect();
    if parts.len() != 2 {
        return Err(format!("Invalid email format: '{email}'"));
    }
    if parts[0].trim().is_empty() {
        return Err(format!(
            "Invalid email format: '{email}' (missing local part)"
        ));
    }
    if parts[1].trim().is_empty() || !parts[1].contains('.') {
        return Err(format!("Invalid email format: '{email}' (invalid domain)"));
    }
    Ok(())
}

/// Pure policy: choose username from available sources.
fn choose_username(env_username: Option<String>, whoami_output: Option<String>) -> String {
    if let Some(user) = env_username {
        if !user.is_empty() {
            return user;
        }
    }
    if let Some(output) = whoami_output {
        let username = output.trim().to_string();
        if !username.is_empty() {
            return username;
        }
    }
    "Unknown User".to_string()
}

/// Get the system username as a fallback.
///
/// Uses platform-specific methods:
/// - On Unix: `whoami` command, fallback to `$USER` env var
/// - On Windows: `%USERNAME%` env var
#[must_use]
pub fn fallback_username(executor: Option<&dyn ProcessExecutor>) -> String {
    let env_username = get_system_username();
    let whoami_output = if cfg!(unix) {
        executor.and_then(|exec| {
            exec.execute("whoami", &[], &[], None)
                .ok()
                .map(|o| o.stdout)
        })
    } else {
        None
    };
    choose_username(env_username, whoami_output)
}

/// Pure policy: choose hostname from available sources.
fn choose_hostname(
    env_hostname: Option<String>,
    hostname_output: Option<String>,
) -> Option<String> {
    if let Some(host) = env_hostname {
        if !host.is_empty() {
            return Some(host);
        }
    }
    hostname_output.filter(|h| !h.is_empty())
}

/// Get a fallback email based on the username.
#[must_use]
pub fn fallback_email(username: &str, executor: Option<&dyn ProcessExecutor>) -> String {
    let hostname = resolve_hostname_impl(executor);
    let host = hostname.unwrap_or_else(|| "localhost".to_string());
    format!("{username}@{host}")
}

/// Internal hostname resolution.
fn resolve_hostname_impl(executor: Option<&dyn ProcessExecutor>) -> Option<String> {
    let env_hostname = get_system_hostname();
    let hostname_output = executor.and_then(|exec| {
        exec.execute("hostname", &[], &[], None)
            .ok()
            .map(|o| o.stdout.trim().to_string())
    });
    choose_hostname(env_hostname, hostname_output)
}

/// Get the default git identity (last resort).
///
/// This should never be reached if the fallback chain is working correctly.
#[must_use]
pub fn default_identity() -> GitIdentity {
    GitIdentity::new("Ralph Workflow".to_string(), "ralph@localhost".to_string())
}

/// Helper trait for error checking in tests
#[cfg(test)]
trait ContainsErr {
    fn contains_err(&self, needle: &str) -> bool;
}

#[cfg(test)]
impl ContainsErr for Result<(), String> {
    fn contains_err(&self, needle: &str) -> bool {
        match self {
            Err(e) => e.contains(needle),
            _ => false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_git_identity_validation_valid() {
        let identity = GitIdentity::new("Test User".to_string(), "test@example.com".to_string());
        assert!(identity.validate().is_ok());
    }

    #[test]
    fn test_git_identity_validation_empty_name() {
        let identity = GitIdentity::new(String::new(), "test@example.com".to_string());
        assert!(identity
            .validate()
            .contains_err("Git user name cannot be empty"));
    }

    #[test]
    fn test_git_identity_validation_empty_email() {
        let identity = GitIdentity::new("Test User".to_string(), String::new());
        assert!(identity
            .validate()
            .contains_err("Git user email cannot be empty"));
    }

    #[test]
    fn test_git_identity_validation_invalid_email_no_at() {
        let identity = GitIdentity::new("Test User".to_string(), "invalidemail".to_string());
        assert!(identity.validate().contains_err("Invalid email format"));
    }

    #[test]
    fn test_git_identity_validation_invalid_email_no_domain() {
        let identity = GitIdentity::new("Test User".to_string(), "user@".to_string());
        assert!(identity.validate().contains_err("Invalid email format"));
    }

    #[test]
    fn test_fallback_username_not_empty() {
        let executor = RealProcessExecutor::new();
        let username = fallback_username(Some(&executor));
        assert!(!username.is_empty());
    }

    #[test]
    fn test_fallback_email_format() {
        let username = "testuser";
        let executor = RealProcessExecutor::new();
        let email = fallback_email(username, Some(&executor));
        assert!(email.contains('@'));
        assert!(email.starts_with(username));
    }

    #[test]
    fn test_fallback_username_without_executor() {
        let username = fallback_username(None);
        assert!(!username.is_empty());
    }

    #[test]
    fn test_fallback_email_without_executor() {
        let username = "testuser";
        let email = fallback_email(username, None);
        assert!(email.contains('@'));
        assert!(email.starts_with(username));
    }

    #[test]
    fn test_default_identity() {
        let identity = default_identity();
        assert_eq!(identity.name, "Ralph Workflow");
        assert_eq!(identity.email, "ralph@localhost");
    }
}
