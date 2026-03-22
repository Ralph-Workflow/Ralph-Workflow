//! I/O and boundary module for prompts - contains imperative parsing code.
//!
//! This module satisfies the dylint boundary-module check for code that uses
//! imperative patterns (while loops, mutable state, byte parsing).

use std::io;
use std::path::{Path, PathBuf};

pub use crate::prompts::template_registry::TemplateError;
pub use crate::prompts::template_validator::TemplateMetadata;
pub use crate::prompts::template_validator::ValidationError;
pub use crate::prompts::template_validator::VariableInfo;

pub fn get_xdg_config_home() -> Option<PathBuf> {
    std::env::var("XDG_CONFIG_HOME")
        .ok()
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var("HOME")
                .ok()
                .map(|h| PathBuf::from(h).join(".config"))
        })
}

pub fn template_exists(path: &Path) -> bool {
    path.exists()
}

#[derive(Debug, thiserror::Error)]
pub enum LoadTemplateError {
    #[error("failed to read template from {path:?}")]
    Io {
        path: PathBuf,
        #[source]
        source: io::Error,
    },
}

pub fn load_template(path: &Path) -> Result<String, LoadTemplateError> {
    std::fs::read_to_string(path).map_err(|source| LoadTemplateError::Io {
        path: path.to_path_buf(),
        source,
    })
}

pub fn validate_syntax(content: &str) -> Vec<ValidationError> {
    let bytes = content.as_bytes();
    let state = crate::prompts::template_parsing::validate_template_bytes(content, bytes);
    state
        .errors
        .into_iter()
        .map(|e| match e {
            crate::prompts::template_parsing::ValidationError::UnclosedComment { line } => {
                ValidationError::UnclosedComment { line }
            }
            crate::prompts::template_parsing::ValidationError::UnclosedConditional { line } => {
                ValidationError::UnclosedConditional { line }
            }
            crate::prompts::template_parsing::ValidationError::UnclosedLoop { line } => {
                ValidationError::UnclosedLoop { line }
            }
            crate::prompts::template_parsing::ValidationError::InvalidConditional {
                line,
                syntax,
            } => ValidationError::InvalidConditional { line, syntax },
            crate::prompts::template_parsing::ValidationError::InvalidLoop { line, syntax } => {
                ValidationError::InvalidLoop { line, syntax }
            }
        })
        .collect()
}

use crate::prompts::prompt_history_entry::PromptHistoryEntry;
use serde::{Serialize, Serializer};

impl Serialize for PromptHistoryEntry {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeStruct;
        let mut s = serializer.serialize_struct(
            "PromptHistoryEntry",
            if self.content_id.is_some() { 2 } else { 1 },
        )?;
        s.serialize_field("content", &self.content)?;
        if let Some(content_id) = &self.content_id {
            s.serialize_field("content_id", content_id)?;
        }
        s.end()
    }
}

fn parse_metadata_line(line: &str) -> Option<(Option<String>, Option<String>)> {
    crate::prompts::template_parsing::parse_metadata_line_impl(line)
}

pub fn extract_variables(content: &str) -> Vec<VariableInfo> {
    crate::prompts::template_parsing::extract_variables_impl(content)
}

pub fn extract_partials(content: &str) -> Vec<String> {
    crate::prompts::template_parsing::extract_partials_impl(content)
}

pub fn extract_metadata(content: &str) -> TemplateMetadata {
    let mut version = None;
    let mut purpose = None;

    for line in content.lines().take(50) {
        let line = line.trim();
        if !line.starts_with("{#") || !line.ends_with("#}") {
            continue;
        }

        if let Some((v, p)) = parse_metadata_line(line) {
            version = version.or(v);
            purpose = purpose.or(p);
        }
    }

    TemplateMetadata { version, purpose }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn missing_template_path() -> PathBuf {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time is after UNIX_EPOCH")
            .as_nanos();
        std::env::temp_dir().join(format!("load_template_missing_{now}"))
    }

    #[test]
    fn load_template_missing_file_returns_not_found_error() {
        let path = missing_template_path();
        assert!(!path.exists(), "generated path should not already exist");

        let err = load_template(&path).expect_err("expected missing file to return an error");
        match err {
            LoadTemplateError::Io {
                path: err_path,
                source,
            } => {
                assert_eq!(err_path, path);
                assert_eq!(source.kind(), io::ErrorKind::NotFound);
            }
        }
    }
}
