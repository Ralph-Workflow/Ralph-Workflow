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
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
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

/// Git object ID (40-character hex SHA-1 commit hash).
///
/// Validated to contain only hexadecimal characters and to be exactly 40 characters long.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct GitOid(String);

impl GitOid {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for GitOid {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl AsRef<str> for GitOid {
    fn as_ref(&self) -> &str {
        &self.0
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

    #[must_use]
    pub fn as_ref(&self) -> &str {
        &self.0
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
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
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
    const LENGTH: usize = 64;

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
        let mut invalid = "a".repeat(39).to_string();
        invalid.push('g');
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
        let hex_oid = "0123456789abcdef".repeat(2) + "0123456789abcdef".repeat(1);
        let oid = GitOid::try_from_str(&hex_oid).unwrap();
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
        let mut set = HashSet::new();
        set.insert(oid1.clone());
        set.insert(oid2.clone());
        set.insert(oid3.clone());
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
}
