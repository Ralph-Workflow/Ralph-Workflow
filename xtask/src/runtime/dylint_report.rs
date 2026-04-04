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
    collect_error_blocks(output)
        .into_iter()
        .filter_map(map_block_to_entry)
        .fold(BTreeMap::new(), |mut errors, (module, message)| {
            errors.entry(module).or_default().push(message);
            errors
        })
}

fn collect_error_blocks(output: &str) -> Vec<String> {
    let mut blocks = Vec::new();
    let mut current = None;

    for line in output.lines() {
        if is_error_start(line) {
            finish_block(&mut current, &mut blocks);
            start_block(&mut current);
        }

        if current.is_none() {
            continue;
        }

        append_line(&mut current, line);

        if line.trim().is_empty() {
            finish_block(&mut current, &mut blocks);
        }
    }

    finish_block(&mut current, &mut blocks);

    blocks
}

fn start_block(current: &mut Option<String>) {
    *current = Some(String::new());
}

fn append_line(current: &mut Option<String>, line: &str) {
    if let Some(buffer) = current {
        buffer.push_str(line);
        buffer.push('\n');
    }
}

fn finish_block(current: &mut Option<String>, blocks: &mut Vec<String>) {
    if let Some(block) = current.take() {
        if !block.trim().is_empty() {
            blocks.push(block);
        }
    }
}

fn map_block_to_entry(block: String) -> Option<(String, String)> {
    let trimmed = block.trim();
    if trimmed.is_empty() {
        return None;
    }

    extract_module(trimmed).map(|module| (module, trimmed.to_string()))
}

fn is_error_start(line: &str) -> bool {
    let trimmed = line.trim_start();
    trimmed.starts_with("error:") || trimmed.starts_with("warning:")
}

/// Extract module name from error message.
///
/// Looks for patterns like:
///   --> ralph-workflow/src/MODULE/...
///   --> ralph-workflow/src/MODULE.rs
fn extract_module(error: &str) -> Option<String> {
    error
        .lines()
        .filter_map(|line| line.split("ralph-workflow/src/").nth(1))
        .filter_map(|path| path.split(&['/', ':']).next())
        .map(|module| module.trim_end_matches(".rs"))
        .find(|module| !module.is_empty() && module != &"lib" && module != &"main")
        .map(str::to_string)
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

    #[test]
    fn test_parse_handles_indented_error_start() {
        let output =
            " error: `let mut` bindings are forbidden\n  --> ralph-workflow/src/banner.rs:10:5\n\n";

        let parsed = parse_dylint_output(output);

        let errors = parsed
            .get("banner")
            .expect("should map the banner module when the error line is indented");

        assert_eq!(errors.len(), 1);
        assert!(errors[0].contains("error: `let mut` bindings are forbidden"));
    }
}
