//! Checks that every item gated behind `#[cfg(feature = "test-utils")]` in
//! `ralph-workflow/src/` is actually referenced somewhere in test code
//! (`tests/` or `test-helpers/src/`).
//!
//! Dead test-utils items violate the dead code policy:
//! see CLAUDE.md § No Dead Code and `docs/agents/verification.md`.
//!
//! ## Why this check exists
//!
//! `pub` items in public modules are never flagged by `dead_code` because the
//! Rust compiler assumes they are part of the external API.  Items that only
//! tests use must therefore live behind the `test-utils` feature flag —
//! `clippy-core-lib-only` (which runs with default features, no `--tests`)
//! then excludes them from the production build.  This check is the second
//! half of that contract: it verifies that each `test-utils`-gated item is
//! actually consumed by test code.  An item that exists only behind
//! `test-utils` but is never imported or called by any test is dead code
//! by definition.

use std::path::{Path, PathBuf};

use crate::domain::test_utils_scan::TestUtilsItem;
use crate::runtime::verify::{CheckStatus, NativeCheckResult};

/// Scans `ralph-workflow/src/` for items declared under
/// `#[cfg(feature = "test-utils")]` and verifies that each one is referenced
/// in at least one file under `tests/` or `test-helpers/src/`.
///
/// Returns `Pass` when every item has at least one test-side reference, or
/// when `ralph-workflow/src/` does not exist (e.g. in unit-test environments
/// with a fake repo path).
///
/// Returns `Error` with a per-item diagnostic listing the file and line of
/// each unused `test-utils` item, referencing the dead code policy.
pub fn check_test_utils_items_used_in_tests(repo_root: &Path) -> NativeCheckResult {
    let src_dir = repo_root.join("ralph-workflow/src");
    if !src_dir.exists() {
        return pass();
    }
    run_test_utils_scan(src_dir, repo_root).unwrap_or_else(|e| e)
}

fn run_test_utils_scan(
    src_dir: PathBuf,
    repo_root: &Path,
) -> Result<NativeCheckResult, NativeCheckResult> {
    let items = collect_items_from_dir(&src_dir)?;
    if items.is_empty() {
        return Ok(pass());
    }
    let test_dirs = [repo_root.join("tests"), repo_root.join("test-helpers/src")];
    let content = gather_test_content(&test_dirs)?;
    Ok(unused_items_result(&items, &content))
}

fn collect_items_from_dir(src_dir: &Path) -> Result<Vec<TestUtilsItem>, NativeCheckResult> {
    let src_files = collect_rs_files(src_dir).map_err(|e| {
        error(format!(
            "Failed to walk ralph-workflow/src during test-utils scan: {e}"
        ))
    })?;
    read_and_collect_items(&src_files)
}

fn read_and_collect_items(src_files: &[PathBuf]) -> Result<Vec<TestUtilsItem>, NativeCheckResult> {
    let (successes, failures): (Vec<_>, Vec<_>) = src_files
        .iter()
        .map(|fp| {
            std::fs::read_to_string(fp)
                .map(|content| {
                    crate::domain::test_utils_scan::collect_test_utils_items_in_file(fp, &content)
                })
                .map_err(|e| format!("{}: {e}", fp.display()))
        })
        .partition(Result::is_ok);
    if failures.is_empty() {
        Ok(successes
            .into_iter()
            .flat_map(|r| r.unwrap_or_default())
            .collect())
    } else {
        let msgs: Vec<String> = failures.into_iter().filter_map(|r| r.err()).collect();
        Err(error(format!(
            "Failed to read {} file(s) while scanning for test-utils items:\n{}",
            msgs.len(),
            msgs.join("\n")
        )))
    }
}

fn gather_test_content(test_dirs: &[PathBuf]) -> Result<String, NativeCheckResult> {
    test_dirs
        .iter()
        .filter(|d| d.exists())
        .try_fold(String::new(), |content, test_dir| {
            append_dir_content(content, test_dir)
        })
}

fn append_dir_content(content: String, test_dir: &Path) -> Result<String, NativeCheckResult> {
    let files = collect_rs_files(test_dir).map_err(|e| {
        error(format!(
            "Failed to walk {} during test-utils usage scan: {e}",
            test_dir.display()
        ))
    })?;
    let added: String = files
        .iter()
        .filter_map(|fp| std::fs::read_to_string(fp).ok())
        .collect();
    Ok(content + &added)
}

fn unused_items_result(items: &[TestUtilsItem], test_content: &str) -> NativeCheckResult {
    let unused: Vec<String> = items
        .iter()
        .filter(|item| !test_content.contains(item.name.as_str()))
        .map(|item| format!("  {}:{} — `{}`", item.file.display(), item.line, item.name))
        .collect();

    if unused.is_empty() {
        pass()
    } else {
        error(format!(
            "Found {} `#[cfg(feature = \"test-utils\")]` item(s) with no reference in test code.\n\
             Dead test-utils items violate the dead code policy — remove them or move to test-helpers/.\n\
             Policy: CLAUDE.md § No Dead Code, docs/agents/verification.md.\n\n\
             Unused items:\n{}",
            unused.len(),
            unused.join("\n")
        ))
    }
}

fn collect_rs_files(dir: &Path) -> std::io::Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    crate::io::scanner::collect_files_with_glob(dir, "*.rs", &mut files)?;
    files.sort();
    Ok(files)
}

fn pass() -> NativeCheckResult {
    NativeCheckResult {
        status: CheckStatus::Pass,
        message: String::new(),
    }
}

fn error(message: String) -> NativeCheckResult {
    NativeCheckResult {
        status: CheckStatus::Error,
        message,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn make_temp_dir(name: &str) -> PathBuf {
        let base = std::env::temp_dir().join(format!("xtask-test-utils-usage-{name}"));
        let _ = fs::remove_dir_all(&base);
        fs::create_dir_all(&base).unwrap();
        base
    }

    fn write(dir: &Path, rel: &str, content: &str) {
        let full = dir.join(rel);
        if let Some(parent) = full.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(full, content).unwrap();
    }

    #[test]
    fn passes_when_src_dir_absent() {
        let dir = make_temp_dir("absent");
        let result = check_test_utils_items_used_in_tests(&dir);
        assert_eq!(result.status, CheckStatus::Pass);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn passes_when_no_test_utils_items() {
        let dir = make_temp_dir("no-items");
        write(
            &dir,
            "ralph-workflow/src/lib.rs",
            "pub fn production_fn() {}\n",
        );
        let result = check_test_utils_items_used_in_tests(&dir);
        assert_eq!(result.status, CheckStatus::Pass);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn passes_when_item_used_in_tests_dir() {
        let dir = make_temp_dir("used-in-tests");
        write(
            &dir,
            "ralph-workflow/src/helpers.rs",
            "#[cfg(feature = \"test-utils\")]\npub fn make_fixture() {}\n",
        );
        write(
            &dir,
            "tests/integration_tests/foo.rs",
            "use helpers::make_fixture;\n",
        );
        let result = check_test_utils_items_used_in_tests(&dir);
        assert_eq!(result.status, CheckStatus::Pass);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn passes_when_item_used_in_test_helpers() {
        let dir = make_temp_dir("used-in-test-helpers");
        write(
            &dir,
            "ralph-workflow/src/helpers.rs",
            "#[cfg(feature = \"test-utils\")]\npub struct FakeEnv;\n",
        );
        write(
            &dir,
            "test-helpers/src/lib.rs",
            "use ralph_workflow::helpers::FakeEnv;\n",
        );
        let result = check_test_utils_items_used_in_tests(&dir);
        assert_eq!(result.status, CheckStatus::Pass);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn errors_when_item_unused_in_tests() {
        let dir = make_temp_dir("unused");
        write(
            &dir,
            "ralph-workflow/src/helpers.rs",
            "#[cfg(feature = \"test-utils\")]\npub fn orphan_helper() {}\n",
        );
        write(&dir, "tests/integration_tests/foo.rs", "// no usage here\n");
        let result = check_test_utils_items_used_in_tests(&dir);
        assert_eq!(result.status, CheckStatus::Error);
        assert!(
            result.message.contains("orphan_helper"),
            "error must name the unused item: {}",
            result.message
        );
        assert!(
            result.message.contains("dead code policy"),
            "error must reference the dead code policy: {}",
            result.message
        );
        assert!(
            result.message.contains("CLAUDE.md"),
            "error must cite CLAUDE.md: {}",
            result.message
        );
        let _ = fs::remove_dir_all(&dir);
    }
}
