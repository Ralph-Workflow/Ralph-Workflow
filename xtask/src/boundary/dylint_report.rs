//! Boundary layer for dylint report generation.
//!
//! Generates module-organized dylint reports in tmp/ directory.

use std::collections::BTreeMap;
use std::path::Path;
use std::process::ExitCode;

use crate::runtime::dylint_report as runtime;

/// Generate dylint reports organized by module.
///
/// This function:
/// 1. Runs `make dylint` to get fresh output
/// 2. Parses errors by module
/// 3. Writes sorted reports to tmp/
/// 4. Fails if dylint fails to compile
pub fn generate_dylint_report() -> ExitCode {
    let repo_root = match std::env::current_dir() {
        Ok(path) => path,
        Err(err) => {
            eprintln!("error: failed to determine current directory: {err}");
            return ExitCode::from(1);
        }
    };

    if let Err(code) = run_report_pipeline(&repo_root) {
        return code;
    }

    ExitCode::SUCCESS
}

fn run_report_pipeline(repo_root: &Path) -> Result<(), ExitCode> {
    eprintln!("Running dylint to generate module reports...");
    eprintln!("(This may take a while...)");

    let dylint_output = runtime::run_dylint_capture(repo_root).map_err(|err| {
        eprintln!("error: failed to run dylint: {err}");
        ExitCode::from(1)
    })?;

    eprintln!("Parsing dylint output...");

    let errors_by_module = runtime::parse_dylint_output(&dylint_output);
    ensure_errors_found(&errors_by_module)?;

    let tmp_dir = repo_root.join("tmp");
    ensure_tmp_dir(&tmp_dir)?;

    eprintln!("Writing reports to tmp/...");

    write_reports(&tmp_dir, &errors_by_module, &dylint_output).map_err(|err| {
        eprintln!("error: failed to write reports: {err}");
        ExitCode::from(1)
    })?;

    eprintln!("\nDylint reports generated successfully:");
    eprintln!(
        "  tmp/dylint-all.txt       - Full output ({} lines)",
        dylint_output.lines().count()
    );
    eprintln!("  tmp/dylint-summary.txt   - Summary by module");

    for (module, errors) in &errors_by_module {
        eprintln!("  tmp/dylint-{}.txt  ({:4} errors)", module, errors.len());
    }

    let total: usize = errors_by_module.values().map(|v| v.len()).sum();
    eprintln!(
        "\nTotal: {} errors across {} modules",
        total,
        errors_by_module.len()
    );

    Ok(())
}

fn ensure_errors_found(errors_by_module: &BTreeMap<String, Vec<String>>) -> Result<(), ExitCode> {
    if errors_by_module.is_empty() {
        eprintln!("error: dylint failed to compile or produced no parseable output");
        eprintln!("hint: run `make dylint` manually to see the error");
        return Err(ExitCode::from(1));
    }

    Ok(())
}

fn ensure_tmp_dir(tmp_dir: &Path) -> Result<(), ExitCode> {
    std::fs::create_dir_all(tmp_dir).map_err(|err| {
        eprintln!("error: failed to create tmp directory: {err}");
        ExitCode::from(1)
    })?;

    Ok(())
}

/// Write all reports to tmp directory.
fn write_reports(
    tmp_dir: &Path,
    errors_by_module: &BTreeMap<String, Vec<String>>,
    full_output: &str,
) -> std::io::Result<()> {
    // Write full output to both dylint-all.txt and dylint-full-current.txt
    std::fs::write(tmp_dir.join("dylint-all.txt"), full_output)?;
    std::fs::write(tmp_dir.join("dylint-full-current.txt"), full_output)?;

    // Write summary
    let summary = generate_summary(errors_by_module);
    std::fs::write(tmp_dir.join("dylint-summary.txt"), summary)?;

    // Write per-module reports
    for (module, errors) in errors_by_module {
        let report = format_module_report(module, errors);
        std::fs::write(tmp_dir.join(format!("dylint-{}.txt", module)), report)?;
    }

    Ok(())
}

/// Generate summary report.
fn generate_summary(errors_by_module: &BTreeMap<String, Vec<String>>) -> String {
    let total: usize = errors_by_module.values().map(|v| v.len()).sum();

    format!(
        "# Dylint Errors by Module\n\nTotal errors: {total}\n\n{module_lines}\n## Files Generated\n\n- `dylint-all.txt` - Complete dylint output\n{file_lines}",
        module_lines = format_module_lines(errors_by_module),
        file_lines = format_file_lines(errors_by_module),
    )
}

fn format_module_lines(errors_by_module: &BTreeMap<String, Vec<String>>) -> String {
    errors_by_module
        .iter()
        .map(|(module, errors)| format!("{:30} {:5} errors\n", module, errors.len()))
        .collect()
}

fn format_file_lines(errors_by_module: &BTreeMap<String, Vec<String>>) -> String {
    errors_by_module
        .keys()
        .map(|module| format!("- `dylint-{}.txt` - {} module errors\n", module, module))
        .collect()
}

/// Format module report.
fn format_module_report(module: &str, errors: &[String]) -> String {
    let mut report = String::new();

    report.push_str(&format!("# Dylint Errors: {}\n", module));
    report.push_str(&format!("\nTotal: {} errors\n\n", errors.len()));
    report.push_str(&"=".repeat(80));
    report.push('\n');

    for (i, error) in errors.iter().enumerate() {
        report.push('\n');
        report.push_str(&format!("## Error {} of {}\n\n", i + 1, errors.len()));
        report.push_str(error);
        report.push('\n');
        report.push_str(&"-".repeat(80));
        report.push('\n');
    }

    report
}
