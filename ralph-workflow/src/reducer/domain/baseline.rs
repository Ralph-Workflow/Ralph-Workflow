#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BaselineOid(String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BaselineOidParseError {
    Empty,
}

impl BaselineOid {
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

pub fn parse_baseline_oid(raw: &str) -> Result<BaselineOid, BaselineOidParseError> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Err(BaselineOidParseError::Empty);
    }

    Ok(BaselineOid(trimmed.to_string()))
}

#[cfg(test)]
mod tests {
    use super::{parse_baseline_oid, BaselineOidParseError};

    #[test]
    fn rejects_empty_values() {
        assert_eq!(
            parse_baseline_oid("").unwrap_err(),
            BaselineOidParseError::Empty
        );
    }

    #[test]
    fn rejects_whitespace_only_values() {
        assert_eq!(
            parse_baseline_oid("   \n").unwrap_err(),
            BaselineOidParseError::Empty
        );
    }

    #[test]
    fn accepts_non_empty_values() {
        let result = parse_baseline_oid("abc123").expect("should accept non-empty baseline");
        assert_eq!(result.as_str(), "abc123");
    }

    #[test]
    fn trims_values_before_returning() {
        let result = parse_baseline_oid("  def456  ").expect("should trim whitespace");
        assert_eq!(result.as_str(), "def456");
    }
}

#[cfg(test)]
mod proptest_parsers {
    use super::parse_baseline_oid;
    use proptest::prelude::*;

    proptest! {
        /// `parse_baseline_oid` must never panic on any string input.
        #[test]
        fn parse_baseline_oid_never_panics(s in ".*") {
            let _ = parse_baseline_oid(&s);
        }

        /// A non-whitespace-only string always succeeds and the result trims surrounding space.
        #[test]
        fn parse_baseline_oid_nonempty_succeeds(
            core in "[a-zA-Z0-9]{1,40}",
            prefix in "[ \t]*",
            suffix in "[ \t]*",
        ) {
            let raw = format!("{prefix}{core}{suffix}");
            let result = parse_baseline_oid(&raw);
            prop_assert!(result.is_ok());
            let oid = result.unwrap();
            prop_assert_eq!(oid.as_str(), core.as_str());
        }

        /// A whitespace-only string always fails.
        #[test]
        fn parse_baseline_oid_whitespace_only_fails(s in "[ \t\n\r]*") {
            prop_assert!(parse_baseline_oid(&s).is_err());
        }
    }
}
