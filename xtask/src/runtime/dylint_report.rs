//! Runtime module for dylint report generation.
//!
//! Handles process execution and output parsing.

use std::collections::BTreeMap;
use std::path::Path;
use std::process::Command;

pub fn output_contains_lint_violations(output: &str) -> bool {
    !output_contains_compilation_failure(output)
        && (output.contains("file_too_long")
            || output.contains("mutable_state_machine")
            || output.contains("imperative_loop"))
}

fn output_contains_compilation_failure(output: &str) -> bool {
    output.contains("error[E")
        || output.contains("error: could not compile")
        || output.contains("error: aborting due to")
}

pub fn run_dylint_capture(repo_root: &Path) -> std::io::Result<String> {
    let output = Command::new("make")
        .arg("dylint")
        .current_dir(repo_root)
        .env("CARGO_TERM_COLOR", "never")
        .output()?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let combined = format!("{}{}", stdout, stderr);

    if !output.status.success() && !output_contains_lint_violations(&combined) {
        return Err(std::io::Error::new(
            std::io::ErrorKind::Other,
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
    fn generic_error_colon_alone_is_not_a_lint_violation() {
        let output = "error: could not compile `ralph-workflow` due to previous errors";
        assert!(!output_contains_lint_violations(output));
    }

    #[test]
    fn aborting_error_is_not_a_lint_violation() {
        let output = "error: aborting due to 3 previous errors\n\nFor more information about this error, try `rustc --explain E0308`.";
        assert!(!output_contains_lint_violations(output));
    }

    #[test]
    fn generic_warning_alone_is_not_a_lint_violation() {
        let output = "warning: unused import: `std::fmt`\n  --> ralph-workflow/src/foo.rs:3:5";
        assert!(!output_contains_lint_violations(output));
    }

    #[test]
    fn empty_output_is_not_a_lint_violation() {
        assert!(!output_contains_lint_violations(""));
    }

    #[test]
    fn file_too_long_is_a_lint_violation() {
        let output =
            "error: file_too_long: file exceeds 500 lines\n  --> ralph-workflow/src/app/mod.rs:1:1";
        assert!(output_contains_lint_violations(output));
    }

    #[test]
    fn mutable_state_machine_is_a_lint_violation() {
        let output = "error: mutable_state_machine: `let mut` bindings are forbidden\n  --> ralph-workflow/src/reducer/mod.rs:42:5";
        assert!(output_contains_lint_violations(output));
    }

    #[test]
    fn imperative_loop_is_a_lint_violation() {
        let output = "error: imperative_loop: for-loops are forbidden in pure modules\n  --> ralph-workflow/src/pipeline/stage.rs:99:9";
        assert!(output_contains_lint_violations(output));
    }

    #[test]
    fn lint_violation_alongside_compiler_errors_is_not_a_valid_lint_only_result() {
        let output = "error[E0432]: unresolved import `crate::runtime::event_loop`\n  --> ralph-workflow/src/app/event_loop/mod.rs:7:25\n\nerror: file_too_long: file exceeds 500 lines\n  --> ralph-workflow/src/app/mod.rs:1:1\n\nerror: could not compile `ralph-workflow` (lib) due to 352 previous errors";
        assert!(!output_contains_lint_violations(output));
    }

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
