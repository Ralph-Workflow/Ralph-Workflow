/// Maximum safe prompt size in bytes before pre-truncation.
const MAX_SAFE_PROMPT_SIZE: u64 = 200_000;

/// Maximum prompt size for GLM-like agents (GLM, Zhipu, Qwen, `DeepSeek`).
const GLM_MAX_PROMPT_SIZE: u64 = 100_000;

/// Maximum prompt size for Claude-based agents.
const CLAUDE_MAX_PROMPT_SIZE: u64 = 300_000;

/// Get the maximum safe prompt size for a specific agent.
#[must_use] 
pub fn model_budget_bytes_for_agent_name(commit_agent: &str) -> u64 {
    let agent_lower = commit_agent.to_lowercase();

    if agent_lower.contains("glm")
        || agent_lower.contains("zhipuai")
        || agent_lower.contains("zai")
        || agent_lower.contains("qwen")
        || agent_lower.contains("deepseek")
    {
        GLM_MAX_PROMPT_SIZE
    } else if agent_lower.contains("claude")
        || agent_lower.contains("ccs")
        || agent_lower.contains("anthropic")
    {
        CLAUDE_MAX_PROMPT_SIZE
    } else {
        MAX_SAFE_PROMPT_SIZE
    }
}

#[must_use] 
pub fn effective_model_budget_bytes(agent_names: &[String]) -> u64 {
    agent_names
        .iter()
        .map(|name| model_budget_bytes_for_agent_name(name))
        .min()
        .unwrap_or(MAX_SAFE_PROMPT_SIZE)
}

/// Truncate diff if it's too large for agents with small context windows.
fn truncate_diff_if_large(diff: &str, max_size: usize) -> String {
    if diff.len() <= max_size {
        return diff.to_string();
    }

    let mut files: Vec<DiffFile> = Vec::new();
    let mut current_file = DiffFile::default();
    let mut in_file = false;

    for line in diff.lines() {
        if line.starts_with("diff --git ") {
            if in_file && !current_file.lines.is_empty() {
                files.push(std::mem::take(&mut current_file));
            }
            in_file = true;
            current_file.lines.push(line.to_string());

            if let Some(path) = line.split(" b/").nth(1) {
                current_file.path = path.to_string();
                current_file.priority = prioritize_file_path(path);
            }
        } else if in_file {
            current_file.lines.push(line.to_string());
        }
    }

    if in_file && !current_file.lines.is_empty() {
        files.push(current_file);
    }

    files.sort_by(|a, b| b.priority.cmp(&a.priority));

    let mut result = String::new();
    let mut current_size = 0;
    let mut files_included: usize = 0;
    let total_files = files.len();

    for file in &files {
        let file_size: usize = file.lines.iter().map(|l| l.len() + 1).sum();

        if current_size + file_size <= max_size {
            for line in &file.lines {
                result.push_str(line);
                result.push('\n');
            }
            current_size += file_size;
            files_included = files_included.saturating_add(1);
        } else if files_included == 0 {
            let truncated_lines = truncate_lines_to_fit(&file.lines, max_size);
            for line in truncated_lines {
                result.push_str(&line);
                result.push('\n');
            }
            files_included = 1;
            break;
        } else {
            break;
        }
    }

    if files_included < total_files {
        let summary = format!(
            "\n[Truncated: {files_included} of {total_files} files shown]\n"
        );
        if summary.len() <= max_size {
            if result.len() + summary.len() > max_size {
                let target_bytes = max_size.saturating_sub(summary.len());
                if target_bytes < result.len() {
                    let mut cut = 0usize;
                    for (idx, _) in result.char_indices() {
                        if idx > target_bytes {
                            break;
                        }
                        cut = idx;
                    }
                    result.truncate(cut);
                }
            }
            result.push_str(&summary);
        }
    }

    result
}

#[must_use] 
pub fn truncate_diff_to_model_budget(diff: &str, max_size_bytes: u64) -> (String, bool) {
    let max_size = usize::try_from(max_size_bytes).unwrap_or(usize::MAX);
    if diff.len() <= max_size {
        (diff.to_string(), false)
    } else {
        (truncate_diff_if_large(diff, max_size), true)
    }
}

#[derive(Default)]
struct DiffFile {
    path: String,
    priority: i32,
    lines: Vec<String>,
}

fn prioritize_file_path(path: &str) -> i32 {
    let normalized = path.replace('\\', "/");
    let parts: Vec<&str> = normalized.split('/').filter(|p| !p.is_empty()).collect();

    if parts.contains(&"src") {
        100
    } else if parts.contains(&"tests") {
        50
    } else if std::path::Path::new(&normalized)
        .extension()
        .is_some_and(|ext| ext.eq_ignore_ascii_case("md") || ext.eq_ignore_ascii_case("txt"))
    {
        10
    } else {
        0
    }
}

fn truncate_to_utf8_boundary(s: &str, max_bytes: usize) -> String {
    if s.len() <= max_bytes {
        return s.to_string();
    }
    let cut = s
        .char_indices()
        .take_while(|(idx, _)| *idx <= max_bytes)
        .last()
        .map(|(idx, _)| idx)
        .unwrap_or(0);
    s[..cut].to_string()
}

fn truncate_lines_to_fit(lines: &[String], max_size: usize) -> Vec<String> {
    // First pass: collect lines that fit within max_size
    let (result, current_size) = lines.iter().fold(
        (Vec::new(), 0usize),
        |(mut acc, size), line| {
            let line_size = line.len() + 1;
            if size + line_size <= max_size {
                acc.push(line.clone());
                (acc, size + line_size)
            } else {
                (acc, size)
            }
        },
    );

    let suffix = " [truncated...]";
    let suffix_len = suffix.len();

    if result.is_empty() {
        return result;
    }

    // Second pass: ensure suffix fits within max_size
    let adjusted = if current_size + suffix_len > max_size {
        let target_bytes = max_size.saturating_sub(suffix_len);
        // Work backwards from end of result, truncating first line if needed
        // Leave room for newline after each line (+1 per line)
        let kept: Vec<_> = result.iter().rev().fold(
            (Vec::new(), 0usize),
            |(mut acc, size), line| {
                let line_size = line.len() + 1;
                if size + line_size <= target_bytes {
                    // Can fit this line completely
                    acc.push(line.clone());
                    (acc, size + line_size)
                } else if acc.is_empty() {
                    // This is the first (last) line - truncate it to fit
                    // Leave room for newline: max allowed is target_bytes - 1
                    let max_for_line = target_bytes.saturating_sub(1);
                    let new_line = truncate_to_utf8_boundary(line, max_for_line);
                    if !new_line.is_empty() {
                        let new_size = new_line.len() + 1;
                        acc.push(new_line.clone());
                        (acc, new_size)
                    } else {
                        (acc, size)
                    }
                } else {
                    // Can't fit more, stop adding
                    (acc, size)
                }
            },
        )
        .0;
        kept
    } else {
        result
    };

    if adjusted.is_empty() {
        adjusted
    } else {
        let mut result = adjusted;
        if let Some(last) = result.last_mut() {
            last.push_str(suffix);
        }
        result
    }
}

#[cfg(test)]
mod diff_truncation_tests {
    use super::*;

    #[test]
    fn prioritize_file_path_handles_crate_prefixed_paths() {
        // Real diffs in this repo often include crate-prefixed paths like `ralph-workflow/src/...`.
        // These should still be treated as high-priority source changes.
        assert_eq!(prioritize_file_path("ralph-workflow/src/lib.rs"), 100);
        assert_eq!(prioritize_file_path("ralph-workflow/tests/integration.rs"), 50);
        assert_eq!(prioritize_file_path("README.md"), 10);
    }

    #[test]
    fn truncate_diff_to_model_budget_never_exceeds_max_size() {
        let files_included = 1;
        let total_files = 2;
        let summary = format!(
            "\n[Truncated: {files_included} of {total_files} files shown]\n"
        );

        let max_size = 1_000usize;

        // Craft a diff where:
        // - file 1 fits within max_size
        // - file 2 does not fit, so a truncation summary is appended
        // - file 1 content is sized so adding summary would exceed max_size
        let file1_header = "diff --git a/src/a.rs b/src/a.rs";
        let desired_file1_size = max_size - summary.len() + 1;
        let filler_line_len = desired_file1_size.saturating_sub(file1_header.len() + 2);
        let file1 = format!(
            "{file1_header}\n+{}\n",
            "x".repeat(filler_line_len.saturating_sub(1))
        );

        let file2 = "diff --git a/tests/b.rs b/tests/b.rs\n+small\n";
        let diff = format!("{file1}{file2}");

        let (truncated, was_truncated) = truncate_diff_to_model_budget(&diff, max_size as u64);
        assert!(was_truncated, "expected truncation when diff exceeds max size");
        assert!(
            truncated.len() <= max_size,
            "truncated diff must not exceed max_size (got {} > {})",
            truncated.len(),
            max_size
        );
    }

    #[test]
    fn truncate_lines_to_fit_reserves_space_for_truncation_suffix() {
        // Regression test: truncate_lines_to_fit() used to append " [truncated...]" after
        // selecting lines that fit max_size, which could push the final output over the
        // intended max_size budget.
        let max_size = 20usize;
        let lines = vec!["x".repeat(max_size - 1)];

        let truncated = truncate_lines_to_fit(&lines, max_size);

        let total_size: usize = truncated.iter().map(|l| l.len() + 1).sum();
        assert!(
            total_size <= max_size,
            "truncate_lines_to_fit must not exceed max_size after adding suffix (got {total_size} > {max_size})"
        );
    }

    // =========================================================================
    // Exhaustive edge case tests for truncation invariants
    // =========================================================================

    /// Test that truncation output never exceeds `max_size` for various edge cases.
    ///
    /// This exhaustively tests boundary conditions around the truncation summary
    /// appending logic to ensure the invariant "`output.len()` <= `max_size`" holds.
    #[test]
    fn truncate_diff_invariant_never_exceeds_max_size_edge_cases() {
        // Test various max_size values around the summary length
        let summary_len = "\n[Truncated: 1 of 2 files shown]\n".len();

        for max_size in [
            10,                 // Very small
            summary_len - 1,    // Just under summary
            summary_len,        // Exactly summary
            summary_len + 1,    // Just over summary
            summary_len + 10,   // Summary + small content
            100,                // Reasonable small size
            1000,               // Reasonable larger size
        ] {
            let file1 = format!(
                "diff --git a/src/a.rs b/src/a.rs\n+{}\n",
                "x".repeat(max_size)
            );
            let file2 = "diff --git a/tests/b.rs b/tests/b.rs\n+extra\n";
            let diff = format!("{file1}{file2}");

            let (truncated, _) = truncate_diff_to_model_budget(&diff, max_size as u64);
            assert!(
                truncated.len() <= max_size,
                "truncated diff exceeded max_size {} (got {}): {:?}",
                max_size,
                truncated.len(),
                &truncated[..truncated.len().min(100)]
            );
        }
    }

    /// Test truncation with content exactly at boundary conditions.
    #[test]
    fn truncate_diff_boundary_content_sizes() {
        for max_size in [50usize, 100, 200, 500] {
            // Content exactly at max_size - should not truncate
            let header = "diff --git a/a b/a\n+";
            let exact_diff = format!(
                "{}{}",
                header,
                "x".repeat(max_size.saturating_sub(header.len()))
            );
            if exact_diff.len() == max_size {
                let (result, was_truncated) =
                    truncate_diff_to_model_budget(&exact_diff, max_size as u64);
                assert!(!was_truncated, "exact size should not trigger truncation");
                assert_eq!(result.len(), max_size);
            }

            // Content one byte over max_size - should truncate
            let over_diff = format!(
                "{}{}",
                header,
                "x".repeat(max_size + 1 - header.len())
            );
            let (result, was_truncated) =
                truncate_diff_to_model_budget(&over_diff, max_size as u64);
            assert!(was_truncated, "over size should trigger truncation");
            assert!(
                result.len() <= max_size,
                "truncated result {} should not exceed max_size {}",
                result.len(),
                max_size
            );
        }
    }

    /// Test that single-file diffs that exceed `max_size` are properly truncated.
    #[test]
    fn truncate_single_large_file_stays_within_budget() {
        let max_size = 100usize;

        // Single file that's way too big
        let large_file = format!(
            "diff --git a/src/big.rs b/src/big.rs\n+{}\n",
            "x".repeat(max_size * 3)
        );

        let (truncated, was_truncated) =
            truncate_diff_to_model_budget(&large_file, max_size as u64);
        assert!(was_truncated, "large file should be truncated");
        assert!(
            truncated.len() <= max_size,
            "single large file truncation {} exceeded max_size {}",
            truncated.len(),
            max_size
        );
    }

    /// Test truncation with unicode content (multi-byte characters).
    #[test]
    fn truncate_diff_handles_unicode_boundaries() {
        let max_size = 50usize;

        // Unicode content: each emoji is 4 bytes
        let emoji_line = "🎉".repeat(20); // 80 bytes
        let diff = format!("diff --git a/a b/a\n+{emoji_line}\n");

        let (truncated, was_truncated) = truncate_diff_to_model_budget(&diff, max_size as u64);
        assert!(was_truncated, "unicode diff should be truncated");
        assert!(
            truncated.len() <= max_size,
            "unicode truncation {} exceeded max_size {}",
            truncated.len(),
            max_size
        );
        // Verify we didn't split a multi-byte character
        assert!(
            std::str::from_utf8(truncated.as_bytes()).is_ok(),
            "truncated output should be valid UTF-8"
        );
    }

    /// Test that empty diff is handled correctly.
    #[test]
    fn truncate_empty_diff() {
        let (result, was_truncated) = truncate_diff_to_model_budget("", 100);
        assert!(!was_truncated, "empty diff should not be truncated");
        assert_eq!(result, "");
    }

    /// Test truncation with multiple small files.
    #[test]
    fn truncate_multiple_small_files_prefers_high_priority() {
        let max_size = 200usize;

        // Create multiple files with different priorities
        let src_file = "diff --git a/src/main.rs b/src/main.rs\n+high priority\n";
        let test_file = "diff --git a/tests/test.rs b/tests/test.rs\n+medium priority\n";
        let doc_file = "diff --git a/README.md b/README.md\n+low priority docs\n";
        let extra = "diff --git a/extra.rs b/extra.rs\n+extra content that exceeds budget\n";

        let diff = format!("{doc_file}{test_file}{src_file}{extra}");

        let (truncated, was_truncated) = truncate_diff_to_model_budget(&diff, max_size as u64);
        assert!(was_truncated, "should truncate when files exceed budget");
        assert!(
            truncated.len() <= max_size,
            "truncated {} exceeded max_size {}",
            truncated.len(),
            max_size
        );
        // High priority src file should be included before low priority docs
        if truncated.contains("priority") {
            assert!(
                truncated.contains("high priority") || truncated.contains("medium priority"),
                "should prioritize src/tests over docs"
            );
        }
    }
}
