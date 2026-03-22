#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PushRefspec(String);

#[derive(Debug, PartialEq, Eq)]
pub enum BranchParseError {
    Empty,
    StartsWithDash,
    ContainsColon,
    ContainsDisallowedCharacters,
    EmptyRefsHeadsSuffix,
    UnsupportedRefNamespace,
}

pub fn parse_head_push_refspec(branch: &str) -> Result<PushRefspec, BranchParseError> {
    let trimmed = branch.trim();
    if trimmed.is_empty() {
        return Err(BranchParseError::Empty);
    }

    if trimmed.starts_with('-') {
        return Err(BranchParseError::StartsWithDash);
    }

    if trimmed.contains(':') {
        return Err(BranchParseError::ContainsColon);
    }

    if trimmed.chars().any(|c| c.is_whitespace() || c == '\0') {
        return Err(BranchParseError::ContainsDisallowedCharacters);
    }

    let full_ref = if let Some(rest) = trimmed.strip_prefix("refs/heads/") {
        if rest.is_empty() {
            return Err(BranchParseError::EmptyRefsHeadsSuffix);
        }
        trimmed.to_string()
    } else if trimmed.starts_with("refs/") {
        return Err(BranchParseError::UnsupportedRefNamespace);
    } else {
        format!("refs/heads/{trimmed}")
    };

    Ok(PushRefspec(format!("HEAD:{full_ref}")))
}

impl PushRefspec {
    pub fn into_string(self) -> String {
        self.0
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[cfg(test)]
mod tests {
    use super::{parse_head_push_refspec, BranchParseError};

    #[test]
    fn rejects_empty_branch_name() {
        assert_eq!(
            parse_head_push_refspec("").unwrap_err(),
            BranchParseError::Empty
        );
    }

    #[test]
    fn rejects_dash_prefixed_branch() {
        assert_eq!(
            parse_head_push_refspec("-feature").unwrap_err(),
            BranchParseError::StartsWithDash
        );
    }

    #[test]
    fn rejects_colon_in_branch_name() {
        assert_eq!(
            parse_head_push_refspec("feature:alpha").unwrap_err(),
            BranchParseError::ContainsColon
        );
    }

    #[test]
    fn rejects_whitespace_in_branch_name() {
        assert_eq!(
            parse_head_push_refspec("has space").unwrap_err(),
            BranchParseError::ContainsDisallowedCharacters
        );
    }

    #[test]
    fn rejects_null_character_in_branch_name() {
        assert_eq!(
            parse_head_push_refspec("\0main").unwrap_err(),
            BranchParseError::ContainsDisallowedCharacters
        );
    }

    #[test]
    fn rejects_empty_refs_heads_suffix() {
        assert_eq!(
            parse_head_push_refspec("refs/heads/").unwrap_err(),
            BranchParseError::EmptyRefsHeadsSuffix
        );
    }

    #[test]
    fn rejects_other_refs_namespace() {
        assert_eq!(
            parse_head_push_refspec("refs/tags/v1").unwrap_err(),
            BranchParseError::UnsupportedRefNamespace
        );
    }

    #[test]
    fn accepts_simple_branch_name() {
        assert_eq!(
            parse_head_push_refspec("main").unwrap().as_str(),
            "HEAD:refs/heads/main"
        );
    }

    #[test]
    fn accepts_refs_heads_input() {
        assert_eq!(
            parse_head_push_refspec("refs/heads/feature")
                .unwrap()
                .as_str(),
            "HEAD:refs/heads/feature"
        );
    }

    #[test]
    fn trims_branch_name_before_processing() {
        assert_eq!(
            parse_head_push_refspec("  feature  ").unwrap().as_str(),
            "HEAD:refs/heads/feature"
        );
    }
}

#[cfg(test)]
mod proptest_parsers {
    use super::parse_head_push_refspec;
    use proptest::prelude::*;

    proptest! {
        /// `parse_head_push_refspec` must never panic on any string input.
        #[test]
        fn parse_head_push_refspec_never_panics(s in ".*") {
            let _ = parse_head_push_refspec(&s);
        }

        /// A simple alphanumeric branch name (no dashes, colons, spaces) always succeeds
        /// and produces a refspec starting with `HEAD:refs/heads/`.
        #[test]
        fn parse_head_push_refspec_valid_name_produces_correct_prefix(
            name in "[a-zA-Z][a-zA-Z0-9_]{0,30}",
        ) {
            let result = parse_head_push_refspec(&name);
            prop_assert!(result.is_ok());
            let refspec = result.unwrap();
            prop_assert!(refspec.as_str().starts_with("HEAD:refs/heads/"));
            prop_assert!(refspec.as_str().ends_with(&name));
        }

        /// A string with internal whitespace always returns an error.
        #[test]
        fn parse_head_push_refspec_rejects_whitespace(
            prefix in "[a-zA-Z]+",
            suffix in "[a-zA-Z]+",
        ) {
            let s = format!("{prefix} {suffix}");
            prop_assert!(parse_head_push_refspec(&s).is_err());
        }

        /// A string containing `:` always returns an error.
        #[test]
        fn parse_head_push_refspec_rejects_colon(
            prefix in "[a-zA-Z]+",
            suffix in "[a-zA-Z]+",
        ) {
            let s = format!("{prefix}:{suffix}");
            prop_assert!(parse_head_push_refspec(&s).is_err());
        }
    }
}
