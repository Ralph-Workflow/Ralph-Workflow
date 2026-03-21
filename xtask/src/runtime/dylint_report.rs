//! Runtime module for dylint report generation.
//!
//! Handles process execution and output parsing.

use std::collections::BTreeMap;
use std::path::Path;
use std::process::Command;

pub fn run_dylint_capture(repo_root: &Path) -> std::io::Result<String> {
    let existing = std::env::var("RUSTFLAGS").unwrap_or_default();
    let rustflags = format!("{} --cap-lints warn", existing).trim().to_string();

    let output = Command::new("make")
        .arg("dylint")
        .current_dir(repo_root)
        .env("CARGO_TERM_COLOR", "never")
        .env("RUSTFLAGS", rustflags)
        .output()?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let combined = format!("{}{}", stdout, stderr);

    if !output.status.success() {
        return Err(std::io::Error::other(
            "dylint failed to compile - compilation errors must be fixed first",
        ));
    }

    Ok(combined)
}

/// Parse dylint output and organize by module.
///
/// Extracts errors and groups them by the top-level module in ralph-workflow/src/.
pub fn parse_dylint_output(output: &str) -> BTreeMap<String, Vec<String>> {
    let mut errors_by_module: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut current_error = String::new();
    let mut in_error = false;

    for line in output.lines() {
        // Error/warning starts with these patterns (note: no brackets for dylint)
        if line.starts_with("error:") || line.starts_with("warning:") {
            // Save previous error if exists
            if in_error && !current_error.is_empty() {
                if let Some(module) = extract_module(&current_error) {
                    errors_by_module
                        .entry(module)
                        .or_default()
                        .push(current_error.trim().to_string());
                }
            }

            // Start new error
            current_error = String::from(line);
            current_error.push('\n');
            in_error = true;
        } else if in_error {
            current_error.push_str(line);
            current_error.push('\n');

            // End of error block is an empty line
            if line.trim().is_empty() {
                if !current_error.trim().is_empty() {
                    if let Some(module) = extract_module(&current_error) {
                        errors_by_module
                            .entry(module)
                            .or_default()
                            .push(current_error.trim().to_string());
                    }
                }
                current_error.clear();
                in_error = false;
            }
        }
    }

    // Save last error if exists
    if in_error && !current_error.trim().is_empty() {
        if let Some(module) = extract_module(&current_error) {
            errors_by_module
                .entry(module)
                .or_default()
                .push(current_error.trim().to_string());
        }
    }

    errors_by_module
}

/// Extract module name from error message.
///
/// Looks for patterns like:
///   --> ralph-workflow/src/MODULE/...
///   --> ralph-workflow/src/MODULE.rs
fn extract_module(error: &str) -> Option<String> {
    for line in error.lines() {
        // Look for file path markers
        if line.contains("-->") || line.contains("ralph-workflow/src/") {
            if let Some(path_start) = line.find("ralph-workflow/src/") {
                let path = &line[path_start + 19..]; // Skip "ralph-workflow/src/"

                // Extract module name (first path component)
                let module = path
                    .split('/')
                    .next()
                    .unwrap_or(path)
                    .split(':')
                    .next()
                    .unwrap_or("")
                    .trim_end_matches(".rs")
                    .to_string();

                if !module.is_empty() && module != "lib" && module != "main" {
                    return Some(module);
                }
            }
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_module_from_path() {
        let error = r#"error: `let mut` bindings are forbidden
  --> ralph-workflow/src/agents/selector.rs:123:9"#;

        assert_eq!(extract_module(error), Some("agents".to_string()));
    }

    #[test]
    fn test_extract_module_from_nested_path() {
        let error = r#"error: imperative loops forbidden
  --> ralph-workflow/src/config/loader/parser.rs:45:5"#;

        assert_eq!(extract_module(error), Some("config".to_string()));
    }

    #[test]
    fn test_extract_module_from_top_level_file() {
        let error = r#"warning: terminal output forbidden
  --> ralph-workflow/src/banner.rs:10:5"#;

        assert_eq!(extract_module(error), Some("banner".to_string()));
    }
}
