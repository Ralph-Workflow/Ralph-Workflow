//! Issue Severity Types
//!
//! Defines severity levels for review issues.

/// Issue severity levels
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub(super) enum IssueSeverity {
    Critical,
    High,
    Medium,
    Low,
}

impl IssueSeverity {
    /// Parse severity from a string
    pub(super) fn from_str(s: &str) -> Option<Self> {
        let lower = s.to_lowercase();
        if lower.contains("critical") {
            Some(Self::Critical)
        } else if lower.contains("high") {
            Some(Self::High)
        } else if lower.contains("medium") {
            Some(Self::Medium)
        } else if lower.contains("low") {
            Some(Self::Low)
        } else {
            None
        }
    }
}

impl std::fmt::Display for IssueSeverity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Critical => write!(f, "Critical"),
            Self::High => write!(f, "High"),
            Self::Medium => write!(f, "Medium"),
            Self::Low => write!(f, "Low"),
        }
    }
}
