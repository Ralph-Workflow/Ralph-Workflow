//! Pure text-parsing helpers for the test-utils dead-code check.
//!
//! These functions are called by `crate::boundary::test_utils_usage` after it has
//! already read file contents from disk.  No I/O happens here.

use std::path::{Path, PathBuf};

/// A `pub` item declared under `#[cfg(feature = "test-utils")]`.
pub struct TestUtilsItem {
    pub name: String,
    pub file: PathBuf,
    pub line: usize,
}

/// Scans `content` for `#[cfg(feature = "test-utils")]` annotations and
/// returns all immediately-following `pub` items.
pub fn collect_test_utils_items_in_file(file_path: &Path, content: &str) -> Vec<TestUtilsItem> {
    let lines: Vec<&str> = content.lines().collect();
    lines
        .iter()
        .enumerate()
        .filter(|(_, line)| line.trim().contains("#[cfg(feature = \"test-utils\")]"))
        .filter_map(|(i, _)| find_next_pub_item(file_path, &lines, i))
        .collect()
}

fn find_next_pub_item(file_path: &Path, lines: &[&str], cfg_line: usize) -> Option<TestUtilsItem> {
    lines
        .get(cfg_line + 1..)?
        .iter()
        .enumerate()
        .find(|(_, line)| {
            let t = line.trim();
            !t.is_empty() && !t.starts_with("//") && !t.starts_with("#[")
        })
        .and_then(|(offset, line)| {
            extract_pub_item_name(line.trim()).map(|name| TestUtilsItem {
                name,
                file: file_path.to_owned(),
                line: cfg_line + offset + 2,
            })
        })
}

/// Extracts the identifier from a `pub` item declaration line.
///
/// Handles `pub fn`, `pub struct`, `pub enum`, `pub type`, `pub const`,
/// `pub trait`, `pub mod`, and their `pub(crate)` equivalents.
pub fn extract_pub_item_name(line: &str) -> Option<String> {
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

#[cfg(test)]
mod tests {
    use super::*;

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
