//! Cloud and git remote configuration types.
//!
//! This module defines the cloud runtime configuration and git remote types
//! used when Ralph is running in cloud-hosted mode.

use crate::common::domain_types::{
    HttpsUrl, NonEmptyString, PushBranch, PushBranchParseError, RemoteName,
};

#[must_use]
pub(crate) fn load_cloud_config_from_env() -> CloudConfig {
    super::boundary::load_cloud_config_from_env()
}

/// Typed error for cloud configuration validation.
///
/// `CloudConfig::validate()` returns `Result<(), CloudConfigValidationError>`.
/// Boundary code can call `.to_string()` for human-readable messages.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CloudConfigValidationError {
    /// `RALPH_CLOUD_API_URL` must be set when cloud mode is enabled.
    ApiUrlMissing,
    /// `RALPH_CLOUD_API_URL` must use `https://` when cloud mode is enabled.
    ApiUrlNotHttps,
    /// `RALPH_CLOUD_API_TOKEN` must be set when cloud mode is enabled.
    ApiTokenMissing,
    /// `RALPH_CLOUD_RUN_ID` must be set when cloud mode is enabled.
    RunIdMissing,
    /// Git remote configuration is invalid.
    GitRemote(GitRemoteValidationError),
}

impl std::fmt::Display for CloudConfigValidationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::ApiUrlMissing => {
                write!(
                    f,
                    "RALPH_CLOUD_API_URL must be set when cloud mode is enabled"
                )
            }
            Self::ApiUrlNotHttps => write!(
                f,
                "RALPH_CLOUD_API_URL must use https:// when cloud mode is enabled"
            ),
            Self::ApiTokenMissing => write!(
                f,
                "RALPH_CLOUD_API_TOKEN must be set when cloud mode is enabled"
            ),
            Self::RunIdMissing => {
                write!(
                    f,
                    "RALPH_CLOUD_RUN_ID must be set when cloud mode is enabled"
                )
            }
            Self::GitRemote(e) => write!(f, "{e}"),
        }
    }
}

impl From<GitRemoteValidationError> for CloudConfigValidationError {
    fn from(e: GitRemoteValidationError) -> Self {
        Self::GitRemote(e)
    }
}

/// Typed error for git remote configuration validation.
///
/// `GitRemoteConfig::validate()` returns `Result<(), GitRemoteValidationError>`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GitRemoteValidationError {
    /// `RALPH_GIT_REMOTE` must not be empty.
    EmptyRemoteName,
    /// `RALPH_GIT_PUSH_BRANCH` must not be empty when set.
    EmptyPushBranch,
    /// `RALPH_GIT_PUSH_BRANCH` must be a branch name, not literal `HEAD`.
    PushBranchIsHead,
    /// `RALPH_GIT_SSH_KEY_PATH` must not be empty when set.
    EmptySshKeyPath,
    /// `RALPH_GIT_TOKEN` must be set when token auth is used.
    EmptyToken,
    /// `RALPH_GIT_TOKEN_USERNAME` must not be empty when token auth is used.
    EmptyTokenUsername,
    /// `RALPH_GIT_CREDENTIAL_HELPER` must be set when credential-helper auth is used.
    EmptyCredentialHelper,
}

impl std::fmt::Display for GitRemoteValidationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::EmptyRemoteName => write!(f, "RALPH_GIT_REMOTE must not be empty"),
            Self::EmptyPushBranch => {
                write!(f, "RALPH_GIT_PUSH_BRANCH must not be empty when set")
            }
            Self::PushBranchIsHead => write!(
                f,
                "RALPH_GIT_PUSH_BRANCH must be a branch name (not literal 'HEAD')"
            ),
            Self::EmptySshKeyPath => {
                write!(f, "RALPH_GIT_SSH_KEY_PATH must not be empty when set")
            }
            Self::EmptyToken => write!(
                f,
                "RALPH_GIT_TOKEN must be set when RALPH_GIT_AUTH_METHOD=token"
            ),
            Self::EmptyTokenUsername => write!(
                f,
                "RALPH_GIT_TOKEN_USERNAME must not be empty when RALPH_GIT_AUTH_METHOD=token"
            ),
            Self::EmptyCredentialHelper => write!(
                f,
                "RALPH_GIT_CREDENTIAL_HELPER must be set when RALPH_GIT_AUTH_METHOD=credential-helper"
            ),
        }
    }
}

/// Cloud runtime configuration (internal).
///
/// This struct is loaded from environment variables when cloud mode is enabled.
#[derive(Debug, Clone, Default, PartialEq)]
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
#[derive(Debug, Clone, PartialEq)]
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

#[derive(Debug, Clone, PartialEq)]
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
    /// # Errors
    ///
    /// Returns a `CloudConfigValidationError` if required fields are missing or invalid.
    pub fn validate(&self) -> Result<(), CloudConfigValidationError> {
        if !self.enabled {
            return Ok(());
        }

        let Some(api_url) = self.api_url.as_deref() else {
            return Err(CloudConfigValidationError::ApiUrlMissing);
        };
        HttpsUrl::try_from_str(api_url).map_err(|_| CloudConfigValidationError::ApiUrlNotHttps)?;

        NonEmptyString::try_from_str(self.api_token.as_deref().unwrap_or_default())
            .map_err(|_| CloudConfigValidationError::ApiTokenMissing)?;

        NonEmptyString::try_from_str(self.run_id.as_deref().unwrap_or_default())
            .map_err(|_| CloudConfigValidationError::RunIdMissing)?;

        // Validate git remote config when cloud mode is enabled.
        self.git_remote
            .validate()
            .map_err(CloudConfigValidationError::GitRemote)?;

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
    /// # Errors
    ///
    /// Returns a `GitRemoteValidationError` if:
    /// - Remote name is empty
    /// - Push branch is invalid
    /// - Auth method configuration is invalid
    pub fn validate(&self) -> Result<(), GitRemoteValidationError> {
        RemoteName::try_from_str(&self.remote_name)
            .map_err(|_| GitRemoteValidationError::EmptyRemoteName)?;

        if let Some(branch) = self.push_branch.as_deref() {
            PushBranch::try_from_str(branch).map_err(|err| match err {
                PushBranchParseError::Empty => GitRemoteValidationError::EmptyPushBranch,
                PushBranchParseError::IsHead => GitRemoteValidationError::PushBranchIsHead,
            })?;
        }

        match &self.auth_method {
            GitAuthMethod::SshKey { key_path } => {
                if let Some(path) = key_path.as_deref() {
                    NonEmptyString::try_from_str(path)
                        .map_err(|_| GitRemoteValidationError::EmptySshKeyPath)?;
                }
            }
            GitAuthMethod::Token { token, username } => {
                NonEmptyString::try_from_str(token)
                    .map_err(|_| GitRemoteValidationError::EmptyToken)?;
                NonEmptyString::try_from_str(username)
                    .map_err(|_| GitRemoteValidationError::EmptyTokenUsername)?;
            }
            GitAuthMethod::CredentialHelper { helper } => {
                NonEmptyString::try_from_str(helper)
                    .map_err(|_| GitRemoteValidationError::EmptyCredentialHelper)?;
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
    // --- Typed error RED tests for CloudConfigValidationError ---

    #[test]
    fn test_validate_missing_api_url_returns_api_url_missing_variant() {
        let config = CloudConfig {
            enabled: true,
            api_url: None,
            api_token: Some("token".to_string()),
            run_id: Some("run".to_string()),
            ..CloudConfig::default()
        };
        assert_eq!(
            config.validate(),
            Err(CloudConfigValidationError::ApiUrlMissing)
        );
    }

    #[test]
    fn test_validate_non_https_returns_api_url_not_https_variant() {
        let config = CloudConfig {
            enabled: true,
            api_url: Some("http://api.example.com".to_string()),
            api_token: Some("token".to_string()),
            run_id: Some("run".to_string()),
            ..CloudConfig::default()
        };
        assert!(
            matches!(
                config.validate(),
                Err(CloudConfigValidationError::ApiUrlNotHttps)
            ),
            "expected ApiUrlNotHttps"
        );
    }

    #[test]
    fn test_validate_missing_token_returns_api_token_missing_variant() {
        let config = CloudConfig {
            enabled: true,
            api_url: Some("https://api.example.com".to_string()),
            api_token: None,
            run_id: Some("run".to_string()),
            ..CloudConfig::default()
        };
        assert_eq!(
            config.validate(),
            Err(CloudConfigValidationError::ApiTokenMissing)
        );
    }

    #[test]
    fn test_validate_missing_run_id_returns_run_id_missing_variant() {
        let config = CloudConfig {
            enabled: true,
            api_url: Some("https://api.example.com".to_string()),
            api_token: Some("token".to_string()),
            run_id: None,
            ..CloudConfig::default()
        };
        assert_eq!(
            config.validate(),
            Err(CloudConfigValidationError::RunIdMissing)
        );
    }

    #[test]
    fn test_cloud_config_validation_error_display_not_empty() {
        for err in [
            CloudConfigValidationError::ApiUrlMissing,
            CloudConfigValidationError::ApiUrlNotHttps,
            CloudConfigValidationError::ApiTokenMissing,
            CloudConfigValidationError::RunIdMissing,
        ] {
            assert!(
                !err.to_string().is_empty(),
                "display must not be empty for {err:?}"
            );
        }
    }

    // --- Typed error RED tests for GitRemoteValidationError ---

    #[test]
    fn test_git_remote_validate_empty_remote_name_returns_typed_variant() {
        let config = GitRemoteConfig {
            remote_name: String::new(),
            ..GitRemoteConfig::default()
        };
        assert_eq!(
            config.validate(),
            Err(GitRemoteValidationError::EmptyRemoteName)
        );
    }

    #[test]
    fn test_git_remote_validate_head_push_branch_returns_typed_variant() {
        let config = GitRemoteConfig {
            push_branch: Some("HEAD".to_string()),
            ..GitRemoteConfig::default()
        };
        assert_eq!(
            config.validate(),
            Err(GitRemoteValidationError::PushBranchIsHead)
        );
    }

    #[test]
    fn test_git_remote_validate_empty_push_branch_returns_typed_variant() {
        let config = GitRemoteConfig {
            push_branch: Some(String::new()),
            ..GitRemoteConfig::default()
        };
        assert_eq!(
            config.validate(),
            Err(GitRemoteValidationError::EmptyPushBranch)
        );
    }

    #[test]
    fn test_git_remote_validate_empty_ssh_key_path_returns_typed_variant() {
        let config = GitRemoteConfig {
            auth_method: GitAuthMethod::SshKey {
                key_path: Some(String::new()),
            },
            ..GitRemoteConfig::default()
        };
        assert_eq!(
            config.validate(),
            Err(GitRemoteValidationError::EmptySshKeyPath)
        );
    }

    #[test]
    fn test_git_remote_validate_empty_token_returns_typed_variant() {
        let config = GitRemoteConfig {
            auth_method: GitAuthMethod::Token {
                token: String::new(),
                username: "oauth2".to_string(),
            },
            ..GitRemoteConfig::default()
        };
        assert_eq!(config.validate(), Err(GitRemoteValidationError::EmptyToken));
    }

    #[test]
    fn test_git_remote_validate_empty_token_username_returns_typed_variant() {
        let config = GitRemoteConfig {
            auth_method: GitAuthMethod::Token {
                token: "ghp_valid_token".to_string(),
                username: String::new(),
            },
            ..GitRemoteConfig::default()
        };
        assert_eq!(
            config.validate(),
            Err(GitRemoteValidationError::EmptyTokenUsername)
        );
    }

    #[test]
    fn test_git_remote_validate_empty_credential_helper_returns_typed_variant() {
        let config = GitRemoteConfig {
            auth_method: GitAuthMethod::CredentialHelper {
                helper: String::new(),
            },
            ..GitRemoteConfig::default()
        };
        assert_eq!(
            config.validate(),
            Err(GitRemoteValidationError::EmptyCredentialHelper)
        );
    }

    #[test]
    fn test_cloud_config_validate_git_remote_error_returns_git_remote_variant() {
        let config = CloudConfig {
            enabled: true,
            api_url: Some("https://api.example.com".to_string()),
            api_token: Some("token".to_string()),
            run_id: Some("run-id".to_string()),
            git_remote: GitRemoteConfig {
                remote_name: String::new(),
                ..GitRemoteConfig::default()
            },
            ..CloudConfig::default()
        };
        assert!(
            matches!(
                config.validate(),
                Err(CloudConfigValidationError::GitRemote(
                    GitRemoteValidationError::EmptyRemoteName
                ))
            ),
            "expected GitRemote(EmptyRemoteName) variant"
        );
    }
}
