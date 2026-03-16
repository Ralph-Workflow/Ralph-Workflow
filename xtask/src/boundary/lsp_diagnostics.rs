use crate::scanner::{self, LineIndex, NativeScanCheck, NativeScanViolation};
use anyhow::{anyhow, Context, Result};
use serde::Serialize;
use std::fs;
use std::io::Write;
use std::path::Path;

pub const FORBIDDEN_ALLOW_EXPECT_CHECK: &str = "forbidden-allow-expect-scan";

const FORBIDDEN_ALLOW_EXPECT_LITERALS: &[&str] = &[
    "#[allow(",
    "#![allow(",
    "#[expect(",
    "#![expect(",
    "#[cfg_attr(",
    "#![cfg_attr(",
];

pub fn emit_forbidden_allow_expect_as_cargo_json(
    repo_root: &Path,
    writer: &mut dyn Write,
) -> Result<bool> {
    let check = forbidden_allow_expect_check()?;
    let results = scanner::run_native_scan_checks_reporting(
        repo_root,
        std::slice::from_ref(check),
        &|_, _| {},
    );
    let result = results
        .into_iter()
        .next()
        .ok_or_else(|| anyhow!("native scan did not return a forbidden allow/expect result"))?;

    for violation in &result.violations {
        let message = compiler_message(repo_root, violation)?;
        serde_json::to_writer(&mut *writer, &message)?;
        writer.write_all(b"\n")?;
    }

    serde_json::to_writer(
        &mut *writer,
        &BuildFinishedMessage {
            reason: "build-finished",
            success: result.passed,
        },
    )?;
    writer.write_all(b"\n")?;

    Ok(result.passed)
}

fn forbidden_allow_expect_check() -> Result<&'static NativeScanCheck> {
    scanner::NATIVE_SCAN_CHECKS
        .iter()
        .find(|check| check.name == FORBIDDEN_ALLOW_EXPECT_CHECK)
        .ok_or_else(|| anyhow!("missing native scan definition for forbidden allow/expect"))
}

fn compiler_message(repo_root: &Path, violation: &NativeScanViolation) -> Result<CompilerMessage> {
    let file_bytes = fs::read(&violation.file)
        .with_context(|| format!("failed to read {}", violation.file.display()))?;
    let line_index = LineIndex::new(&file_bytes);
    let line_number_zero_based = violation.line_number.saturating_sub(1);
    let line_start_byte = line_index.start_of_line(line_number_zero_based);
    let (column_start, column_end) = highlight_columns(&violation.line);
    let byte_start = line_start_byte + column_start.saturating_sub(1);
    let byte_end = line_start_byte + column_end.saturating_sub(1);
    let manifest_path = repo_root.join("xtask/Cargo.toml");
    let target_src_path = repo_root.join("xtask/src/main.rs");
    let file_name = display_path(&violation.file);

    Ok(CompilerMessage {
        reason: "compiler-message",
        package_id: format!("file://{}#0.1.0", display_path(&manifest_path)),
        manifest_path: display_path(&manifest_path),
        target: CargoTarget {
            kind: vec!["bin".to_string()],
            crate_types: vec!["bin".to_string()],
            name: "xtask".to_string(),
            src_path: display_path(&target_src_path),
            edition: "2021".to_string(),
            doc: false,
            doctest: false,
            test: true,
        },
        message: RustDiagnostic {
            code: Some(DiagnosticCode {
                code: FORBIDDEN_ALLOW_EXPECT_CHECK.to_string(),
                explanation: None,
            }),
            level: "error",
            message: policy_message_for_line(&violation.line),
            spans: vec![DiagnosticSpan {
                file_name,
                byte_start,
                byte_end,
                line_start: violation.line_number,
                line_end: violation.line_number,
                column_start,
                column_end,
                is_primary: true,
                text: vec![SpanText {
                    text: violation.line.clone(),
                    highlight_start: column_start,
                    highlight_end: column_end,
                }],
                label: Some("forbidden by repo lint policy".to_string()),
                suggested_replacement: None,
                suggestion_applicability: None,
                expansion: None,
            }],
            children: Vec::new(),
            rendered: None,
        },
    })
}

fn display_path(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

fn highlight_columns(line: &str) -> (usize, usize) {
    let found = FORBIDDEN_ALLOW_EXPECT_LITERALS.iter().find_map(|literal| {
        line.find(literal)
            .map(|index| (index + 1, index + 1 + literal.len()))
    });

    found.unwrap_or_else(|| {
        let start = line.find("#").map_or(1, |index| index + 1);
        let end = (start + 1).min(line.len() + 1);
        (start, end.max(start + 1))
    })
}

fn policy_message_for_line(line: &str) -> String {
    if line.contains("allow(") {
        "#[allow(...)] is forbidden by repo lint policy; refactor the code instead".to_string()
    } else if line.contains("cfg_attr(") {
        "cfg_attr wrapping allow/expect is forbidden by repo lint policy unless it is the narrow documented #[expect(..., reason = ...)] case".to_string()
    } else {
        "#[expect(...)] is only permitted for documented external-code cases with a non-empty reason on the narrowest possible scope".to_string()
    }
}

#[derive(Serialize)]
struct CompilerMessage {
    reason: &'static str,
    package_id: String,
    manifest_path: String,
    target: CargoTarget,
    message: RustDiagnostic,
}

#[derive(Serialize)]
struct CargoTarget {
    kind: Vec<String>,
    #[serde(rename = "crate_types")]
    crate_types: Vec<String>,
    name: String,
    src_path: String,
    edition: String,
    doc: bool,
    doctest: bool,
    test: bool,
}

#[derive(Serialize)]
struct RustDiagnostic {
    code: Option<DiagnosticCode>,
    level: &'static str,
    message: String,
    spans: Vec<DiagnosticSpan>,
    children: Vec<DiagnosticChild>,
    rendered: Option<String>,
}

#[derive(Serialize)]
struct DiagnosticCode {
    code: String,
    explanation: Option<String>,
}

#[derive(Serialize)]
struct DiagnosticSpan {
    file_name: String,
    byte_start: usize,
    byte_end: usize,
    line_start: usize,
    line_end: usize,
    column_start: usize,
    column_end: usize,
    is_primary: bool,
    text: Vec<SpanText>,
    label: Option<String>,
    suggested_replacement: Option<String>,
    suggestion_applicability: Option<String>,
    expansion: Option<serde_json::Value>,
}

#[derive(Serialize)]
struct SpanText {
    text: String,
    highlight_start: usize,
    highlight_end: usize,
}

#[derive(Serialize)]
struct DiagnosticChild {
    message: String,
    level: String,
    spans: Vec<DiagnosticSpan>,
    rendered: Option<String>,
}

#[derive(Serialize)]
struct BuildFinishedMessage {
    reason: &'static str,
    success: bool,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn make_temp_repo(name: &str) -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        let dir = std::env::temp_dir().join(format!("xtask-lsp-{name}-{unique}"));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("temp repo should be creatable");
        dir
    }

    fn write_file(path: &Path, content: &str) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).expect("parent dirs should be creatable");
        }
        fs::write(path, content).expect("test file should be writable");
    }

    #[test]
    fn forbidden_allow_expect_lsp_output_reports_native_scan_violations() {
        let repo_root = make_temp_repo("violation");
        write_file(
            &repo_root.join("xtask/src/bad.rs"),
            "#[allow(clippy::dbg_macro)]\nfn sample() {}\n",
        );
        write_file(
            &repo_root.join("xtask/Cargo.toml"),
            "[package]\nname = \"xtask\"\nversion = \"0.1.0\"\n",
        );

        let mut output = Vec::new();
        let passed = emit_forbidden_allow_expect_as_cargo_json(&repo_root, &mut output)
            .expect("LSP diagnostic emission should succeed");

        let output_text = String::from_utf8(output).expect("output should be UTF-8 JSON lines");
        let json_lines: Vec<Value> = output_text
            .lines()
            .map(|line| serde_json::from_str(line).expect("each line should be valid JSON"))
            .collect();

        assert!(!passed, "violations should produce a failing LSP check");
        assert_eq!(
            json_lines.len(),
            2,
            "expected one compiler message plus build-finished"
        );
        assert_eq!(json_lines[0]["reason"], "compiler-message");
        assert_eq!(
            json_lines[0]["message"]["code"]["code"],
            FORBIDDEN_ALLOW_EXPECT_CHECK
        );
        assert_eq!(json_lines[0]["message"]["spans"][0]["line_start"], 1);
        assert!(json_lines[0]["message"]["spans"][0]["file_name"]
            .as_str()
            .is_some_and(|name| name.ends_with("xtask/src/bad.rs")));
        assert_eq!(json_lines[1]["reason"], "build-finished");
        assert_eq!(json_lines[1]["success"], false);

        let _ = fs::remove_dir_all(&repo_root);
    }

    #[test]
    fn forbidden_allow_expect_lsp_output_reports_success_when_scan_is_clean() {
        let repo_root = make_temp_repo("clean");
        write_file(
            &repo_root.join("xtask/src/good.rs"),
            "fn sample() -> usize {\n    1\n}\n",
        );
        write_file(
            &repo_root.join("xtask/Cargo.toml"),
            "[package]\nname = \"xtask\"\nversion = \"0.1.0\"\n",
        );

        let mut output = Vec::new();
        let passed = emit_forbidden_allow_expect_as_cargo_json(&repo_root, &mut output)
            .expect("clean scan should still emit build-finished JSON");

        let output_text = String::from_utf8(output).expect("output should be UTF-8 JSON lines");
        let json_lines: Vec<Value> = output_text
            .lines()
            .map(|line| serde_json::from_str(line).expect("each line should be valid JSON"))
            .collect();

        assert!(passed, "clean scan should return success");
        assert_eq!(
            json_lines.len(),
            1,
            "clean scan should emit only build-finished"
        );
        assert_eq!(json_lines[0]["reason"], "build-finished");
        assert_eq!(json_lines[0]["success"], true);

        let _ = fs::remove_dir_all(&repo_root);
    }
}
