//! Cloud and git remote configuration types.
//!
//! This module defines the cloud runtime configuration and git remote types
//! used when Ralph is running in cloud-hosted mode.

/// Cloud runtime configuration (internal).
///
/// This struct is loaded from environment variables when cloud mode is enabled.
#[derive(Debug, Clone, Default)]
pub struct CloudConfig {
    /// Enable cloud reporting mode (internal env-config).
    pub enabled: bool,
    /// Cloud API base URL.
    pub api_url: Option<String>,
    /// Bearer token for API authentication.
    pub api_token: Option<String>,
    /// Run ID assigned by cloud orchestrator.
    pub run_id: Option<String>,
    /// Heartbeat interval in seconds.
    pub heartbeat_interval_secs: u32,
    /// Whether to continue on API failures.
    pub graceful_degradation: bool,
    /// Git remote configuration
    pub git_remote: GitRemoteConfig,
}

/// Git remote configuration (internal).
///
/// Loaded from environment variables when cloud mode is enabled.
#[derive(Debug, Clone)]
pub struct GitRemoteConfig {
    /// Authentication method for git operations
    pub auth_method: GitAuthMethod,
    /// Branch to push to (defaults to current branch)
    pub push_branch: Option<String>,
    /// Whether to create a PR instead of direct push
    pub create_pr: bool,
    /// PR title template (supports {`run_id`}, {`prompt_summary`} placeholders)
    pub pr_title_template: Option<String>,
    /// PR body template
    pub pr_body_template: Option<String>,
    /// Base branch for PR (defaults to main/master)
    pub pr_base_branch: Option<String>,
    /// Whether to force push (dangerous, disabled by default)
    pub force_push: bool,
    /// Remote name (defaults to "origin")
    pub remote_name: String,
}

#[derive(Debug, Clone)]
pub enum GitAuthMethod {
    /// Use SSH key (default for containers with mounted keys)
    SshKey {
        /// Path to private key (default: /`root/.ssh/id_rsa` or `SSH_AUTH_SOCK`)
        key_path: Option<String>,
    },
    /// Use token-based HTTPS authentication
    Token {
        /// Git token (from `RALPH_GIT_TOKEN` env var)
        token: String,
        /// Username for token auth (often "oauth2" or "x-access-token")
        username: String,
    },
    /// Use git credential helper (for cloud provider integrations)
    CredentialHelper {
        /// Helper command (e.g., "gcloud", "aws codecommit credential-helper")
        helper: String,
    },
}

/// Cloud configuration that is safe to store in reducer state / checkpoints.
///
/// This is a *redacted* view of [`CloudConfig`]: it carries only non-sensitive
/// fields required for pure orchestration.
///
/// In particular, it MUST NOT contain API tokens, git tokens, or any other
/// credential material.
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct CloudStateConfig {
    pub enabled: bool,
    pub api_url: Option<String>,
    pub run_id: Option<String>,
    pub heartbeat_interval_secs: u32,
    pub graceful_degradation: bool,
    pub git_remote: GitRemoteStateConfig,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct GitRemoteStateConfig {
    pub auth_method: GitAuthStateMethod,
    pub push_branch: String,
    pub create_pr: bool,
    pub pr_title_template: Option<String>,
    pub pr_body_template: Option<String>,
    pub pr_base_branch: Option<String>,
    pub force_push: bool,
    pub remote_name: String,
}

impl Default for GitRemoteStateConfig {
    fn default() -> Self {
        Self {
            auth_method: GitAuthStateMethod::SshKey { key_path: None },
            push_branch: String::new(),
            create_pr: false,
            pr_title_template: None,
            pr_body_template: None,
            pr_base_branch: None,
            force_push: false,
            remote_name: "origin".to_string(),
        }
    }
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub enum GitAuthStateMethod {
    SshKey { key_path: Option<String> },
    Token { username: String },
    CredentialHelper { helper: String },
}

impl Default for GitAuthStateMethod {
    fn default() -> Self {
        Self::SshKey { key_path: None }
    }
}

impl CloudStateConfig {
    #[must_use]
    pub fn disabled() -> Self {
        Self {
            enabled: false,
            api_url: None,
            run_id: None,
            heartbeat_interval_secs: 30,
            graceful_degradation: true,
            git_remote: GitRemoteStateConfig::default(),
        }
    }
}

impl From<&CloudConfig> for CloudStateConfig {
    fn from(cfg: &CloudConfig) -> Self {
        let auth_method = match &cfg.git_remote.auth_method {
            GitAuthMethod::SshKey { key_path } => GitAuthStateMethod::SshKey {
                key_path: key_path.clone(),
            },
            GitAuthMethod::Token { username, .. } => GitAuthStateMethod::Token {
                username: username.clone(),
            },
            GitAuthMethod::CredentialHelper { helper } => GitAuthStateMethod::CredentialHelper {
                helper: helper.clone(),
            },
        };

        Self {
            enabled: cfg.enabled,
            api_url: cfg.api_url.clone(),
            run_id: cfg.run_id.clone(),
            heartbeat_interval_secs: cfg.heartbeat_interval_secs,
            graceful_degradation: cfg.graceful_degradation,
            git_remote: GitRemoteStateConfig {
                auth_method,
                push_branch: cfg.git_remote.push_branch.clone().unwrap_or_default(),
                create_pr: cfg.git_remote.create_pr,
                pr_title_template: cfg.git_remote.pr_title_template.clone(),
                pr_body_template: cfg.git_remote.pr_body_template.clone(),
                pr_base_branch: cfg.git_remote.pr_base_branch.clone(),
                force_push: cfg.git_remote.force_push,
                remote_name: cfg.git_remote.remote_name.clone(),
            },
        }
    }
}

impl Default for GitAuthMethod {
    fn default() -> Self {
        Self::SshKey { key_path: None }
    }
}

impl Default for GitRemoteConfig {
    fn default() -> Self {
        Self {
            auth_method: GitAuthMethod::default(),
            push_branch: None,
            create_pr: false,
            pr_title_template: None,
            pr_body_template: None,
            pr_base_branch: None,
            force_push: false,
            remote_name: "origin".to_string(),
        }
    }
}

impl CloudConfig {
    /// Load cloud configuration from a caller-supplied env-var accessor.
    /// This is the canonical implementation; `from_env` is a thin wrapper.
    #[must_use]
    pub fn from_env_fn(get: impl Fn(&str) -> Option<String>) -> Self {
        let enabled =
            get("RALPH_CLOUD_MODE").is_some_and(|v| v.eq_ignore_ascii_case("true") || v == "1");

        if !enabled {
            return Self::disabled();
        }

        Self {
            enabled: true,
            api_url: get("RALPH_CLOUD_API_URL"),
            api_token: get("RALPH_CLOUD_API_TOKEN"),
            run_id: get("RALPH_CLOUD_RUN_ID"),
            heartbeat_interval_secs: get("RALPH_CLOUD_HEARTBEAT_INTERVAL")
                .and_then(|v| v.parse().ok())
                .unwrap_or(30),
            graceful_degradation: get("RALPH_CLOUD_GRACEFUL_DEGRADATION")
                .is_none_or(|v| !v.eq_ignore_ascii_case("false") && v != "0"),
            git_remote: GitRemoteConfig::from_env_fn(|k| get(k)),
        }
    }

    /// Load cloud config from environment variables ONLY.
    /// Returns disabled config when cloud mode is not enabled.
    #[must_use]
    pub fn from_env() -> Self {
        Self::from_env_fn(|k| std::env::var(k).ok())
    }

    #[must_use]
    pub fn disabled() -> Self {
        Self {
            enabled: false,
            api_url: None,
            api_token: None,
            run_id: None,
            heartbeat_interval_secs: 30,
            graceful_degradation: true,
            git_remote: GitRemoteConfig::default(),
        }
    }

    /// Validate that required fields are present when enabled.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn validate(&self) -> Result<(), String> {
        if !self.enabled {
            return Ok(());
        }

        let Some(api_url) = self.api_url.as_deref() else {
            return Err("RALPH_CLOUD_API_URL must be set when cloud mode is enabled".to_string());
        };
        if !api_url
            .trim_start()
            .to_ascii_lowercase()
            .starts_with("https://")
        {
            return Err(
                "RALPH_CLOUD_API_URL must use https:// when cloud mode is enabled".to_string(),
            );
        }

        if self.api_token.as_deref().unwrap_or_default().is_empty() {
            return Err("RALPH_CLOUD_API_TOKEN must be set when cloud mode is enabled".to_string());
        }

        if self.run_id.as_deref().unwrap_or_default().is_empty() {
            return Err("RALPH_CLOUD_RUN_ID must be set when cloud mode is enabled".to_string());
        }

        // Validate git remote config when cloud mode is enabled.
        self.git_remote.validate()?;

        Ok(())
    }
}

impl GitRemoteConfig {
    /// # Errors
    ///
    /// Returns an error if:
    /// - Remote name is empty
    /// - Push branch is invalid
    /// - Auth method configuration is invalid
    pub fn validate(&self) -> Result<(), String> {
        if self.remote_name.trim().is_empty() {
            return Err("RALPH_GIT_REMOTE must not be empty".to_string());
        }

        if let Some(branch) = self.push_branch.as_deref() {
            let trimmed = branch.trim();
            if trimmed.is_empty() {
                return Err("RALPH_GIT_PUSH_BRANCH must not be empty when set".to_string());
            }
            if trimmed == "HEAD" {
                return Err(
                    "RALPH_GIT_PUSH_BRANCH must be a branch name (not literal 'HEAD')".to_string(),
                );
            }
        }

        match &self.auth_method {
            GitAuthMethod::SshKey { key_path } => {
                if let Some(path) = key_path.as_deref() {
                    if path.trim().is_empty() {
                        return Err("RALPH_GIT_SSH_KEY_PATH must not be empty when set".to_string());
                    }
                }
            }
            GitAuthMethod::Token { token, username } => {
                if token.trim().is_empty() {
                    return Err(
                        "RALPH_GIT_TOKEN must be set when RALPH_GIT_AUTH_METHOD=token".to_string(),
                    );
                }
                if username.trim().is_empty() {
                    return Err(
                        "RALPH_GIT_TOKEN_USERNAME must not be empty when RALPH_GIT_AUTH_METHOD=token"
                            .to_string(),
                    );
                }
            }
            GitAuthMethod::CredentialHelper { helper } => {
                if helper.trim().is_empty() {
                    return Err(
                        "RALPH_GIT_CREDENTIAL_HELPER must be set when RALPH_GIT_AUTH_METHOD=credential-helper"
                            .to_string(),
                    );
                }
            }
        }

        Ok(())
    }

    /// Load git-remote configuration from a caller-supplied env-var accessor.
    /// This is the canonical implementation; `from_env` is a thin wrapper.
    #[must_use]
    pub fn from_env_fn(get: impl Fn(&str) -> Option<String>) -> Self {
        let auth_method = match get("RALPH_GIT_AUTH_METHOD")
            .unwrap_or_else(|| "ssh".to_string())
            .to_lowercase()
            .as_str()
        {
            "token" => {
                let token = get("RALPH_GIT_TOKEN").unwrap_or_default();
                let username =
                    get("RALPH_GIT_TOKEN_USERNAME").unwrap_or_else(|| "x-access-token".to_string());
                GitAuthMethod::Token { token, username }
            }
            "credential-helper" => {
                let helper =
                    get("RALPH_GIT_CREDENTIAL_HELPER").unwrap_or_else(|| "gcloud".to_string());
                GitAuthMethod::CredentialHelper { helper }
            }
            _ => {
                let key_path = get("RALPH_GIT_SSH_KEY_PATH");
                GitAuthMethod::SshKey { key_path }
            }
        };

        Self {
            auth_method,
            push_branch: get("RALPH_GIT_PUSH_BRANCH"),
            create_pr: get("RALPH_GIT_CREATE_PR")
                .is_some_and(|v| v.eq_ignore_ascii_case("true") || v == "1"),
            pr_title_template: get("RALPH_GIT_PR_TITLE"),
            pr_body_template: get("RALPH_GIT_PR_BODY"),
            pr_base_branch: get("RALPH_GIT_PR_BASE_BRANCH"),
            force_push: get("RALPH_GIT_FORCE_PUSH")
                .is_some_and(|v| v.eq_ignore_ascii_case("true") || v == "1"),
            remote_name: get("RALPH_GIT_REMOTE").unwrap_or_else(|| "origin".to_string()),
        }
    }

    /// Load git-remote configuration from the process environment.
    #[must_use]
    pub fn from_env() -> Self {
        Self::from_env_fn(|k| std::env::var(k).ok())
    }
}

#[cfg(test)]
mod cloud_tests {
    use super::*;

    #[test]
    fn test_cloud_disabled_by_default() {
        let config = CloudConfig::from_env_fn(|_| None);
        assert!(!config.enabled);
    }

    #[test]
    fn test_cloud_enabled_with_env_var() {
        let env = [
            ("RALPH_CLOUD_MODE", "true"),
            ("RALPH_CLOUD_API_URL", "https://api.example.com"),
            ("RALPH_CLOUD_API_TOKEN", "secret"),
            ("RALPH_CLOUD_RUN_ID", "run123"),
        ];
        let config = CloudConfig::from_env_fn(|k| {
            env.iter()
                .find(|(key, _)| *key == k)
                .map(|(_, v)| (*v).to_string())
        });
        assert!(config.enabled);
        assert_eq!(config.api_url, Some("https://api.example.com".to_string()));
        assert_eq!(config.run_id, Some("run123".to_string()));
    }

    #[test]
    fn test_cloud_validation_requires_fields() {
        let config = CloudConfig {
            enabled: true,
            api_url: None,
            api_token: None,
            run_id: None,
            heartbeat_interval_secs: 30,
            graceful_degradation: true,
            git_remote: GitRemoteConfig::default(),
        };

        assert!(config.validate().is_err());
    }

    #[test]
    fn test_git_auth_method_from_env() {
        let env = [
            ("RALPH_GIT_AUTH_METHOD", "token"),
            ("RALPH_GIT_TOKEN", "ghp_test"),
        ];
        let config = GitRemoteConfig::from_env_fn(|k| {
            env.iter()
                .find(|(key, _)| *key == k)
                .map(|(_, v)| (*v).to_string())
        });
        match config.auth_method {
            GitAuthMethod::Token { token, .. } => {
                assert_eq!(token, "ghp_test");
            }
            _ => panic!("Expected Token auth method"),
        }
    }

    #[test]
    fn test_cloud_disabled_validation_passes() {
        let config = CloudConfig::disabled();
        assert!(
            config.validate().is_ok(),
            "Disabled cloud config should always validate"
        );
    }

    #[test]
    fn test_cloud_validation_rejects_non_https_api_url() {
        let config = CloudConfig {
            enabled: true,
            api_url: Some("http://api.example.com".to_string()),
            api_token: Some("secret".to_string()),
            run_id: Some("run123".to_string()),
            heartbeat_interval_secs: 30,
            graceful_degradation: true,
            git_remote: GitRemoteConfig::default(),
        };
        assert!(
            config.validate().is_err(),
            "Cloud API URL must be https:// when cloud mode is enabled"
        );
    }

    #[test]
    fn test_cloud_validation_requires_git_token_for_token_auth() {
        let config = CloudConfig {
            enabled: true,
            api_url: Some("https://api.example.com".to_string()),
            api_token: Some("secret".to_string()),
            run_id: Some("run123".to_string()),
            heartbeat_interval_secs: 30,
            graceful_degradation: true,
            git_remote: GitRemoteConfig {
                auth_method: GitAuthMethod::Token {
                    token: String::new(),
                    username: "x-access-token".to_string(),
                },
                ..GitRemoteConfig::default()
            },
        };
        assert!(
            config.validate().is_err(),
            "Token auth requires a non-empty RALPH_GIT_TOKEN"
        );
    }
}
