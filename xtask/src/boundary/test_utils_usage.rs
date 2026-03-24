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

    // Phase 1: collect every pub item declared under #[cfg(feature = "test-utils")]
    let src_files = match collect_rs_files(&src_dir) {
        Ok(f) => f,
        Err(e) => {
            return error(format!(
                "Failed to walk ralph-workflow/src during test-utils scan: {e}"
            ))
        }
    };

    let mut items: Vec<TestUtilsItem> = Vec::new();
    let mut read_errors: Vec<String> = Vec::new();

    for file_path in &src_files {
        match std::fs::read_to_string(file_path) {
            Ok(content) => collect_test_utils_items(file_path, &content, &mut items),
            Err(e) => read_errors.push(format!("{}: {e}", file_path.display())),
        }
    }

    if !read_errors.is_empty() {
        return error(format!(
            "Failed to read {} file(s) while scanning for test-utils items:\n{}",
            read_errors.len(),
            read_errors.join("\n")
        ));
    }

    if items.is_empty() {
        return pass();
    }

    // Phase 2: gather all content from test-side directories
    let test_dirs = [repo_root.join("tests"), repo_root.join("test-helpers/src")];

    let mut test_content = String::new();
    for test_dir in &test_dirs {
        if !test_dir.exists() {
            continue;
        }
        let files = match collect_rs_files(test_dir) {
            Ok(f) => f,
            Err(e) => {
                return error(format!(
                    "Failed to walk {} during test-utils usage scan: {e}",
                    test_dir.display()
                ))
            }
        };
        for fp in &files {
            if let Ok(c) = std::fs::read_to_string(fp) {
                test_content.push_str(&c);
            }
        }
    }

    // Phase 3: any item not referenced in test code is dead code
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

struct TestUtilsItem {
    name: String,
    file: PathBuf,
    line: usize,
}

/// Scans `content` for `#[cfg(feature = "test-utils")]` annotations and
/// records the name of the `pub` item that immediately follows each one.
fn collect_test_utils_items(file_path: &Path, content: &str, out: &mut Vec<TestUtilsItem>) {
    let lines: Vec<&str> = content.lines().collect();
    let mut i = 0;
    while i < lines.len() {
        if lines[i].trim().contains("#[cfg(feature = \"test-utils\")]") {
            // Skip blank lines, comments, and further attributes between the
            // cfg annotation and the actual item declaration.
            let mut j = i + 1;
            while j < lines.len() {
                let next = lines[j].trim();
                if next.is_empty() || next.starts_with("//") || next.starts_with("#[") {
                    j += 1;
                    continue;
                }
                if let Some(name) = extract_pub_item_name(next) {
                    out.push(TestUtilsItem {
                        name,
                        file: file_path.to_owned(),
                        line: j + 1,
                    });
                }
                break;
            }
        }
        i += 1;
    }
}

/// Extracts the identifier from a `pub` item declaration line.
///
/// Handles `pub fn`, `pub struct`, `pub enum`, `pub type`, `pub const`,
/// `pub trait`, `pub mod`, and their `pub(crate)` equivalents.
fn extract_pub_item_name(line: &str) -> Option<String> {
    let rest = line
        .strip_prefix("pub(crate)")
        .or_else(|| line.strip_prefix("pub"))?;
    let rest = rest.trim_start();

    let ident_start = rest
        .strip_prefix("fn ")
        .or_else(|| rest.strip_prefix("struct "))
        .or_else(|| rest.strip_prefix("enum "))
        .or_else(|| rest.strip_prefix("type "))
        .or_else(|| rest.strip_prefix("const "))
        .or_else(|| rest.strip_prefix("trait "))
        .or_else(|| rest.strip_prefix("mod "))?;

    let name: String = ident_start
        .chars()
        .take_while(|c| c.is_alphanumeric() || *c == '_')
        .collect();

    if name.is_empty() {
        None
    } else {
        Some(name)
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

    #[test]
    fn extract_pub_item_name_handles_common_kinds() {
        assert_eq!(
            extract_pub_item_name("pub fn make_fixture() {}"),
            Some("make_fixture".into())
        );
        assert_eq!(
            extract_pub_item_name("pub struct FakeEnv;"),
            Some("FakeEnv".into())
        );
        assert_eq!(
            extract_pub_item_name("pub(crate) mod helpers {"),
            Some("helpers".into())
        );
        assert_eq!(extract_pub_item_name("fn private_fn() {}"), None);
        assert_eq!(extract_pub_item_name("// comment"), None);
    }
}
