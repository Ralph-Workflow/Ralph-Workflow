//! Output formatting utilities for JSON detection and display.
//!
//! Provides functions for detecting JSON output requests in command-line
//! arguments and formatting JSON for human-readable display.

use crate::common::truncate_text;
use crate::config::Verbosity;

/// Detect if command-line arguments request JSON output.
///
/// Scans the provided argv for common JSON output flags used by various CLIs:
/// - `--json` or `--json=...`
/// - `--output-format` with json value
/// - `--format json`
/// - `-F json`
/// - `-o stream-json` or similar
#[must_use]
pub fn argv_requests_json(argv: &[String]) -> bool {
    // Skip argv[0] (the executable); scan flags/args only.
    let args: Vec<&String> = argv.iter().skip(1).collect();

    let dominated_by_output_format = args
        .iter()
        .position(|&arg| arg == "--output-format")
        .is_some_and(|i| args.get(i + 1).is_some_and(|next| next.contains("json")));

    dominated_by_output_format
        || args.iter().any(|&arg| {
            arg == "--json"
                || arg == "--format"
                || arg == "-F"
                || arg == "-o"
                || arg == "--output-format"
                || arg.starts_with("--json=")
                || arg.starts_with("--output-format=")
                || arg.starts_with("--format=")
                || arg.starts_with("-F")
                || arg.starts_with("-o")
                || (arg == "--format"
                    && args
                        .iter()
                        .position(|&a| a == arg)
                        .and_then(|i| args.get(i + 1))
                        .is_some_and(|next| *next == "json"))
                || (arg == "-F"
                    && args
                        .iter()
                        .position(|&a| a == arg)
                        .and_then(|i| args.get(i + 1))
                        .is_some_and(|next| *next == "json"))
                || (arg == "-o"
                    && args
                        .iter()
                        .position(|&a| a == arg)
                        .and_then(|i| args.get(i + 1))
                        .is_some_and(|next| next.contains("json")))
        })
}

/// Format generic JSON output for display.
///
/// Parses the input as JSON and formats it according to verbosity level:
/// - `Full` or `Debug`: Pretty-print with indentation
/// - Other levels: Compact single-line format
///
/// Output is truncated according to verbosity limits.
#[must_use]
pub fn format_generic_json_for_display(line: &str, verbosity: Verbosity) -> String {
    let Ok(value) = serde_json::from_str::<serde_json::Value>(line) else {
        return truncate_text(line, verbosity.truncate_limit("agent_msg"));
    };

    let formatted = match verbosity {
        Verbosity::Full | Verbosity::Debug => {
            serde_json::to_string_pretty(&value).unwrap_or_else(|_| line.to_string())
        }
        _ => serde_json::to_string(&value).unwrap_or_else(|_| line.to_string()),
    };
    truncate_text(&formatted, verbosity.truncate_limit("agent_msg"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_argv_requests_json_detects_common_flags() {
        assert!(argv_requests_json(&[
            "tool".to_string(),
            "--json".to_string()
        ]));
        assert!(argv_requests_json(&[
            "tool".to_string(),
            "--output-format=stream-json".to_string()
        ]));
        assert!(argv_requests_json(&[
            "tool".to_string(),
            "--output-format".to_string(),
            "stream-json".to_string()
        ]));
        assert!(argv_requests_json(&[
            "tool".to_string(),
            "--format".to_string(),
            "json".to_string()
        ]));
        assert!(argv_requests_json(&[
            "tool".to_string(),
            "-F".to_string(),
            "json".to_string()
        ]));
        assert!(argv_requests_json(&[
            "tool".to_string(),
            "-o".to_string(),
            "stream-json".to_string()
        ]));
    }

    #[test]
    fn test_format_generic_json_for_display_pretty_prints_when_full() {
        let line = r#"{"type":"message","content":{"text":"hello"}}"#;
        let formatted = format_generic_json_for_display(line, Verbosity::Full);
        assert!(formatted.contains('\n'));
        assert!(formatted.contains("\"type\""));
        assert!(formatted.contains("\"message\""));
    }
}
