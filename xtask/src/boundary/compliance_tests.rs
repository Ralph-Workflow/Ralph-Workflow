//! Tests for compliance checking functions.

use super::*;
use std::fs;
use std::path::{Path, PathBuf};

fn make_temp_dir(name: &str) -> PathBuf {
    let unique = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let base = std::env::temp_dir().join(format!("xtask-compliance-{name}-{unique}"));
    let _ = fs::remove_dir_all(&base);
    fs::create_dir_all(&base).unwrap();
    base
}

fn write_file(dir: &Path, path: &str, content: &str) {
    let full = dir.join(path);
    if let Some(parent) = full.parent() {
        fs::create_dir_all(parent).unwrap();
    }
    fs::write(full, content).unwrap();
}

// ── check_no_shell_scripts tests ──────────────────────────────────────────

#[test]
fn test_check_no_shell_scripts_pass_when_dirs_missing() {
    let result = check_no_shell_scripts(Path::new("/nonexistent-fake-repo-path"));
    assert_eq!(result.status, CheckStatus::Pass);
    assert!(result.message.is_empty());
}

#[test]
fn test_check_no_shell_scripts_pass_when_no_sh_files() {
    let dir = make_temp_dir("no-sh-pass");
    fs::create_dir_all(dir.join("scripts")).unwrap();
    fs::create_dir_all(dir.join("tests/integration_tests")).unwrap();
    write_file(&dir, "scripts/README.md", "# no scripts here");
    write_file(&dir, "tests/integration_tests/my_test.rs", "// rust file");

    let result = check_no_shell_scripts(&dir);
    assert_eq!(
        result.status,
        CheckStatus::Pass,
        "message: {}",
        result.message
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_check_no_shell_scripts_error_when_sh_file_found() {
    let dir = make_temp_dir("sh-found");
    fs::create_dir_all(dir.join("scripts")).unwrap();
    write_file(&dir, "scripts/migrate.sh", "#!/bin/bash\necho hello");

    let result = check_no_shell_scripts(&dir);
    assert_eq!(result.status, CheckStatus::Error);
    assert!(
        result.message.contains("migrate.sh"),
        "message must mention the file: {}",
        result.message
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_check_no_shell_scripts_error_when_sh_in_integration_tests() {
    let dir = make_temp_dir("sh-in-integration");
    let test_dir = dir.join("tests/integration_tests");
    fs::create_dir_all(&test_dir).unwrap();
    write_file(&dir, "tests/integration_tests/old_check.sh", "#!/bin/bash");

    let result = check_no_shell_scripts(&dir);
    assert_eq!(result.status, CheckStatus::Error);
    assert!(
        result.message.contains("old_check.sh"),
        "{}",
        result.message
    );

    let _ = fs::remove_dir_all(&dir);
}

#[cfg(unix)]
#[test]
fn test_check_no_shell_scripts_errors_on_unreadable_directory() {
    use std::os::unix::fs::PermissionsExt;

    let dir = make_temp_dir("no-shell-unreadable");
    let scripts_dir = dir.join("scripts");
    fs::create_dir_all(&scripts_dir).unwrap();
    write_file(&dir, "scripts/migrate.sh", "#!/bin/bash\necho hi");

    let mut perms = fs::metadata(&scripts_dir).unwrap().permissions();
    perms.set_mode(0o000);
    fs::set_permissions(&scripts_dir, perms).unwrap();

    let result = check_no_shell_scripts(&dir);

    // Restore permissions so cleanup works.
    let mut perms_restore = fs::metadata(&scripts_dir).unwrap().permissions();
    perms_restore.set_mode(0o755);
    let _ = fs::set_permissions(&scripts_dir, perms_restore);

    assert_eq!(result.status, CheckStatus::Error, "{}", result.message);
    assert!(
        result.message.contains("read_dir") || result.message.contains("Failed"),
        "message must mention directory walk error: {}",
        result.message
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_check_no_shell_scripts_pass_for_scripts_remote_sh_files() {
    // scripts/remote/ is intentionally excluded from the migration-regression scan.
    let dir = make_temp_dir("sh-in-remote");
    fs::create_dir_all(dir.join("scripts/remote")).unwrap();
    write_file(
        &dir,
        "scripts/remote/run.sh",
        "#!/usr/bin/env bash\nexec \"$@\"",
    );

    let result = check_no_shell_scripts(&dir);
    assert_eq!(
        result.status,
        CheckStatus::Pass,
        "scripts/remote/ should be excluded from scan; message: {}",
        result.message
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_check_no_shell_scripts_error_for_sh_outside_remote_subdir() {
    // A .sh file directly under scripts/ (outside remote/) is still a regression.
    let dir = make_temp_dir("sh-scripts-root");
    fs::create_dir_all(dir.join("scripts/remote")).unwrap();
    write_file(&dir, "scripts/legacy.sh", "#!/bin/bash\necho hi");
    write_file(
        &dir,
        "scripts/remote/run.sh",
        "#!/usr/bin/env bash\nexec \"$@\"",
    );

    let result = check_no_shell_scripts(&dir);
    assert_eq!(result.status, CheckStatus::Error);
    assert!(
        result.message.contains("legacy.sh"),
        "message must flag legacy.sh: {}",
        result.message
    );
    assert!(
        !result.message.contains("run.sh"),
        "message must NOT flag scripts/remote/run.sh: {}",
        result.message
    );

    let _ = fs::remove_dir_all(&dir);
}

// ── check_timeout_wrappers tests ──────────────────────────────────────────

#[test]
fn test_check_timeout_wrappers_pass_when_dir_missing() {
    let result = check_timeout_wrappers(Path::new("/nonexistent-fake-repo-path"));
    assert_eq!(result.status, CheckStatus::Pass);
    assert!(result.message.is_empty());
}

#[test]
fn test_check_timeout_wrappers_pass_when_all_tests_wrapped() {
    let dir = make_temp_dir("pass");
    let test_dir = dir.join("tests/integration_tests");
    fs::create_dir_all(&test_dir).unwrap();

    write_file(
        &dir,
        "tests/integration_tests/my_test.rs",
        r#"
#[test]
fn test_something() {
    with_default_timeout(|| {
        // test body
    });
}
"#,
    );

    let result = check_timeout_wrappers(&dir);
    assert_eq!(
        result.status,
        CheckStatus::Pass,
        "message: {}",
        result.message
    );
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_check_timeout_wrappers_warning_when_missing_wrapper() {
    let dir = make_temp_dir("warn");
    let test_dir = dir.join("tests/integration_tests");
    fs::create_dir_all(&test_dir).unwrap();

    write_file(
        &dir,
        "tests/integration_tests/bad_test.rs",
        r#"
#[test]
fn test_missing_timeout() {
    // No timeout wrapper here
    assert!(true);
}
"#,
    );

    let result = check_timeout_wrappers(&dir);
    assert_eq!(result.status, CheckStatus::Warning);
    assert!(
        result.message.contains("test_missing_timeout"),
        "message should mention the failing test: {}",
        result.message
    );
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_check_timeout_wrappers_finds_fn_after_additional_attributes() {
    let dir = make_temp_dir("warn-extra-attrs");
    let test_dir = dir.join("tests/integration_tests");
    fs::create_dir_all(&test_dir).unwrap();

    write_file(
        &dir,
        "tests/integration_tests/bad_test_attr.rs",
        r#"
#[test]
#[ignore = "https://example.com/issues/123"]
fn test_missing_timeout_with_extra_attr() {
    // No timeout wrapper here
    assert!(true);
}
"#,
    );

    let result = check_timeout_wrappers(&dir);
    assert_eq!(result.status, CheckStatus::Warning);
    assert!(
        result
            .message
            .contains("test_missing_timeout_with_extra_attr"),
        "message should mention the failing test: {}",
        result.message
    );
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_check_timeout_wrappers_skip_template_file() {
    let dir = make_temp_dir("skip-template");
    let test_dir = dir.join("tests/integration_tests");
    fs::create_dir_all(&test_dir).unwrap();

    // _TEMPLATE.rs should be skipped even if it has violations
    write_file(
        &dir,
        "tests/integration_tests/_TEMPLATE.rs",
        r#"
#[test]
fn test_template_no_timeout() {
    // Template test without timeout wrapper
    assert!(true);
}
"#,
    );

    let result = check_timeout_wrappers(&dir);
    assert_eq!(
        result.status,
        CheckStatus::Pass,
        "message: {}",
        result.message
    );
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_check_timeout_wrappers_handles_nested_module() {
    let dir = make_temp_dir("nested");

    write_file(
        &dir,
        "tests/integration_tests/submodule/mod.rs",
        r#"
#[test]
fn test_nested_missing() {
    assert!(true);
}
"#,
    );

    // mod.rs is skipped, so no violations
    let result = check_timeout_wrappers(&dir);
    assert_eq!(
        result.status,
        CheckStatus::Pass,
        "message: {}",
        result.message
    );

    write_file(
        &dir,
        "tests/integration_tests/submodule/tests.rs",
        r#"
#[test]
fn test_nested_no_timeout() {
    assert!(true);
}
"#,
    );

    let result2 = check_timeout_wrappers(&dir);
    assert_eq!(result2.status, CheckStatus::Warning);
    assert!(result2.message.contains("test_nested_no_timeout"));
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_check_timeout_wrappers_with_timeout_variant() {
    let dir = make_temp_dir("with-timeout");
    let test_dir = dir.join("tests/integration_tests");
    fs::create_dir_all(&test_dir).unwrap();

    write_file(
        &dir,
        "tests/integration_tests/slow_test.rs",
        r#"
#[test]
fn test_slow() {
    with_timeout(|| {
        // slow test body
    }, std::time::Duration::from_secs(30));
}
"#,
    );

    let result = check_timeout_wrappers(&dir);
    assert_eq!(
        result.status,
        CheckStatus::Pass,
        "message: {}",
        result.message
    );
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_check_timeout_wrappers_multiple_tests_mixed() {
    let dir = make_temp_dir("mixed");
    let test_dir = dir.join("tests/integration_tests");
    fs::create_dir_all(&test_dir).unwrap();

    write_file(
        &dir,
        "tests/integration_tests/mixed.rs",
        r#"
#[test]
fn test_ok() {
    with_default_timeout(|| {
        assert!(true);
    });
}

#[test]
fn test_missing() {
    assert!(true);
}

#[test]
fn test_also_ok() {
    with_timeout(|| {
        assert!(true);
    }, std::time::Duration::from_secs(10));
}
"#,
    );

    let result = check_timeout_wrappers(&dir);
    assert_eq!(result.status, CheckStatus::Warning);
    assert!(result.message.contains("test_missing"));
    assert!(!result.message.contains("test_ok"));
    assert!(!result.message.contains("test_also_ok"));
    let _ = fs::remove_dir_all(&dir);
}

// ── New Aho-Corasick specific tests ───────────────────────────────────────

#[test]
fn test_check_timeout_wrappers_fn_with_brace_on_same_line() {
    // fn declaration and opening brace on the same line (e.g. `fn test_foo() {`)
    let dir = make_temp_dir("brace-same-line");
    let test_dir = dir.join("tests/integration_tests");
    fs::create_dir_all(&test_dir).unwrap();

    write_file(
        &dir,
        "tests/integration_tests/inline.rs",
        "#[test]\nfn test_inline() {\n    with_default_timeout(|| assert!(true));\n}\n",
    );

    let result = check_timeout_wrappers(&dir);
    assert_eq!(
        result.status,
        CheckStatus::Pass,
        "fn with brace on same line should pass when wrapper present: {}",
        result.message
    );

    // Also check missing wrapper on same-line-brace fn.
    let dir2 = make_temp_dir("brace-same-line-missing");
    let test_dir2 = dir2.join("tests/integration_tests");
    fs::create_dir_all(&test_dir2).unwrap();

    write_file(
        &dir2,
        "tests/integration_tests/inline_missing.rs",
        "#[test]\nfn test_inline_missing() {\n    assert!(true);\n}\n",
    );

    let result2 = check_timeout_wrappers(&dir2);
    assert_eq!(result2.status, CheckStatus::Warning);
    assert!(
        result2.message.contains("test_inline_missing"),
        "{}",
        result2.message
    );

    let _ = fs::remove_dir_all(&dir);
    let _ = fs::remove_dir_all(&dir2);
}

#[test]
fn test_check_timeout_wrappers_multiple_files_mixed() {
    // Two files: one passing, one failing.
    let dir = make_temp_dir("multi-file-mixed");
    let test_dir = dir.join("tests/integration_tests");
    fs::create_dir_all(&test_dir).unwrap();

    write_file(
        &dir,
        "tests/integration_tests/good.rs",
        r#"
#[test]
fn test_good() {
    with_default_timeout(|| {
        assert!(true);
    });
}
"#,
    );

    write_file(
        &dir,
        "tests/integration_tests/bad.rs",
        r#"
#[test]
fn test_bad() {
    assert!(false);
}
"#,
    );

    let result = check_timeout_wrappers(&dir);
    assert_eq!(result.status, CheckStatus::Warning);
    assert!(
        result.message.contains("test_bad"),
        "message must mention the bad test: {}",
        result.message
    );
    assert!(
        !result.message.contains("test_good"),
        "message must not mention the good test: {}",
        result.message
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_check_timeout_wrappers_timeout_outside_body_not_counted() {
    // A timeout wrapper present in a sibling function must not satisfy
    // the constraint for a test that lacks one.
    let dir = make_temp_dir("timeout-outside-body");
    let test_dir = dir.join("tests/integration_tests");
    fs::create_dir_all(&test_dir).unwrap();

    write_file(
        &dir,
        "tests/integration_tests/sibling.rs",
        r#"
#[test]
fn test_has_timeout() {
    with_default_timeout(|| {
        assert!(true);
    });
}

#[test]
fn test_lacks_timeout() {
    assert!(true);
}
"#,
    );

    let result = check_timeout_wrappers(&dir);
    assert_eq!(result.status, CheckStatus::Warning);
    assert!(
        result.message.contains("test_lacks_timeout"),
        "test_lacks_timeout should be flagged: {}",
        result.message
    );
    assert!(
        !result.message.contains("test_has_timeout"),
        "test_has_timeout should NOT be flagged: {}",
        result.message
    );

    let _ = fs::remove_dir_all(&dir);
}

#[cfg(unix)]
#[test]
fn test_check_timeout_wrappers_reports_read_errors_separately() {
    use std::os::unix::fs::PermissionsExt;

    let dir = make_temp_dir("read-error");
    let test_dir = dir.join("tests/integration_tests");
    fs::create_dir_all(&test_dir).unwrap();

    let file_rel = "tests/integration_tests/unreadable.rs";
    write_file(
        &dir,
        file_rel,
        "#[test]\nfn test_unreadable() { assert!(true); }\n",
    );

    let file_path = dir.join(file_rel);
    let mut perms = fs::metadata(&file_path).unwrap().permissions();
    perms.set_mode(0o000);
    fs::set_permissions(&file_path, perms).unwrap();

    let result = check_timeout_wrappers(&dir);

    // Restore permissions so cleanup works.
    let mut perms_restore = fs::metadata(&file_path).unwrap().permissions();
    perms_restore.set_mode(0o644);
    let _ = fs::set_permissions(&file_path, perms_restore);

    assert_eq!(result.status, CheckStatus::Error, "{}", result.message);
    assert!(
        result.message.contains("read error"),
        "message must mention read error: {}",
        result.message
    );
    assert!(
        !result.message.contains("missing timeout wrapper"),
        "read errors must not be counted as missing wrappers: {}",
        result.message
    );

    let _ = fs::remove_dir_all(&dir);
}
