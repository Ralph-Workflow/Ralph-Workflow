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
        if self.name.trim().is_empty() {
            return Err("Git user name cannot be empty".to_string());
        }
        if self.email.trim().is_empty() {
            return Err("Git user email cannot be empty".to_string());
        }
        // Basic email validation - check for @ and at least one . after @
        let email = self.email.trim();
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
}

/// Get the system username as a fallback.
///
/// Uses platform-specific methods:
/// - On Unix: `whoami` command, fallback to `$USER` env var
/// - On Windows: `%USERNAME%` env var
#[must_use]
pub fn fallback_username(executor: Option<&dyn ProcessExecutor>) -> String {
    // Try environment variables first (fastest) - uses runtime function
    if let Some(user) = get_system_username() {
        return user;
    }

    // As a last resort, try to run whoami (Unix only)
    if cfg!(unix) {
        if let Some(exec) = executor {
            if let Ok(output) = exec.execute("whoami", &[], &[], None) {
                let username = output.stdout.trim().to_string();
                if !username.is_empty() {
                    return username;
                }
            }
        }
    }

    // Ultimate fallback
    "Unknown User".to_string()
}

/// Get a fallback email based on the username.
///
/// Format: `{username}@{hostname}` or `{username}@localhost`
#[must_use]
pub fn fallback_email(username: &str, executor: Option<&dyn ProcessExecutor>) -> String {
    // Try to get hostname
    let hostname = match get_hostname(executor) {
        Some(host) if !host.is_empty() => host,
        _ => "localhost".to_string(),
    };

    format!("{username}@{hostname}")
}

/// Get the system hostname.
fn get_hostname(executor: Option<&dyn ProcessExecutor>) -> Option<String> {
    // Try HOSTNAME environment variable first (fastest) - uses runtime function
    if let Some(hostname) = get_system_hostname() {
        return Some(hostname);
    }

    // Try the `hostname` command
    if let Some(exec) = executor {
        if let Ok(output) = exec.execute("hostname", &[], &[], None) {
            let hostname = output.stdout.trim().to_string();
            if !hostname.is_empty() {
                return Some(hostname);
            }
        }
    }

    None
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
