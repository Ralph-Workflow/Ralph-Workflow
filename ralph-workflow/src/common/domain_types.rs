//! Domain newtypes for primitive-obsessed identifiers.
//!
//! This module defines strongly-typed wrappers around raw string identifiers used
//! throughout the codebase. These newtypes encode domain semantics and invariants
//! at the type level, preventing argument mixups and improving code clarity.
//!
//! # Design Principles
//!
//! - Each newtype wraps a single `String` inner value
//! - All newtypes implement `Display`, `AsRef<str>`, `From<String>`, and `From<&str>`
//! - All newtypes derive `Debug`, `Clone`, `PartialEq`, `Eq`, `Hash`
//! - Checkpoint-facing types also derive `Serialize`/`Deserialize`
//! - Validation is encoded in `TryFrom` where runtime checks are needed (e.g., hex format)

use serde::{Deserialize, Serialize};
use std::fmt;

/// Agent identifier string (e.g., "claude", "opencode", "openai/gpt-4").
///
/// Used in agent configuration, chain management, and pipeline execution.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct AgentName(String);

impl AgentName {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for AgentName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl AsRef<str> for AgentName {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

impl From<String> for AgentName {
    fn from(value: String) -> Self {
        Self(value)
    }
}

impl From<&str> for AgentName {
    fn from(value: &str) -> Self {
        Self(value.to_string())
    }
}

/// File size in bytes for artifacts such as `.agent/ISSUES.md`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct IssueFileSize(usize);

impl IssueFileSize {
    #[must_use]
    pub fn as_bytes(&self) -> usize {
        self.0
    }
}

impl fmt::Display for IssueFileSize {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} bytes", self.0)
    }
}

impl From<usize> for IssueFileSize {
    fn from(value: usize) -> Self {
        Self(value)
    }
}

/// Count of entries inside the `.agent` directory for preflight diagnostics.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct AgentDirectoryEntryCount(usize);

impl AgentDirectoryEntryCount {
    #[must_use]
    pub fn as_count(&self) -> usize {
        self.0
    }
}

impl fmt::Display for AgentDirectoryEntryCount {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} entries", self.0)
    }
}

impl From<usize> for AgentDirectoryEntryCount {
    fn from(value: usize) -> Self {
        Self(value)
    }
}

/// Non-empty text string preserved from user input.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct NonEmptyString(String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NonEmptyStringParseError {
    pub value: String,
    pub reason: NonEmptyStringParseErrorReason,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NonEmptyStringParseErrorReason {
    Empty,
    WhitespaceOnly,
}

impl fmt::Display for NonEmptyStringParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "Non-empty text expected, but got '{}' ({:?})",
            self.value, self.reason
        )
    }
}

impl std::error::Error for NonEmptyStringParseError {}

impl NonEmptyString {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn try_from_str(value: &str) -> Result<Self, NonEmptyStringParseError> {
        Self::validate(value)?;
        Ok(Self(value.to_string()))
    }

    fn validate(value: &str) -> Result<(), NonEmptyStringParseError> {
        if value.is_empty() {
            return Err(NonEmptyStringParseError {
                value: value.to_string(),
                reason: NonEmptyStringParseErrorReason::Empty,
            });
        }

        if value.trim().is_empty() {
            return Err(NonEmptyStringParseError {
                value: value.to_string(),
                reason: NonEmptyStringParseErrorReason::WhitespaceOnly,
            });
        }

        Ok(())
    }
}

impl fmt::Display for NonEmptyString {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl AsRef<str> for NonEmptyString {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

impl TryFrom<&str> for NonEmptyString {
    type Error = NonEmptyStringParseError;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Self::try_from_str(value)
    }
}

impl TryFrom<String> for NonEmptyString {
    type Error = NonEmptyStringParseError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        Self::try_from_str(&value)
    }
}

/// HTTPS URL string (must start with https://).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct HttpsUrl(String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HttpsUrlParseError {
    Empty,
    NotHttps,
}

impl fmt::Display for HttpsUrlParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Empty => write!(f, "HTTPS URL cannot be empty"),
            Self::NotHttps => write!(f, "HTTPS URL must start with https://"),
        }
    }
}

impl std::error::Error for HttpsUrlParseError {}

impl HttpsUrl {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn try_from_str(value: &str) -> Result<Self, HttpsUrlParseError> {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            return Err(HttpsUrlParseError::Empty);
        }

        if !trimmed.to_ascii_lowercase().starts_with("https://") {
            return Err(HttpsUrlParseError::NotHttps);
        }

        Ok(Self(trimmed.to_string()))
    }
}

impl fmt::Display for HttpsUrl {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl AsRef<str> for HttpsUrl {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

impl TryFrom<&str> for HttpsUrl {
    type Error = HttpsUrlParseError;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Self::try_from_str(value)
    }
}

impl TryFrom<String> for HttpsUrl {
    type Error = HttpsUrlParseError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        Self::try_from_str(&value)
    }
}

/// Git remote name (non-empty string).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct RemoteName(String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RemoteNameParseError {
    Empty,
}

impl fmt::Display for RemoteNameParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Remote name cannot be empty")
    }
}

impl std::error::Error for RemoteNameParseError {}

impl RemoteName {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn try_from_str(value: &str) -> Result<Self, RemoteNameParseError> {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            return Err(RemoteNameParseError::Empty);
        }

        Ok(Self(trimmed.to_string()))
    }
}

impl fmt::Display for RemoteName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl AsRef<str> for RemoteName {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

impl TryFrom<&str> for RemoteName {
    type Error = RemoteNameParseError;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Self::try_from_str(value)
    }
}

impl TryFrom<String> for RemoteName {
    type Error = RemoteNameParseError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        Self::try_from_str(&value)
    }
}

/// Branch name used for pushes (must not be HEAD).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct PushBranch(String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PushBranchParseError {
    Empty,
    IsHead,
}

impl fmt::Display for PushBranchParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Empty => write!(f, "Push branch cannot be empty"),
            Self::IsHead => write!(f, "Push branch cannot be HEAD"),
        }
    }
}

impl std::error::Error for PushBranchParseError {}

impl PushBranch {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn try_from_str(value: &str) -> Result<Self, PushBranchParseError> {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            return Err(PushBranchParseError::Empty);
        }
        if trimmed == "HEAD" {
            return Err(PushBranchParseError::IsHead);
        }

        Ok(Self(trimmed.to_string()))
    }
}

impl fmt::Display for PushBranch {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl AsRef<str> for PushBranch {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

impl TryFrom<&str> for PushBranch {
    type Error = PushBranchParseError;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Self::try_from_str(value)
    }
}

impl TryFrom<String> for PushBranch {
    type Error = PushBranchParseError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        Self::try_from_str(&value)
    }
}

/// Git object ID (40-character hex SHA-1 commit hash).
///
/// Validated to contain only hexadecimal characters and to be exactly 40 characters long.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct GitOid(String);

impl fmt::Display for GitOid {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl From<String> for GitOid {
    fn from(value: String) -> Self {
        Self(value)
    }
}

impl From<&str> for GitOid {
    fn from(value: &str) -> Self {
        Self(value.to_string())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GitOidParseError {
    pub value: String,
    pub reason: GitOidParseErrorReason,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GitOidParseErrorReason {
    WrongLength { expected: usize, actual: usize },
    InvalidHexCharacter { char: char, position: usize },
}

impl fmt::Display for GitOidParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.reason {
            GitOidParseErrorReason::WrongLength { expected, actual } => {
                write!(
                    f,
                    "Git OID '{}' has {} characters, expected {}",
                    self.value, actual, expected
                )
            }
            GitOidParseErrorReason::InvalidHexCharacter { char, position } => {
                write!(
                    f,
                    "Git OID '{}' contains non-hex character '{}' at position {}",
                    self.value, char, position
                )
            }
        }
    }
}

impl std::error::Error for GitOidParseError {}

impl GitOid {
    pub const LENGTH: usize = 40;

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn try_from_str(value: &str) -> Result<Self, GitOidParseError> {
        Self::validate(value)?;
        Ok(Self(value.to_string()))
    }

    fn validate(value: &str) -> Result<(), GitOidParseError> {
        if value.len() != Self::LENGTH {
            return Err(GitOidParseError {
                value: value.to_string(),
                reason: GitOidParseErrorReason::WrongLength {
                    expected: Self::LENGTH,
                    actual: value.len(),
                },
            });
        }

        value
            .char_indices()
            .find(|(_, c)| !c.is_ascii_hexdigit())
            .map_or(Ok(()), |(i, c)| {
                Err(GitOidParseError {
                    value: value.to_string(),
                    reason: GitOidParseErrorReason::InvalidHexCharacter {
                        char: c,
                        position: i,
                    },
                })
            })
    }
}

impl AsRef<str> for GitOid {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

/// Git branch name string.
///
/// Used for branch identification in rebase, cloud push, and checkpoint operations.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct BranchName(String);

impl BranchName {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for BranchName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl AsRef<str> for BranchName {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

impl From<String> for BranchName {
    fn from(value: String) -> Self {
        Self(value)
    }
}

impl From<&str> for BranchName {
    fn from(value: &str) -> Self {
        Self(value.to_string())
    }
}

/// AI model identifier string (e.g., "claude-3-5-sonnet-20241022", "gpt-4o").
///
/// Used in agent configuration and model selection.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct ModelName(String);

impl ModelName {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for ModelName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl AsRef<str> for ModelName {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

impl From<String> for ModelName {
    fn from(value: String) -> Self {
        Self(value)
    }
}

impl From<&str> for ModelName {
    fn from(value: &str) -> Self {
        Self(value.to_string())
    }
}

/// AI provider identifier string (e.g., "anthropic", "openai", "google").
///
/// Used in agent configuration and provider selection.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ProviderName(String);

impl ProviderName {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for ProviderName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl AsRef<str> for ProviderName {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

impl From<String> for ProviderName {
    fn from(value: String) -> Self {
        Self(value)
    }
}

impl From<&str> for ProviderName {
    fn from(value: &str) -> Self {
        Self(value.to_string())
    }
}

/// SHA-256 hex digest string (64-character lowercase hex).
///
/// Used for config and prompt content integrity verification.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Sha256Checksum(String);

impl Sha256Checksum {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for Sha256Checksum {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl AsRef<str> for Sha256Checksum {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

impl From<String> for Sha256Checksum {
    fn from(value: String) -> Self {
        Self(value)
    }
}

impl From<&str> for Sha256Checksum {
    fn from(value: &str) -> Self {
        Self(value.to_string())
    }
}

/// ISO-8601 timestamp string.
///
/// Used for checkpoint timestamps and other temporal tracking.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct IsoTimestamp(String);

impl IsoTimestamp {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for IsoTimestamp {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl AsRef<str> for IsoTimestamp {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

impl From<String> for IsoTimestamp {
    fn from(value: String) -> Self {
        Self(value)
    }
}

impl From<&str> for IsoTimestamp {
    fn from(value: &str) -> Self {
        Self(value.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_agent_name_from_string() {
        let name = AgentName::from(String::from("claude"));
        assert_eq!(name.as_str(), "claude");
        assert_eq!(format!("{name}"), "claude");
    }

    #[test]
    fn test_agent_name_from_str() {
        let name = AgentName::from("opencode");
        assert_eq!(name.as_str(), "opencode");
    }

    #[test]
    fn test_agent_name_as_ref() {
        let name = AgentName::from("claude");
        let s: &str = name.as_ref();
        assert_eq!(s, "claude");
    }

    #[test]
    fn test_git_oid_from_valid_string() {
        let oid = GitOid::try_from_str(&"a".repeat(40)).unwrap();
        assert_eq!(oid.as_str(), "a".repeat(40).as_str());
    }

    #[test]
    fn test_git_oid_display() {
        let oid = GitOid::try_from_str(&"b".repeat(40)).unwrap();
        assert_eq!(format!("{oid}"), "b".repeat(40));
    }

    #[test]
    fn test_git_oid_from_str() {
        let oid = GitOid::try_from_str(&"c".repeat(40)).unwrap();
        assert_eq!(oid.as_str(), "c".repeat(40).as_str());
    }

    #[test]
    fn test_git_oid_wrong_length() {
        let result = GitOid::try_from_str("abc");
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(matches!(
            err.reason,
            GitOidParseErrorReason::WrongLength {
                expected: 40,
                actual: 3
            }
        ));
    }

    #[test]
    fn test_git_oid_invalid_char() {
        let invalid = "a".repeat(39).to_string() + "g";
        let result = GitOid::try_from_str(&invalid);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(matches!(
            err.reason,
            GitOidParseErrorReason::InvalidHexCharacter { char: 'g', .. }
        ));
    }

    #[test]
    fn test_git_oid_valid_hex_characters() {
        let hex_oid = "0123456789abcdef0123456789abcdef01234567";
        let oid = GitOid::try_from_str(hex_oid).unwrap();
        assert_eq!(oid.as_str().len(), 40);
    }

    #[test]
    fn test_branch_name_from_string() {
        let name = BranchName::from(String::from("main"));
        assert_eq!(name.as_str(), "main");
        assert_eq!(format!("{name}"), "main");
    }

    #[test]
    fn test_branch_name_from_str() {
        let name = BranchName::from("feature/test");
        assert_eq!(name.as_str(), "feature/test");
    }

    #[test]
    fn test_model_name_from_string() {
        let name = ModelName::from(String::from("claude-3-5-sonnet-20241022"));
        assert_eq!(name.as_str(), "claude-3-5-sonnet-20241022");
    }

    #[test]
    fn test_model_name_from_str() {
        let name = ModelName::from("gpt-4o");
        assert_eq!(name.as_str(), "gpt-4o");
    }

    #[test]
    fn test_provider_name_from_string() {
        let name = ProviderName::from(String::from("anthropic"));
        assert_eq!(name.as_str(), "anthropic");
    }

    #[test]
    fn test_provider_name_from_str() {
        let name = ProviderName::from("openai");
        assert_eq!(name.as_str(), "openai");
    }

    #[test]
    fn test_sha256_checksum_from_string() {
        let checksum = Sha256Checksum::from(String::from(
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ));
        assert_eq!(
            checksum.as_str(),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }

    #[test]
    fn test_sha256_checksum_from_str() {
        let checksum = Sha256Checksum::from(
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        );
        assert_eq!(checksum.as_str().len(), 64);
    }

    #[test]
    fn test_iso_timestamp_from_string() {
        let ts = IsoTimestamp::from(String::from("2024-01-01T00:00:00Z"));
        assert_eq!(ts.as_str(), "2024-01-01T00:00:00Z");
    }

    #[test]
    fn test_iso_timestamp_from_str() {
        let ts = IsoTimestamp::from("2026-03-16T12:00:00Z");
        assert_eq!(ts.as_str(), "2026-03-16T12:00:00Z");
    }

    #[test]
    fn issue_file_size_formats_bytes() {
        let size = IssueFileSize::from(1024);
        assert_eq!(size.as_bytes(), 1024);
        assert!(format!("{size}").contains("1024"));
    }

    #[test]
    fn agent_directory_entry_count_formats_entries() {
        let count = AgentDirectoryEntryCount::from(7);
        assert_eq!(count.as_count(), 7);
        assert!(format!("{count}").contains("7"));
    }

    #[test]
    fn test_git_oid_clone() {
        let oid1 = GitOid::try_from_str(&"d".repeat(40)).unwrap();
        let oid2 = oid1.clone();
        assert_eq!(oid1, oid2);
    }

    #[test]
    fn test_git_oid_eq() {
        let oid1 = GitOid::try_from_str(&"e".repeat(40)).unwrap();
        let oid2 = GitOid::try_from_str(&"e".repeat(40)).unwrap();
        assert_eq!(oid1, oid2);
    }

    #[test]
    fn test_git_oid_ne() {
        let oid1 = GitOid::try_from_str(&"f".repeat(40)).unwrap();
        let oid2 = GitOid::try_from_str(&"0".repeat(40)).unwrap();
        assert_ne!(oid1, oid2);
    }

    #[test]
    fn test_git_oid_hash() {
        use std::collections::HashSet;
        let oid1 = GitOid::try_from_str(&"1".repeat(40)).unwrap();
        let oid2 = GitOid::try_from_str(&"1".repeat(40)).unwrap();
        let oid3 = GitOid::try_from_str(&"2".repeat(40)).unwrap();
        let set: HashSet<_> = [oid1.clone(), oid2.clone(), oid3.clone()]
            .into_iter()
            .collect();
        assert_eq!(set.len(), 2);
    }

    #[test]
    fn test_git_oid_as_ref() {
        let oid = GitOid::try_from_str(&"9".repeat(40)).unwrap();
        let s: &str = oid.as_ref();
        assert_eq!(s.len(), 40);
    }

    #[test]
    fn test_git_oid_error_display() {
        let err = GitOidParseError {
            value: "abc".to_string(),
            reason: GitOidParseErrorReason::WrongLength {
                expected: 40,
                actual: 3,
            },
        };
        let display = format!("{err}");
        assert!(display.contains("abc"));
        assert!(display.contains("40"));
        assert!(display.contains("3"));
    }

    #[test]
    fn non_empty_string_accepts_text() {
        let text = NonEmptyString::try_from_str("Valid Title").unwrap();
        assert_eq!(text.as_str(), "Valid Title");
    }

    #[test]
    fn non_empty_string_rejects_empty() {
        let result = NonEmptyString::try_from_str("");
        assert!(result.is_err());
    }

    #[test]
    fn non_empty_string_rejects_whitespace() {
        let result = NonEmptyString::try_from_str("   ");
        assert!(result.is_err());
    }

    #[test]
    fn https_url_rejects_http() {
        assert!(matches!(
            HttpsUrl::try_from_str("http://example.com"),
            Err(HttpsUrlParseError::NotHttps)
        ));
    }

    #[test]
    fn https_url_rejects_empty() {
        assert!(matches!(
            HttpsUrl::try_from_str("   "),
            Err(HttpsUrlParseError::Empty)
        ));
    }

    #[test]
    fn remote_name_rejects_empty() {
        assert!(matches!(
            RemoteName::try_from_str(""),
            Err(RemoteNameParseError::Empty)
        ));
    }

    #[test]
    fn push_branch_rejects_head() {
        assert!(matches!(
            PushBranch::try_from_str("HEAD"),
            Err(PushBranchParseError::IsHead)
        ));
    }
}
