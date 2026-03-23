//! Environment access in the runtime boundary.
//!
//! This module provides environment-related capabilities that domain code
//! can use through trait abstraction.

use std::collections::HashMap;

/// Trait for environment variable access, allowing testability.
pub trait Environment: Send + Sync {
    /// Get an environment variable.
    fn var(&self, key: &str) -> Option<String>;

    /// Get all environment variables.
    fn vars(&self) -> HashMap<String, String>;
}

/// Real environment implementation using std::env.
pub struct RealEnvironment;

impl Environment for RealEnvironment {
    fn var(&self, key: &str) -> Option<String> {
        std::env::var(key).ok()
    }

    fn vars(&self) -> HashMap<String, String> {
        std::env::vars().collect()
    }
}

/// Trait for git-specific environment configuration.
///
/// Allows handlers to configure git authentication without directly calling
/// `std::env::set_var`, keeping the imperative mutation in the runtime boundary.
pub trait GitEnvironment: Send + Sync {
    /// Configure GIT_SSH_COMMAND to use a specific SSH key.
    fn configure_git_ssh_command(&self, key_path: &str) -> Result<(), GitEnvError>;

    /// Disable git terminal prompt (GIT_TERMINAL_PROMPT=0).
    fn disable_git_terminal_prompt(&self) -> Result<(), GitEnvError>;
}

/// Error returned when git environment configuration fails.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GitEnvError(String);

impl GitEnvError {
    #[must_use]
    pub fn new(msg: impl Into<String>) -> Self {
        Self(msg.into())
    }
}

impl std::fmt::Display for GitEnvError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for GitEnvError {}

/// Real git environment implementation using std::env.
pub struct RealGitEnvironment;

fn validate_ssh_key_path(key_path: &str) -> Result<(), GitEnvError> {
    if key_path.trim().is_empty() {
        return Err(GitEnvError::new("empty SSH key path"));
    }
    if key_path.contains('\0') || key_path.contains('\n') || key_path.contains('\r') {
        return Err(GitEnvError::new("SSH key path contains invalid characters"));
    }
    Ok(())
}

impl GitEnvironment for RealGitEnvironment {
    fn configure_git_ssh_command(&self, key_path: &str) -> Result<(), GitEnvError> {
        validate_ssh_key_path(key_path)?;
        let escaped = shell_escape_posix(key_path);
        let cmd = format!("ssh -o 'IdentitiesOnly=yes' -i {escaped}");
        std::env::set_var("GIT_SSH_COMMAND", &cmd);
        Ok(())
    }

    fn disable_git_terminal_prompt(&self) -> Result<(), GitEnvError> {
        std::env::set_var("GIT_TERMINAL_PROMPT", "0");
        Ok(())
    }
}

fn shell_escape_posix(s: &str) -> String {
    let inner: String = s
        .chars()
        .flat_map(|ch| {
            if ch == '\'' {
                "'\"'\"'".chars().collect::<Vec<_>>()
            } else {
                vec![ch]
            }
        })
        .collect();
    format!("'{inner}'")
}

/// Mock git environment for testing.
///
/// Tracks configured values without touching the real process environment.
#[cfg(any(test, feature = "test-utils"))]
pub mod mock {
    use super::GitEnvError;
    use std::sync::Mutex;

    pub struct MockGitEnvironment {
        pub ssh_commands: Mutex<Vec<String>>,
        pub terminal_prompts_disabled: Mutex<bool>,
        pub errors: Mutex<Vec<GitEnvError>>,
    }

    impl Clone for MockGitEnvironment {
        fn clone(&self) -> Self {
            Self {
                ssh_commands: Mutex::new(self.ssh_commands.lock().unwrap().clone()),
                terminal_prompts_disabled: Mutex::new(
                    *self.terminal_prompts_disabled.lock().unwrap(),
                ),
                errors: Mutex::new(self.errors.lock().unwrap().clone()),
            }
        }
    }

    impl MockGitEnvironment {
        #[must_use]
        pub fn new() -> Self {
            Self {
                ssh_commands: Mutex::new(Vec::new()),
                terminal_prompts_disabled: Mutex::new(false),
                errors: Mutex::new(Vec::new()),
            }
        }

        #[must_use]
        pub fn configured_ssh_keys(&self) -> Vec<String> {
            self.ssh_commands.lock().unwrap().clone()
        }

        #[must_use]
        pub fn terminal_prompt_disabled(&self) -> bool {
            *self.terminal_prompts_disabled.lock().unwrap()
        }
    }

    impl Default for MockGitEnvironment {
        fn default() -> Self {
            Self::new()
        }
    }

    impl super::GitEnvironment for MockGitEnvironment {
        fn configure_git_ssh_command(&self, key_path: &str) -> Result<(), GitEnvError> {
            super::validate_ssh_key_path(key_path)?;
            let escaped = super::shell_escape_posix(key_path);
            let cmd = format!("ssh -o 'IdentitiesOnly=yes' -i {escaped}");
            self.ssh_commands.lock().unwrap().push(cmd);
            Ok(())
        }

        fn disable_git_terminal_prompt(&self) -> Result<(), GitEnvError> {
            *self.terminal_prompts_disabled.lock().unwrap() = true;
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::mock::MockGitEnvironment;
    use super::GitEnvironment;

    #[test]
    fn mock_git_environment_configures_ssh_command() {
        let env = MockGitEnvironment::new();
        env.configure_git_ssh_command("/home/user/.ssh/id_rsa")
            .unwrap();
        let keys = env.configured_ssh_keys();
        assert_eq!(keys.len(), 1);
        assert!(keys[0].contains("id_rsa"));
    }

    #[test]
    fn mock_git_environment_rejects_empty_ssh_key_path() {
        let env = MockGitEnvironment::new();
        let result = env.configure_git_ssh_command("");
        assert!(result.is_err());
    }

    #[test]
    fn mock_git_environment_rejects_newline_in_ssh_key_path() {
        let env = MockGitEnvironment::new();
        let result = env.configure_git_ssh_command("/tmp/key\n-oProxyCommand=evil");
        assert!(result.is_err());
    }

    #[test]
    fn mock_git_environment_rejects_carriage_return_in_ssh_key_path() {
        let env = MockGitEnvironment::new();
        let result = env.configure_git_ssh_command("/tmp/key\r-oProxyCommand=evil");
        assert!(result.is_err());
    }

    #[test]
    fn mock_git_environment_disables_terminal_prompt() {
        let env = MockGitEnvironment::new();
        env.disable_git_terminal_prompt().unwrap();
        assert!(env.terminal_prompt_disabled());
    }

    #[test]
    fn shell_escape_wraps_in_single_quotes() {
        assert_eq!(super::shell_escape_posix("/a b"), "'/a b'");
    }

    #[test]
    fn shell_escape_handles_single_quotes() {
        assert_eq!(super::shell_escape_posix("a'b"), "'a'\"'\"'b'");
    }
}
