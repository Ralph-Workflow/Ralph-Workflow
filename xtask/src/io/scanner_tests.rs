use super::*;
use std::fs;

fn make_temp_dir(name: &str) -> PathBuf {
    let unique = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let base = std::env::temp_dir().join(format!("xtask-scanner-{name}-{unique}"));
    fs::create_dir_all(&base).unwrap();
    base
}

fn write_file(dir: &Path, rel_path: &str, content: &str) {
    let full = dir.join(rel_path);
    if let Some(parent) = full.parent() {
        fs::create_dir_all(parent).unwrap();
    }
    fs::write(full, content).unwrap();
}

// ── LineIndex unit tests ──────────────────────────────────────────────────

mod line_index_tests {
    use super::super::LineIndex;

    #[test]
    fn test_empty_content_line_number() {
        let idx = LineIndex::new(&[]);
        assert_eq!(idx.line_number(0), 0);
    }

    #[test]
    fn test_empty_content_line_start() {
        let idx = LineIndex::new(&[]);
        assert_eq!(idx.line_start(0), 0);
    }

    #[test]
    fn test_empty_content_line_end() {
        let idx = LineIndex::new(&[]);
        assert_eq!(idx.line_end(0), 0);
    }

    #[test]
    fn test_single_line_no_trailing_newline_line_number() {
        let content = b"hello";
        let idx = LineIndex::new(content);
        assert_eq!(idx.line_number(0), 0);
        assert_eq!(idx.line_number(4), 0);
    }

    #[test]
    fn test_single_line_no_trailing_newline_line_start() {
        let content = b"hello";
        let idx = LineIndex::new(content);
        assert_eq!(idx.line_start(0), 0);
        assert_eq!(idx.line_start(4), 0);
    }

    #[test]
    fn test_single_line_no_trailing_newline_line_end() {
        let content = b"hello";
        let idx = LineIndex::new(content);
        assert_eq!(idx.line_end(0), 5);
        assert_eq!(idx.line_end(4), 5);
    }

    #[test]
    fn test_single_newline_only() {
        // b"\n": offset 0 is before the newline (line 0), offset 1 is after (line 1).
        let content = b"\n";
        let idx = LineIndex::new(content);
        assert_eq!(idx.line_number(0), 0, "offset 0 must be on line 0");
        assert_eq!(
            idx.line_number(1),
            1,
            "offset 1 (after newline) must be on line 1"
        );
    }

    #[test]
    fn test_two_lines_line_number() {
        // b"abc\ndef": newline at offset 3.
        let content = b"abc\ndef";
        let idx = LineIndex::new(content);
        assert_eq!(idx.line_number(0), 0);
        assert_eq!(idx.line_number(2), 0);
        assert_eq!(idx.line_number(3), 0, "newline itself is on line 0");
        assert_eq!(idx.line_number(4), 1, "char after newline is on line 1");
        assert_eq!(idx.line_number(6), 1);
    }

    #[test]
    fn test_two_lines_line_start() {
        let content = b"abc\ndef";
        let idx = LineIndex::new(content);
        assert_eq!(idx.line_start(0), 0);
        assert_eq!(idx.line_start(5), 4, "line 'def' starts at offset 4");
    }

    #[test]
    fn test_two_lines_line_end() {
        let content = b"abc\ndef";
        let idx = LineIndex::new(content);
        assert_eq!(idx.line_end(0), 3, "line 'abc' ends at newline offset 3");
        assert_eq!(idx.line_end(5), 7, "line 'def' ends at content_len 7");
    }

    #[test]
    fn test_offset_exactly_at_newline() {
        // Newline is at offset 3; line_start(3) should be 0 (still on line 0).
        let content = b"abc\ndef";
        let idx = LineIndex::new(content);
        assert_eq!(idx.line_start(3), 0);
        assert_eq!(idx.line_end(3), 3);
    }

    #[test]
    fn test_last_line_no_trailing_newline_line_end() {
        let content = b"first\nsecond";
        let idx = LineIndex::new(content);
        // "second" starts at offset 6, content_len == 12.
        assert_eq!(idx.line_end(6), 12);
        assert_eq!(idx.line_end(11), 12);
    }

    #[test]
    fn test_extract_line_returns_correct_bytes() {
        let content = b"line one\nline two\nline three";
        let idx = LineIndex::new(content);
        assert_eq!(idx.extract_line(content, 0), b"line one");
        assert_eq!(idx.extract_line(content, 9), b"line two");
        assert_eq!(idx.extract_line(content, 18), b"line three");
    }

    #[test]
    fn test_line_number_monotonically_non_decreasing() {
        let content = b"a\nb\nc\nd";
        let idx = LineIndex::new(content);
        let mut prev = 0usize;
        for i in 0..content.len() {
            let ln = idx.line_number(i);
            assert!(
                ln >= prev,
                "line_number must be non-decreasing: offset {i}, got {ln}, prev {prev}"
            );
            prev = ln;
        }
    }

    #[test]
    fn test_multiline_with_trailing_newline() {
        let content = b"alpha\nbeta\n";
        let idx = LineIndex::new(content);
        // Trailing newline at offset 10; offset 11 is on an empty line 2.
        assert_eq!(
            idx.line_number(10),
            1,
            "newline at offset 10 is still on line 1"
        );
        assert_eq!(idx.line_number(11), 2, "after trailing newline is line 2");
        assert_eq!(idx.line_start(11), 11);
        assert_eq!(idx.line_end(11), 11); // empty last line, content_len == 11
    }
}

// ── helpers ───────────────────────────────────────────────────────────────

fn simple_check(name: &'static str, literals: &'static [&'static str]) -> NativeScanCheck {
    NativeScanCheck {
        name,
        literals,
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    }
}

// ── basic literal matching ────────────────────────────────────────────────

#[test]
fn test_literal_match_found_returns_failure() {
    let dir = make_temp_dir("literal-match");
    write_file(&dir, "src/lib.rs", "let forbidden_pattern = true;\n");

    let check = NativeScanCheck {
        name: "test-simple",
        literals: &["forbidden_pattern"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(!results[0].passed, "should fail when pattern found");
    assert!(!results[0].violations.is_empty());
    assert_eq!(results[0].violations[0].line_number, 1);

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_literal_match_not_found_returns_pass() {
    let dir = make_temp_dir("literal-no-match");
    write_file(&dir, "src/lib.rs", "let safe_code = true;\n");

    let check = NativeScanCheck {
        name: "test-simple",
        literals: &["forbidden_pattern"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(results[0].passed);

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_empty_directory_returns_pass() {
    let dir = make_temp_dir("empty-dir");
    fs::create_dir_all(dir.join("src")).unwrap();

    let check = NativeScanCheck {
        name: "test-empty",
        literals: &["anything"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(results[0].passed);

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_missing_directory_returns_pass() {
    let dir = make_temp_dir("missing-dir");
    // Do NOT create src/ subdirectory.

    let check = NativeScanCheck {
        name: "test-missing",
        literals: &["anything"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(results[0].passed);

    let _ = fs::remove_dir_all(&dir);
}

#[cfg(unix)]
#[test]
fn test_unreadable_directory_causes_failure_not_silent_pass() {
    use std::os::unix::fs::PermissionsExt;

    let dir = make_temp_dir("unreadable-dir");
    write_file(&dir, "src/lib.rs", "forbidden_pattern\n");

    // Make the directory unreadable so directory walking hits a read_dir error.
    let src_dir = dir.join("src");
    let mut perms = fs::metadata(&src_dir).unwrap().permissions();
    perms.set_mode(0o000);
    fs::set_permissions(&src_dir, perms).unwrap();

    let check = NativeScanCheck {
        name: "test-unreadable",
        literals: &["forbidden_pattern"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});

    // Restore permissions so cleanup works.
    let mut perms_restore = fs::metadata(&src_dir).unwrap().permissions();
    perms_restore.set_mode(0o755);
    let _ = fs::set_permissions(&src_dir, perms_restore);

    assert!(
        !results[0].passed,
        "unreadable directories must fail the scan (not silently pass)"
    );
    assert!(
        results[0]
            .violations
            .iter()
            .any(|v| v.line.contains("read_dir") || v.line.contains("Permission")),
        "expected an explicit read_dir error violation, got: {}",
        results[0]
            .violations
            .iter()
            .map(|v| v.line.as_str())
            .collect::<Vec<_>>()
            .join(" | ")
    );

    let _ = fs::remove_dir_all(&dir);
}

#[cfg(unix)]
#[test]
fn test_unreadable_file_causes_failure_not_silent_pass() {
    use std::os::unix::fs::PermissionsExt;

    let dir = make_temp_dir("unreadable-file");
    write_file(&dir, "src/lib.rs", "forbidden_pattern\n");

    // Make the file unreadable so the scan must surface a read error.
    let src_file = dir.join("src/lib.rs");
    let mut perms = fs::metadata(&src_file).unwrap().permissions();
    perms.set_mode(0o000);
    fs::set_permissions(&src_file, perms).unwrap();

    let check = NativeScanCheck {
        name: "test-unreadable-file",
        literals: &["forbidden_pattern"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});

    // Restore permissions so cleanup works.
    let mut perms_restore = fs::metadata(&src_file).unwrap().permissions();
    perms_restore.set_mode(0o644);
    let _ = fs::set_permissions(&src_file, perms_restore);

    assert!(
        !results[0].passed,
        "unreadable files must fail the scan (not silently pass)"
    );
    assert!(
        results[0]
            .violations
            .iter()
            .any(|v| v.line.contains("read") || v.line.contains("Permission")),
        "expected an explicit read-file error violation, got: {}",
        results[0]
            .violations
            .iter()
            .map(|v| v.line.as_str())
            .collect::<Vec<_>>()
            .join(" | ")
    );

    let _ = fs::remove_dir_all(&dir);
}

// ── comment-line exclusion ────────────────────────────────────────────────

#[test]
fn test_comment_line_skipped_when_flag_set() {
    let dir = make_temp_dir("comment-skip");
    write_file(&dir, "src/lib.rs", "// forbidden_pattern\n");

    let check = NativeScanCheck {
        name: "test-comment-skip",
        literals: &["forbidden_pattern"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        results[0].passed,
        "comment-line match must not trigger failure"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_comment_line_not_skipped_when_flag_unset() {
    let dir = make_temp_dir("comment-noskip");
    write_file(&dir, "src/lib.rs", "// forbidden_pattern\n");

    let check = NativeScanCheck {
        name: "test-comment-noskip",
        literals: &["forbidden_pattern"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "comment-line match must trigger failure"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_indented_comment_line_skipped() {
    let dir = make_temp_dir("indented-comment");
    write_file(&dir, "src/lib.rs", "    // forbidden_pattern\n");

    let check = NativeScanCheck {
        name: "test-indented-comment",
        literals: &["forbidden_pattern"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        results[0].passed,
        "indented comment-line match must not trigger failure"
    );

    let _ = fs::remove_dir_all(&dir);
}

// ── StemWithBoolSuffix ────────────────────────────────────────────────────

#[test]
fn test_stem_with_bool_suffix_matches() {
    let dir = make_temp_dir("stem-bool-match");
    write_file(&dir, "src/lib.rs", "fn foo(test_mode: bool) {}\n");

    let check = NativeScanCheck {
        name: "test-stem",
        literals: &["test_mode"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::StemWithBoolSuffix,
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(!results[0].passed, "test_mode: bool must trigger failure");

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_stem_with_bool_suffix_no_match_without_bool() {
    let dir = make_temp_dir("stem-no-bool");
    write_file(&dir, "src/lib.rs", "let test_mode = false;\n");

    let check = NativeScanCheck {
        name: "test-stem-nobool",
        literals: &["test_mode"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::StemWithBoolSuffix,
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        results[0].passed,
        "test_mode without ': bool' must not trigger failure"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_stem_with_bool_suffix_with_spaces() {
    let dir = make_temp_dir("stem-bool-spaces");
    write_file(&dir, "src/lib.rs", "fn foo(test_mode : bool) {}\n");

    let check = NativeScanCheck {
        name: "test-stem-spaces",
        literals: &["test_mode"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::StemWithBoolSuffix,
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "test_mode : bool (spaces) must trigger failure"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_stem_with_bool_suffix_with_newline_whitespace() {
    // Regression: formatting can insert newlines between the stem and `: bool`.
    // This should still match the intended `\s*:\s*bool` suffix.
    let dir = make_temp_dir("stem-bool-newline");
    write_file(&dir, "src/lib.rs", "fn foo(test_mode\n: bool) {}\n");

    let check = NativeScanCheck {
        name: "test-stem-newline",
        literals: &["test_mode"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::StemWithBoolSuffix,
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "test_mode\\n: bool must trigger failure (whitespace includes newlines)"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_stem_word_boundary_prevents_false_positive() {
    // "is_testing_mode: bool" should NOT trigger the "is_test" stem check
    // because the char before "testing_mode" is "is_", forming a longer identifier.
    let dir = make_temp_dir("stem-word-boundary");
    write_file(&dir, "src/lib.rs", "fn foo(is_testing_mode: bool) {}\n");

    let check = NativeScanCheck {
        name: "test-boundary",
        literals: &["is_test"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::StemWithBoolSuffix,
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        results[0].passed,
        "is_testing_mode: bool must NOT trigger is_test stem check"
    );

    let _ = fs::remove_dir_all(&dir);
}

// ── file exclusion globs ──────────────────────────────────────────────────

#[test]
fn test_excluded_file_not_scanned() {
    let dir = make_temp_dir("exclude-file");
    write_file(&dir, "src/_TEMPLATE.rs", "forbidden_pattern\n");
    write_file(&dir, "src/lib.rs", "safe\n");

    let check = NativeScanCheck {
        name: "test-exclude",
        literals: &["forbidden_pattern"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &["_TEMPLATE.rs"],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(results[0].passed, "_TEMPLATE.rs must be excluded from scan");

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_path_component_exclude_glob() {
    let dir = make_temp_dir("exclude-path-component");
    write_file(&dir, "src/tests/test_foo.rs", "forbidden_pattern\n");
    write_file(&dir, "src/lib.rs", "safe\n");

    let check = NativeScanCheck {
        name: "test-path-exclude",
        literals: &["forbidden_pattern"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &["**/tests/**"],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        results[0].passed,
        "files under tests/ must be excluded from scan"
    );

    let _ = fs::remove_dir_all(&dir);
}

// ── multi-check isolation ─────────────────────────────────────────────────

#[test]
fn test_two_checks_independent_results() {
    let dir = make_temp_dir("multi-check");
    write_file(&dir, "src/lib.rs", "pattern_a\n");

    let checks = [
        simple_check("check-a", &["pattern_a"]),
        simple_check("check-b", &["pattern_b"]),
    ];

    let results = run_native_scan_checks_reporting(&dir, &checks, &|_, _| {});
    assert!(!results[0].passed, "check-a must fail (pattern_a present)");
    assert!(results[1].passed, "check-b must pass (pattern_b absent)");

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_check_result_order_matches_input_order() {
    let dir = make_temp_dir("result-order");
    write_file(&dir, "src/lib.rs", "alpha beta\n");

    let checks = [
        simple_check("first", &["alpha"]),
        simple_check("second", &["beta"]),
        simple_check("third", &["gamma"]),
    ];

    let results = run_native_scan_checks_reporting(&dir, &checks, &|_, _| {});
    assert_eq!(results[0].check_name, "first");
    assert_eq!(results[1].check_name, "second");
    assert_eq!(results[2].check_name, "third");
    assert!(!results[0].passed);
    assert!(!results[1].passed);
    assert!(results[2].passed);

    let _ = fs::remove_dir_all(&dir);
}

// ── directory grouping ────────────────────────────────────────────────────

#[test]
fn test_two_checks_same_directory_both_detected() {
    // Two checks with the same directory are grouped: files read once,
    // but both patterns are detected.
    let dir = make_temp_dir("same-dir-group");
    write_file(&dir, "src/lib.rs", "pattern_x\npattern_y\n");

    let checks = [
        simple_check("check-x", &["pattern_x"]),
        simple_check("check-y", &["pattern_y"]),
    ];

    let results = run_native_scan_checks_reporting(&dir, &checks, &|_, _| {});
    assert!(!results[0].passed);
    assert!(!results[1].passed);

    let _ = fs::remove_dir_all(&dir);
}

// ── alternation patterns ──────────────────────────────────────────────────

#[test]
fn test_any_of_multiple_literals_triggers_failure() {
    let dir = make_temp_dir("alternation");
    write_file(&dir, "src/lib.rs", "let skip_auth = true;\n");

    let check = NativeScanCheck {
        name: "test-alternation",
        literals: &[
            "skip_validation",
            "skip_verify",
            "skip_check",
            "skip_auth",
            "skip_api",
        ],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::StemWithBoolSuffix,
    };

    // skip_auth = true (not `: bool`) → must NOT match
    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(results[0].passed, "skip_auth without ': bool' must pass");

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_any_of_multiple_literals_with_bool_triggers_failure() {
    let dir = make_temp_dir("alternation-bool");
    write_file(&dir, "src/lib.rs", "fn f(skip_auth: bool) {}\n");

    let check = NativeScanCheck {
        name: "test-alternation-bool",
        literals: &[
            "skip_validation",
            "skip_verify",
            "skip_check",
            "skip_auth",
            "skip_api",
        ],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::StemWithBoolSuffix,
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(!results[0].passed, "skip_auth: bool must fail");

    let _ = fs::remove_dir_all(&dir);
}

// ── violation details ─────────────────────────────────────────────────────

#[test]
fn test_violation_contains_correct_line_number() {
    let dir = make_temp_dir("line-number");
    write_file(
        &dir,
        "src/lib.rs",
        "fn foo() {}\nfn bar() {}\nforbidden_here\nfn baz() {}\n",
    );

    let check = NativeScanCheck {
        name: "test-lineno",
        literals: &["forbidden_here"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(!results[0].passed);
    assert_eq!(results[0].violations[0].line_number, 3);

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_violation_contains_line_text() {
    let dir = make_temp_dir("line-text");
    write_file(&dir, "src/lib.rs", "    let forbidden_pattern = 42;\n");

    let check = NativeScanCheck {
        name: "test-linetext",
        literals: &["forbidden_pattern"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(!results[0].passed);
    assert!(results[0].violations[0].line.contains("forbidden_pattern"));

    let _ = fs::remove_dir_all(&dir);
}

// ── AnyLiteralAtLineStart ─────────────────────────────────────────────────

#[test]
fn test_line_start_literal_at_column_zero_is_violation() {
    let dir = make_temp_dir("line-start-col0");
    write_file(&dir, "src/lib.rs", "#[allow(clippy::foo)]\nfn foo() {}\n");

    let check = NativeScanCheck {
        name: "test-line-start-col0",
        literals: &["#[allow("],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "#[allow( at column 0 must trigger violation"
    );
    assert_eq!(results[0].violations[0].line_number, 1);

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_line_start_literal_with_leading_spaces_is_violation() {
    let dir = make_temp_dir("line-start-spaces");
    write_file(&dir, "src/lib.rs", "    #[allow(clippy::foo)]\n");

    let check = NativeScanCheck {
        name: "test-line-start-spaces",
        literals: &["#[allow("],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "#[allow( after leading spaces must trigger violation"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_line_start_literal_inline_not_violation() {
    let dir = make_temp_dir("line-start-inline");
    // Inline usage: non-whitespace before #[allow( — not a line-start attribute.
    write_file(&dir, "src/lib.rs", "foo(#[allow(clippy::foo)] bar)\n");

    let check = NativeScanCheck {
        name: "test-line-start-inline",
        literals: &["#[allow("],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        results[0].passed,
        "#[allow( inline (after non-whitespace) must NOT trigger violation"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_line_start_comment_line_skipped_when_flag() {
    let dir = make_temp_dir("line-start-comment");
    write_file(&dir, "src/lib.rs", "// #[allow(clippy::foo)]\n");

    let check = NativeScanCheck {
        name: "test-line-start-comment",
        literals: &["#[allow("],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        results[0].passed,
        "comment-line #[allow( must be skipped when skip_comment_lines=true"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_forbidden_allow_expect_scan_rejects_allow_even_with_cfg_test() {
    let dir = make_temp_dir("line-start-large-stack-test-allow");
    write_file(
        &dir,
        "ralph-workflow/src/lib.rs",
        "#[cfg(test)]\n#[allow(clippy::large_stack_frames)]\nmod tests {}\n",
    );

    let check = NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &["#[allow(", "#![allow(", "#[expect(", "#![expect("],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "#[allow(clippy::large_stack_frames)] must be rejected even when preceded by #[cfg(test)] — use #[expect(..., reason=...)] instead"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_forbidden_allow_expect_scan_rejects_file_scope_large_stack_frames_in_tests_tree() {
    let dir = make_temp_dir("line-start-large-stack-tests-tree-inner-allow");
    write_file(
        &dir,
        "tests/integration_tests/sample.rs",
        "#![allow(clippy::large_stack_frames)]\nfn sample() {}\n",
    );

    let check = NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &["#[allow(", "#![allow(", "#[expect(", "#![expect("],
        directories: &["tests"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "file-scope large_stack_frames allow should not be exempt just because the file is under tests/"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_forbidden_allow_expect_scan_catches_cfg_attr_allow() {
    let dir = make_temp_dir("cfg-attr-allow");
    write_file(
        &dir,
        "ralph-workflow/src/lib.rs",
        "#[cfg_attr(test, allow(clippy::large_stack_frames))]\nfn foo() {}\n",
    );

    let check = NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &[
            "#[allow(",
            "#![allow(",
            "#[expect(",
            "#![expect(",
            "#[cfg_attr(",
            "#![cfg_attr(",
        ],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "cfg_attr wrapping allow(...) must be detected as a violation"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_forbidden_allow_expect_scan_catches_crate_level_cfg_attr_allow() {
    let dir = make_temp_dir("crate-cfg-attr-allow");
    write_file(
        &dir,
        "ralph-workflow/src/lib.rs",
        "#![cfg_attr(test, allow(clippy::large_stack_frames))]\nfn foo() {}\n",
    );

    let check = NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &[
            "#[allow(",
            "#![allow(",
            "#[expect(",
            "#![expect(",
            "#[cfg_attr(",
            "#![cfg_attr(",
        ],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "crate-level #![cfg_attr(..., allow(...))] must be detected as a violation"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_forbidden_allow_expect_scan_skips_cfg_attr_without_allow_or_expect() {
    let dir = make_temp_dir("cfg-attr-no-allow");
    write_file(
        &dir,
        "ralph-workflow/src/lib.rs",
        "#[cfg_attr(test, derive(Debug))]\nfn foo() {}\n",
    );

    let check = NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &[
            "#[allow(",
            "#![allow(",
            "#[expect(",
            "#![expect(",
            "#[cfg_attr(",
            "#![cfg_attr(",
        ],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        results[0].passed,
        "cfg_attr without allow/expect must NOT be flagged as a violation (false positive)"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_forbidden_allow_expect_scan_catches_cfg_attr_expect() {
    let dir = make_temp_dir("cfg-attr-expect");
    write_file(
        &dir,
        "ralph-workflow/src/lib.rs",
        "#[cfg_attr(test, expect(clippy::large_stack_frames))]\nfn foo() {}\n",
    );

    let check = NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &[
            "#[allow(",
            "#![allow(",
            "#[expect(",
            "#![expect(",
            "#[cfg_attr(",
            "#![cfg_attr(",
        ],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "cfg_attr wrapping expect(...) must be detected as a violation"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_forbidden_allow_expect_scan_allows_expect_with_reason() {
    let dir = make_temp_dir("expect-with-reason-allowed");
    write_file(
        &dir,
        "ralph-workflow/src/lib.rs",
        "#[expect(clippy::some_lint, reason = \"proc-macro output from derive_more\")]\nfn foo() {}\n",
    );

    let check = NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &["#[allow(", "#![allow(", "#[expect(", "#![expect("],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        results[0].passed,
        "#[expect(..., reason = \"...\")] at item scope should be allowed"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_forbidden_allow_expect_scan_blocks_expect_without_reason() {
    let dir = make_temp_dir("expect-without-reason-blocked");
    write_file(
        &dir,
        "ralph-workflow/src/lib.rs",
        "#[expect(clippy::some_lint)]\nfn foo() {}\n",
    );

    let check = NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &["#[allow(", "#![allow(", "#[expect(", "#![expect("],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "#[expect(...)] without reason should be blocked"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_forbidden_allow_expect_scan_blocks_inner_expect_with_reason() {
    let dir = make_temp_dir("inner-expect-blocked");
    write_file(
        &dir,
        "ralph-workflow/src/lib.rs",
        "#![expect(clippy::some_lint, reason = \"external\")]\nfn foo() {}\n",
    );

    let check = NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &["#[allow(", "#![allow(", "#[expect(", "#![expect("],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "#![expect(...)] (inner attribute) should always be blocked regardless of reason"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_forbidden_allow_expect_scan_blocks_expect_with_empty_reason() {
    let dir = make_temp_dir("empty-reason-blocked");
    write_file(
        &dir,
        "ralph-workflow/src/lib.rs",
        "#[expect(clippy::some_lint, reason = \"\")]\nfn foo() {}\n",
    );

    let check = NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &["#[allow(", "#![allow(", "#[expect(", "#![expect("],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "#[expect(..., reason = \"\")] with empty reason should be blocked"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_forbidden_allow_expect_scan_allows_cfg_attr_expect_with_reason() {
    let dir = make_temp_dir("cfg-attr-expect-reason-allowed");
    write_file(
        &dir,
        "ralph-workflow/src/lib.rs",
        "#[cfg_attr(test, expect(clippy::some_lint, reason = \"proc-macro\"))]\nfn foo() {}\n",
    );

    let check = NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &[
            "#[allow(",
            "#![allow(",
            "#[expect(",
            "#![expect(",
            "#[cfg_attr(",
            "#![cfg_attr(",
        ],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        results[0].passed,
        "#[cfg_attr(..., expect(..., reason = \"...\"))] should be allowed"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_forbidden_allow_expect_scan_blocks_cfg_attr_expect_without_reason() {
    let dir = make_temp_dir("cfg-attr-expect-no-reason-blocked");
    write_file(
        &dir,
        "ralph-workflow/src/lib.rs",
        "#[cfg_attr(test, expect(clippy::some_lint))]\nfn foo() {}\n",
    );

    let check = NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &[
            "#[allow(",
            "#![allow(",
            "#[expect(",
            "#![expect(",
            "#[cfg_attr(",
            "#![cfg_attr(",
        ],
        directories: &["ralph-workflow/src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "#[cfg_attr(..., expect(...))] without reason should be blocked"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_line_has_nonempty_reason() {
    // Test cases for the line_has_nonempty_reason helper
    use super::line_has_nonempty_reason;

    // Valid cases with non-empty reason
    assert!(
        line_has_nonempty_reason(b"#[expect(clippy::foo, reason = \"proc-macro\")]"),
        "reason = \"proc-macro\" should be valid"
    );
    assert!(
        line_has_nonempty_reason(b"#[expect(clippy::foo, reason=\"proc-macro\")]"),
        "reason=\"proc-macro\" should be valid (no space)"
    );
    assert!(
        line_has_nonempty_reason(b"#[expect(clippy::foo, reason =  \"proc-macro\")]"),
        "reason =  \"proc-macro\" should be valid (extra space)"
    );

    // Invalid cases - no reason
    assert!(
        !line_has_nonempty_reason(b"#[expect(clippy::foo)]"),
        "no reason should be invalid"
    );

    // Invalid cases - empty reason
    assert!(
        !line_has_nonempty_reason(b"#[expect(clippy::foo, reason = \"\")]"),
        "empty reason should be invalid"
    );

    // Invalid cases - no quote
    assert!(
        !line_has_nonempty_reason(b"#[expect(clippy::foo, reason = )]"),
        "no quote should be invalid"
    );

    // Valid even with #[allow] prefix (helper doesn't filter)
    assert!(
        line_has_nonempty_reason(b"#[allow(clippy::foo, reason = \"test\")]"),
        "reason in #[allow] should be detected by helper"
    );
}

#[test]
fn test_line_start_violation_contains_correct_line_number() {
    let dir = make_temp_dir("line-start-lineno");
    write_file(
        &dir,
        "src/lib.rs",
        "fn foo() {}\nfn bar() {}\n#[allow(dead_code)]\nfn baz() {}\n",
    );

    let check = NativeScanCheck {
        name: "test-line-start-lineno",
        literals: &["#[allow("],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(!results[0].passed);
    assert_eq!(
        results[0].violations[0].line_number, 3,
        "violation must be on line 3"
    );

    let _ = fs::remove_dir_all(&dir);
}

// ── NegativeLookahead ─────────────────────────────────────────────────────

#[test]
fn test_negative_lookahead_no_context_is_violation() {
    let dir = make_temp_dir("neg-lookahead-no-ctx");
    write_file(&dir, "src/lib.rs", "#[ignore]\nfn slow_test() {}\n");

    let check = NativeScanCheck {
        name: "test-neg-no-ctx",
        literals: &["#[ignore"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::NegativeLookahead {
            negative_context: "https://",
            word_boundary_at_end: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "#[ignore] without URL must trigger violation"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_negative_lookahead_with_context_is_pass() {
    let dir = make_temp_dir("neg-lookahead-with-ctx");
    write_file(
        &dir,
        "src/lib.rs",
        "#[ignore] // https://example.com/issue/123\nfn slow_test() {}\n",
    );

    let check = NativeScanCheck {
        name: "test-neg-with-ctx",
        literals: &["#[ignore"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::NegativeLookahead {
            negative_context: "https://",
            word_boundary_at_end: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        results[0].passed,
        "#[ignore] with URL on same line must NOT trigger violation"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_negative_lookahead_word_boundary_prevents_match() {
    let dir = make_temp_dir("neg-lookahead-boundary");
    // #[ignore_slow] — the char after "ignore" is '_', an identifier char.
    write_file(&dir, "src/lib.rs", "#[ignore_slow]\nfn test() {}\n");

    let check = NativeScanCheck {
        name: "test-neg-boundary",
        literals: &["#[ignore"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::NegativeLookahead {
            negative_context: "https://",
            word_boundary_at_end: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        results[0].passed,
        "#[ignore_slow] must NOT trigger when word_boundary_at_end=true"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_negative_lookahead_no_boundary_matches_any() {
    let dir = make_temp_dir("neg-lookahead-no-boundary");
    // Without word-boundary check, #[ignore_slow] DOES trigger.
    write_file(&dir, "src/lib.rs", "#[ignore_slow]\nfn test() {}\n");

    let check = NativeScanCheck {
        name: "test-neg-no-boundary",
        literals: &["#[ignore"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::NegativeLookahead {
            negative_context: "https://",
            word_boundary_at_end: false,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        !results[0].passed,
        "#[ignore_slow] MUST trigger when word_boundary_at_end=false"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_negative_lookahead_context_anywhere_on_line_is_pass() {
    let dir = make_temp_dir("neg-lookahead-ctx-anywhere");
    // URL appears before the #[ignore] on the same line.
    write_file(
        &dir,
        "src/lib.rs",
        "// see https://example.com #[ignore]\nfn test() {}\n",
    );

    let check = NativeScanCheck {
        name: "test-neg-ctx-anywhere",
        literals: &["#[ignore"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::NegativeLookahead {
            negative_context: "https://",
            word_boundary_at_end: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        results[0].passed,
        "https:// before #[ignore] on same line must NOT trigger violation"
    );

    let _ = fs::remove_dir_all(&dir);
}

// ── parallel groups ───────────────────────────────────────────────────────

#[test]
fn test_parallel_groups_return_same_results_as_single_group() {
    // Two checks in DIFFERENT directories → two separate scan groups.
    // Verifies that parallel group scanning produces the same violations as
    // running each check individually.
    let dir = make_temp_dir("parallel-groups");
    write_file(&dir, "src/lib.rs", "pattern_alpha\n");
    write_file(&dir, "other/lib.rs", "pattern_beta\n");

    let checks = [
        NativeScanCheck {
            name: "check-alpha",
            literals: &["pattern_alpha"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        },
        NativeScanCheck {
            name: "check-beta",
            literals: &["pattern_beta"],
            directories: &["other"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        },
    ];

    let results = run_native_scan_checks_reporting(&dir, &checks, &|_, _| {});
    assert!(!results[0].passed, "check-alpha must find pattern_alpha");
    assert!(!results[1].passed, "check-beta must find pattern_beta");

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_audit_ignore_has_url_is_in_native_scan_checks() {
    assert!(
        NATIVE_SCAN_CHECKS
            .iter()
            .any(|c| c.name == "audit-ignore-has-url"),
        "NATIVE_SCAN_CHECKS must include audit-ignore-has-url"
    );
}

#[test]
fn test_forbidden_allow_expect_is_in_native_scan_checks() {
    assert!(
        NATIVE_SCAN_CHECKS
            .iter()
            .any(|c| c.name == "forbidden-allow-expect-scan"),
        "NATIVE_SCAN_CHECKS must include forbidden-allow-expect-scan"
    );
}

#[test]
fn test_forbidden_allow_expect_scan_covers_gui_rust() {
    let check = NATIVE_SCAN_CHECKS
        .iter()
        .find(|check| check.name == "forbidden-allow-expect-scan")
        .expect("forbidden-allow-expect-scan must be present");

    assert!(
        check.directories.contains(&"test-helpers/src"),
        "forbidden-allow-expect-scan must cover test-helpers/src"
    );
    assert!(
        check.directories.contains(&"ralph-gui"),
        "forbidden-allow-expect-scan must cover ralph-gui so GUI Rust files are scanned"
    );
    assert!(
        check.exclude_globs.contains(&"**/node_modules/**"),
        "forbidden-allow-expect-scan must exclude transient node_modules trees"
    );
    assert!(
        check.exclude_globs.contains(&"**/dist/**"),
        "forbidden-allow-expect-scan must exclude transient frontend build outputs"
    );
}

#[test]
fn test_forbidden_allow_expect_scan_covers_lints_directory() {
    let check = NATIVE_SCAN_CHECKS
        .iter()
        .find(|check| check.name == "forbidden-allow-expect-scan")
        .expect("forbidden-allow-expect-scan must be present");

    assert!(
        check.directories.contains(&"lints"),
        "forbidden-allow-expect-scan must cover lints/ directory"
    );
    assert!(
        check.exclude_globs.contains(&"**/ui/**"),
        "forbidden-allow-expect-scan must exclude lints/*/ui/ test fixtures"
    );
}

#[test]
fn test_forbidden_allow_expect_scan_excludes_lints_ui_directory() {
    let dir = make_temp_dir("lints-ui-exclusion");
    write_file(
        &dir,
        "lints/fake_lint/ui/violating.rs",
        "#![allow(clippy::large_stack_frames)]\nfn foo() {}\n",
    );

    let check = NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &["#[allow(", "#![allow(", "#[expect(", "#![expect("],
        directories: &["lints"],
        include_glob: "*.rs",
        exclude_globs: &["**/ui/**"],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    };

    let results = run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
    assert!(
        results[0].passed,
        "forbidden-allow-expect-scan should NOT flag violations in lints/*/ui/ test fixtures"
    );

    let _ = fs::remove_dir_all(&dir);
}

// ── NATIVE_SCAN_CHECKS sanity ─────────────────────────────────────────────

#[test]
fn test_native_scan_checks_all_have_non_empty_literals() {
    for check in NATIVE_SCAN_CHECKS {
        assert!(
            !check.literals.is_empty(),
            "check '{}' must have at least one literal",
            check.name
        );
    }
}

#[test]
fn test_native_scan_checks_all_have_non_empty_directories() {
    for check in NATIVE_SCAN_CHECKS {
        assert!(
            !check.directories.is_empty(),
            "check '{}' must specify at least one directory",
            check.name
        );
    }
}

#[test]
fn test_no_string_errors_handlers_check_is_in_native_scan_checks() {
    assert!(
        NATIVE_SCAN_CHECKS
            .iter()
            .any(|c| c.name == "no-string-errors-handlers"),
        "NATIVE_SCAN_CHECKS must include no-string-errors-handlers"
    );
}

#[test]
fn test_no_string_errors_handlers_scans_handler_directory() {
    let check = NATIVE_SCAN_CHECKS
        .iter()
        .find(|c| c.name == "no-string-errors-handlers")
        .expect("no-string-errors-handlers must be in NATIVE_SCAN_CHECKS");
    assert!(
        check
            .directories
            .iter()
            .any(|d| d.contains("reducer/handler")),
        "no-string-errors-handlers must scan the handler directory, got: {:?}",
        check.directories
    );
}

#[test]
fn test_scan_read_worker_count_is_bounded() {
    // Regression guard: never spawn one OS thread per file.
    // The exact bound is an implementation detail, but it must be far smaller
    // than the number of files to avoid resource exhaustion.
    let workers = super::scan_read_worker_count(10_000);
    assert!(workers > 0);
    assert!(workers <= 32, "workers must be capped, got {workers}");
}

// ── Boyer-Moore-Horspool tests ─────────────────────────────────────────────

mod bmh_tests {
    use super::super::bmh_contains;

    #[test]
    fn test_bmh_contains_empty_pattern_always_true() {
        assert!(bmh_contains(b"abc", b""), "empty pattern must always match");
        assert!(
            bmh_contains(b"", b""),
            "empty pattern in empty text must match"
        );
    }

    #[test]
    fn test_bmh_contains_empty_text_returns_false() {
        assert!(
            !bmh_contains(b"", b"abc"),
            "non-empty pattern in empty text must not match"
        );
    }

    #[test]
    fn test_bmh_contains_pattern_longer_than_text() {
        assert!(
            !bmh_contains(b"hi", b"hello"),
            "pattern longer than text must not match"
        );
    }

    #[test]
    fn test_bmh_contains_exact_match() {
        assert!(
            bmh_contains(b"https://", b"https://"),
            "exact match must return true"
        );
    }

    #[test]
    fn test_bmh_contains_found_at_start() {
        assert!(
            bmh_contains(b"https://example.com", b"https://"),
            "pattern at start of text must match"
        );
    }

    #[test]
    fn test_bmh_contains_found_at_end() {
        assert!(
            bmh_contains(b"see https://", b"https://"),
            "pattern at end of text must match"
        );
    }

    #[test]
    fn test_bmh_contains_not_found() {
        assert!(
            !bmh_contains(b"http://example.com", b"https://"),
            "https:// must not match in http:// URL"
        );
    }

    #[test]
    fn test_bmh_contains_single_char_pattern_found() {
        assert!(bmh_contains(b"abc", b"b"), "single char pattern must match");
    }

    #[test]
    fn test_bmh_contains_single_char_pattern_not_found() {
        assert!(
            !bmh_contains(b"abc", b"z"),
            "absent single char must not match"
        );
    }

    #[test]
    fn test_bmh_contains_repeated_chars() {
        // Degenerate case: repeated chars exercise the shift table carefully.
        assert!(
            bmh_contains(b"aaaaab", b"aaab"),
            "repeated char pattern must match when present"
        );
        assert!(
            !bmh_contains(b"aaaaa", b"aaab"),
            "repeated char pattern must not match when absent"
        );
    }

    #[test]
    fn test_bmh_contains_pattern_equals_text_length() {
        assert!(
            bmh_contains(b"hello", b"hello"),
            "pattern same length as text must match"
        );
        assert!(
            !bmh_contains(b"hello", b"world"),
            "pattern same length as text but different must not match"
        );
    }
}

// ── KMP (Knuth-Morris-Pratt) tests ───────────────────────────────────────

mod kmp_tests {
    use super::super::kmp_search;

    #[test]
    fn test_kmp_empty_needle_returns_some_zero() {
        // Empty needle matches at position 0 in any haystack.
        assert_eq!(kmp_search(b"abc", b""), Some(0));
        assert_eq!(kmp_search(b"", b""), Some(0));
    }

    #[test]
    fn test_kmp_needle_longer_than_haystack_returns_none() {
        assert_eq!(kmp_search(b"ab", b"abc"), None);
    }

    #[test]
    fn test_kmp_finds_at_start() {
        assert_eq!(kmp_search(b"bool is_flag", b"bool"), Some(0));
    }

    #[test]
    fn test_kmp_finds_at_end() {
        assert_eq!(kmp_search(b"is: bool", b"bool"), Some(4));
    }

    #[test]
    fn test_kmp_finds_in_middle() {
        assert_eq!(kmp_search(b"foo bool bar", b"bool"), Some(4));
    }

    #[test]
    fn test_kmp_not_found_returns_none() {
        assert_eq!(kmp_search(b"is: boo", b"bool"), None);
    }

    #[test]
    fn test_kmp_exact_match() {
        assert_eq!(kmp_search(b"bool", b"bool"), Some(0));
    }

    #[test]
    fn test_kmp_repeated_chars_worst_case() {
        // needle = "aaab", haystack = "aaaaaaaaab"
        // Naive search would scan O(n*m) positions; KMP uses O(n+m).
        // This validates correctness on the worst-case input for naive search.
        assert_eq!(kmp_search(b"aaaaaaaaab", b"aaab"), Some(6));
    }

    #[test]
    fn test_kmp_repeated_chars_not_found() {
        // "aaab" not present in "aaaaaaa" (no 'b').
        assert_eq!(kmp_search(b"aaaaaaa", b"aaab"), None);
    }

    #[test]
    fn test_kmp_single_char_found() {
        assert_eq!(kmp_search(b"xyz", b"y"), Some(1));
    }

    #[test]
    fn test_kmp_single_char_not_found() {
        assert_eq!(kmp_search(b"xyz", b"w"), None);
    }

    #[test]
    fn test_kmp_agrees_with_starts_with_for_bool_suffix() {
        // Verify kmp_search at position 0 agrees with starts_with for
        // all common bool-suffix patterns that StemWithBoolSuffix encounters.
        let cases: &[&[u8]] = &[
            b"bool)", b"bool,", b"bool ", b"bool\n", b"boolean", b"boo", b"bo", b"b", b"",
        ];
        for &haystack in cases {
            // kmp_search finds needle at pos 0 iff starts_with matches.
            let starts = haystack.starts_with(b"bool");
            let kmp_at_zero = kmp_search(haystack, b"bool") == Some(0);
            assert_eq!(
                starts, kmp_at_zero,
                "kmp vs starts_with disagreement for {haystack:?}"
            );
        }
    }
}

// ── Two-Way string search tests ───────────────────────────────────────────

mod tw_tests {
    use super::super::{bmh_contains, tw_contains};

    #[test]
    fn test_tw_contains_basic_match() {
        assert!(tw_contains(b"hello world", b"world"));
        assert!(tw_contains(b"https://example.com", b"https://"));
    }

    #[test]
    fn test_tw_contains_no_match() {
        assert!(!tw_contains(b"hello world", b"https://"));
        assert!(!tw_contains(b"http://example.com", b"https://"));
    }

    #[test]
    fn test_tw_contains_empty_pattern() {
        assert!(tw_contains(b"anything", b""));
        assert!(tw_contains(b"", b""));
    }

    #[test]
    fn test_tw_contains_pattern_equals_text() {
        assert!(tw_contains(b"https://", b"https://"));
    }

    #[test]
    fn test_tw_contains_pattern_longer_than_text() {
        assert!(!tw_contains(b"hi", b"hello"));
    }

    #[test]
    fn test_tw_contains_repetitive_text_worst_case_bmh() {
        // "aaaa...aaab" (n=1001) with pattern "aaab".
        // BMH degenerates to O(n×m) on this input; Two-Way runs in O(n).
        let n = 1000usize;
        let mut text: Vec<u8> = (0..n).map(|_| b'a').collect();
        text.push(b'b');
        assert!(tw_contains(&text, b"aaab"), "must find 'aaab' in aaa...ab");
        let text_no_match: Vec<u8> = (0..n).map(|_| b'a').collect();
        assert!(
            !tw_contains(&text_no_match, b"aaab"),
            "must not find 'aaab' in all-a text"
        );
    }

    #[test]
    fn test_tw_contains_agrees_with_bmh() {
        // Property test: tw_contains and bmh_contains must agree on all inputs,
        // including the actual negative_context strings used in NegativeLookahead checks.
        let cases: &[(&[u8], &[u8])] = &[
            (b"foo bar baz", b"bar"),
            (
                b"#[ignore] // https://github.com/foo/bar/issues/1",
                b"https://",
            ),
            (b"#[ignore]", b"https://"),
            (b"", b"x"),
            (b"x", b""),
            (b"https://", b"https://"),
            (b"http://example.com", b"https://"),
            (b"aaaaab", b"aaab"),
            (b"aaaaa", b"aaab"),
        ];
        for (text, pat) in cases {
            assert_eq!(
                tw_contains(text, pat),
                bmh_contains(text, pat),
                "tw_contains and bmh_contains disagree: text={:?} pat={:?}",
                text,
                pat
            );
        }
    }

    #[test]
    fn test_tw_contains_match_at_start() {
        assert!(tw_contains(b"world foo", b"world"));
    }

    #[test]
    fn test_tw_contains_match_at_end() {
        assert!(tw_contains(b"foo world", b"world"));
    }

    #[test]
    fn test_tw_contains_single_char_pattern() {
        assert!(tw_contains(b"abc", b"b"));
        assert!(!tw_contains(b"abc", b"z"));
    }
}

// ── adaptive progress threshold tests ─────────────────────────────────────

mod adaptive_threshold_tests {
    #[test]
    fn test_adaptive_threshold_small_codebase() {
        // 20 total files: threshold = max(5, 20/20) = max(5, 1) = 5
        let total = 20usize;
        let threshold = (total / 20).max(5);
        assert_eq!(threshold, 5);
    }

    #[test]
    fn test_adaptive_threshold_medium_codebase() {
        // 200 total files: threshold = max(5, 200/20) = max(5, 10) = 10
        let total = 200usize;
        let threshold = (total / 20).max(5);
        assert_eq!(threshold, 10);
    }

    #[test]
    fn test_adaptive_threshold_large_codebase() {
        // 2000 total files: threshold = max(5, 2000/20) = max(5, 100) = 100
        let total = 2000usize;
        let threshold = (total / 20).max(5);
        assert_eq!(threshold, 100);
    }

    #[test]
    fn test_adaptive_threshold_zero_files() {
        // 0 total files: threshold = max(5, 0) = 5 (no divide-by-zero risk)
        let total = 0usize;
        let threshold = (total / 20).max(5);
        assert_eq!(threshold, 5);
    }

    #[test]
    fn test_adaptive_threshold_10_files() {
        // 10 files: threshold = max(5, 10/20) = max(5, 0) = 5
        let total = 10usize;
        let threshold = (total / 20).max(5);
        assert_eq!(threshold, 5);
    }

    #[test]
    fn test_adaptive_threshold_150_files() {
        // 150 files: threshold = max(5, 150/20) = max(5, 7) = 7
        let total = 150usize;
        let threshold = (total / 20).max(5);
        assert_eq!(threshold, 7);
    }
}

// ── per-file scan progress tests ──────────────────────────────────────────

mod scan_progress_tests {
    use super::super::{run_native_scan_checks_reporting, MatchMode, NativeScanCheck};
    use std::fs;
    use std::path::PathBuf;
    use std::sync::Mutex;

    fn make_temp_dir(name: &str) -> PathBuf {
        let base = std::env::temp_dir().join(format!("xtask-progress-{name}"));
        let _ = fs::remove_dir_all(&base);
        fs::create_dir_all(&base).unwrap();
        base
    }

    fn write_file(dir: &std::path::Path, rel_path: &str, content: &str) {
        let full = dir.join(rel_path);
        if let Some(parent) = full.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(full, content).unwrap();
    }

    #[test]
    fn test_scan_progress_emitted_every_50_files() {
        // Create 150 files so the 50-file progress callback fires at least 3 times.
        let dir = make_temp_dir("progress-150");
        for i in 0..150 {
            write_file(&dir, &format!("src/file_{i:04}.rs"), "let x = 1;\n");
        }

        let events: Mutex<Vec<String>> = Mutex::new(Vec::new());
        let check = NativeScanCheck {
            name: "test-progress",
            literals: &["forbidden"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|name, info| {
            events.lock().unwrap().push(format!("{name}:{info}"));
        });

        let captured = events.into_inner().unwrap();
        // With 150 files and a boundary every 50, expect at least 3 progress events.
        assert!(
            captured.len() >= 3,
            "expected ≥3 progress events for 150 files, got: {captured:?}"
        );
        // Every event must be prefixed with "native-scan:".
        for event in &captured {
            assert!(
                event.starts_with("native-scan:"),
                "unexpected progress event: {event}"
            );
        }

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_scan_progress_emits_pre_scan_count_for_small_file_count() {
        // For 10 files, pre-scan count events ARE emitted even though the corpus is small.
        // Adaptive threshold = max(5, 10/20) = 5, so per-file events fire at n=5 and n=10.
        let dir = make_temp_dir("progress-small");
        for i in 0..10 {
            write_file(&dir, &format!("src/file_{i}.rs"), "let x = 1;\n");
        }

        let events: Mutex<Vec<String>> = Mutex::new(Vec::new());
        let check = NativeScanCheck {
            name: "test-progress-small",
            literals: &["forbidden"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|name, info| {
            events.lock().unwrap().push(format!("{name}:{info}"));
        });

        let captured = events.into_inner().unwrap();
        // At minimum: "10 files across 1 group(s)" + "  src: 10 files" = 2 pre-scan events.
        assert!(
            captured.len() >= 2,
            "expected at least 2 pre-scan count events for 10 files, got: {captured:?}"
        );
        // All events must use the "native-scan:" prefix.
        for event in &captured {
            assert!(
                event.starts_with("native-scan:"),
                "unexpected progress event: {event}"
            );
        }
        // First event must mention the file count.
        assert!(
            captured[0].contains("10 files"),
            "first event must contain total file count, got: {}",
            captured[0]
        );

        let _ = fs::remove_dir_all(&dir);
    }
}

// ── DiagnosticLevel / scan_has_diagnostic_prefix tests ────────────────────

mod classify_tests {
    use super::super::{scan_has_diagnostic_prefix, DiagnosticLevel};

    #[test]
    fn test_diagnostic_prefix_detects_error_lowercase() {
        let level = scan_has_diagnostic_prefix("error: something went wrong\n");
        assert_eq!(level, DiagnosticLevel::Error);
    }

    #[test]
    fn test_diagnostic_prefix_detects_error_titlecase() {
        let level = scan_has_diagnostic_prefix("Error: something went wrong\n");
        assert_eq!(level, DiagnosticLevel::Error);
    }

    #[test]
    fn test_diagnostic_prefix_detects_rustc_error_bracket_form() {
        // rustc/clippy commonly emit: `error[E0XXX]: ...`
        let level = scan_has_diagnostic_prefix("error[E0425]: cannot find value\n");
        assert_eq!(level, DiagnosticLevel::Error);
    }

    #[test]
    fn test_diagnostic_prefix_detects_warning() {
        let level = scan_has_diagnostic_prefix("warning: unused variable\n");
        assert_eq!(level, DiagnosticLevel::Warning);
    }

    #[test]
    fn test_diagnostic_prefix_detects_warning_tc() {
        // "Warning:" (title-case) is included for completeness.
        let level = scan_has_diagnostic_prefix("Warning: deprecated usage\n");
        assert_eq!(level, DiagnosticLevel::Warning);
    }

    #[test]
    fn test_diagnostic_prefix_detects_warning_bracket_form() {
        // Some toolchains can emit warnings in the bracketed form.
        let level = scan_has_diagnostic_prefix("warning[dead_code]: function is never used\n");
        assert_eq!(level, DiagnosticLevel::Warning);
    }

    #[test]
    fn test_diagnostic_prefix_detects_indented_error() {
        // Leading whitespace before "error:" must still be detected.
        let level = scan_has_diagnostic_prefix("   error: indented error\n");
        assert_eq!(level, DiagnosticLevel::Error);
    }

    #[test]
    fn test_diagnostic_prefix_treats_cr_as_whitespace() {
        // trim_start() treats '\r' as whitespace; our prefix check must match.
        let level = scan_has_diagnostic_prefix("\rerror: windows line ending artifact\n");
        assert_eq!(level, DiagnosticLevel::Error);
    }

    #[test]
    fn test_diagnostic_prefix_clean_output() {
        let level = scan_has_diagnostic_prefix("Compiling foo v0.1.0\nFinished\n");
        assert_eq!(level, DiagnosticLevel::Clean);
    }

    #[test]
    fn test_diagnostic_prefix_error_wins_over_warning() {
        let text = "warning: something\nerror: fatal\n";
        let level = scan_has_diagnostic_prefix(text);
        assert_eq!(level, DiagnosticLevel::Error);
    }

    #[test]
    fn test_diagnostic_prefix_empty_string() {
        let level = scan_has_diagnostic_prefix("");
        assert_eq!(level, DiagnosticLevel::Clean);
    }

    #[test]
    fn test_diagnostic_prefix_mid_line_not_counted() {
        // "error:" that appears after non-whitespace must NOT trigger.
        let level = scan_has_diagnostic_prefix("foo error: bar\n");
        assert_eq!(
            level,
            DiagnosticLevel::Clean,
            "mid-line error: must not trigger"
        );
    }

    #[test]
    fn test_diagnostic_prefix_multiline_only_warning() {
        let text = "Compiling\nwarning: unused\nFinished\n";
        let level = scan_has_diagnostic_prefix(text);
        assert_eq!(level, DiagnosticLevel::Warning);
    }

    #[test]
    fn test_diagnostic_prefix_react_act_warning_is_warning() {
        let text =
            "Warning: An update to Configuration inside a test was not wrapped in act(...)\n";
        let level = scan_has_diagnostic_prefix(text);
        assert_eq!(level, DiagnosticLevel::Warning);
    }

    #[test]
    fn test_diagnostic_level_max_level() {
        assert_eq!(
            DiagnosticLevel::Error.max_level(DiagnosticLevel::Clean),
            DiagnosticLevel::Error
        );
        assert_eq!(
            DiagnosticLevel::Clean.max_level(DiagnosticLevel::Error),
            DiagnosticLevel::Error
        );
        assert_eq!(
            DiagnosticLevel::Warning.max_level(DiagnosticLevel::Clean),
            DiagnosticLevel::Warning
        );
        assert_eq!(
            DiagnosticLevel::Clean.max_level(DiagnosticLevel::Clean),
            DiagnosticLevel::Clean
        );
        assert_eq!(
            DiagnosticLevel::Warning.max_level(DiagnosticLevel::Error),
            DiagnosticLevel::Error
        );
    }

    #[test]
    fn test_scan_has_diagnostic_prefix_consistent_across_repeated_calls() {
        // Verify that the cached OnceLock automaton produces consistent results
        // across many calls (regression guard for OnceLock correctness).
        // Calling 1000 times exercises the cached path after the first construction.
        let inputs = [
            ("error: something bad", DiagnosticLevel::Error),
            ("warning: something mild", DiagnosticLevel::Warning),
            ("  info: no prefix match", DiagnosticLevel::Clean),
            ("", DiagnosticLevel::Clean),
        ];
        for _ in 0..1_000 {
            for (text, expected) in &inputs {
                assert_eq!(
                    scan_has_diagnostic_prefix(text),
                    *expected,
                    "inconsistent result for: {text:?}"
                );
            }
        }
    }
}

// ── DFA builder equivalence tests ─────────────────────────────────────────
//
// Verify that AhoCorasickBuilder with DFA mode produces identical match results
// to the default AhoCorasick::new() construction for all MatchMode variants.

mod dfa_builder_tests {
    use super::super::{run_native_scan_checks_reporting, MatchMode, NativeScanCheck};
    use std::fs;
    use std::path::PathBuf;

    fn make_temp_dir(name: &str) -> PathBuf {
        let base = std::env::temp_dir().join(format!("xtask-dfa-{name}"));
        let _ = fs::remove_dir_all(&base);
        fs::create_dir_all(&base).unwrap();
        base
    }

    fn write_file(dir: &std::path::Path, rel_path: &str, content: &str) {
        let full = dir.join(rel_path);
        if let Some(parent) = full.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(full, content).unwrap();
    }

    /// DFA builder must produce the same violation count as default for AnyLiteral.
    ///
    /// Verifies that the optimised AhoCorasickBuilder::new().kind(DFA) path in
    /// scan_group_collect returns results byte-for-byte identical to what the
    /// default AhoCorasick::new() heuristic produced before the optimisation.
    #[test]
    fn test_dfa_builder_produces_same_results_as_default_for_any_literal() {
        let dir = make_temp_dir("dfa-any-literal");
        write_file(
            &dir,
            "src/lib.rs",
            "let x = forbidden_alpha;\nlet y = safe_code;\nlet z = forbidden_beta;\n",
        );

        let check = NativeScanCheck {
            name: "dfa-any-literal",
            literals: &["forbidden_alpha", "forbidden_beta"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        // Both patterns must be found: 2 violations.
        assert!(
            !results[0].passed,
            "DFA scanner must find forbidden literals"
        );
        assert_eq!(
            results[0].violations.len(),
            2,
            "DFA scanner must find exactly 2 violations, got: {:?}",
            results[0].violations
        );
        assert_eq!(results[0].violations[0].line_number, 1);
        assert_eq!(results[0].violations[1].line_number, 3);

        let _ = fs::remove_dir_all(&dir);
    }

    /// DFA builder must produce the same (absent) results for NegativeLookahead.
    ///
    /// When the negative context is present on the same line, no violation must be
    /// emitted.  This verifies the DFA transition table does not interfere with the
    /// per-match post-filter that checks for the negative context string.
    #[test]
    fn test_dfa_builder_produces_same_results_for_negative_lookahead() {
        let dir = make_temp_dir("dfa-negative-lookahead");
        // Line 1: pattern with negative context → no violation
        // Line 2: pattern without negative context → violation
        write_file(
            &dir,
            "src/lib.rs",
            "is_testing = true // allow-in-test\nis_testing = true\n",
        );

        let check = NativeScanCheck {
            name: "dfa-negative-lookahead",
            literals: &["is_testing"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::NegativeLookahead {
                negative_context: "allow-in-test",
                word_boundary_at_end: false,
            },
        };

        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            !results[0].passed,
            "DFA scanner must find the negative-lookahead violation on line 2"
        );
        assert_eq!(
            results[0].violations.len(),
            1,
            "exactly one violation expected (line 2), got: {:?}",
            results[0].violations
        );
        assert_eq!(results[0].violations[0].line_number, 2);

        let _ = fs::remove_dir_all(&dir);
    }

    /// DFA builder must not panic and must return no violations for an empty pattern list.
    ///
    /// scan_group_collect returns early when all_patterns is empty (before even calling
    /// the builder); this test confirms that code path is reached without panic.
    #[test]
    fn test_dfa_builder_fallback_on_empty_pattern_list() {
        let dir = make_temp_dir("dfa-empty-patterns");
        write_file(&dir, "src/lib.rs", "any content here\n");

        let check = NativeScanCheck {
            name: "dfa-empty-patterns",
            literals: &[], // intentionally empty
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: MatchMode::AnyLiteral {
                skip_comment_lines: false,
            },
        };

        // Must not panic; an empty pattern list returns Pass immediately.
        let results =
            run_native_scan_checks_reporting(&dir, std::slice::from_ref(&check), &|_, _| {});
        assert!(
            results[0].passed,
            "empty pattern list must produce no violations"
        );

        let _ = fs::remove_dir_all(&dir);
    }
}

// ── collect_scan_groups tests ─────────────────────────────────────────────

#[test]
fn test_collect_scan_groups_returns_sorted_files() {
    // Verify that collect_scan_groups returns files in sorted order for a
    // single group.  Files are created in reverse alphabetical order to
    // confirm the sort is applied by the function, not by the OS.
    let dir = make_temp_dir("collect-groups-sorted");
    write_file(&dir, "src/c.rs", "// c");
    write_file(&dir, "src/a.rs", "// a");
    write_file(&dir, "src/b.rs", "// b");

    let check = NativeScanCheck {
        name: "test",
        literals: &["x"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let groups = collect_scan_groups(&dir, &[check]);
    assert_eq!(groups.len(), 1, "one group expected");
    let (_, (_, result)) = groups.into_iter().next().unwrap();
    let files = result.expect("traversal must succeed");
    assert_eq!(files.len(), 3, "three files expected");
    // Verify ascending sort order.
    for i in 1..files.len() {
        assert!(
            files[i - 1] <= files[i],
            "files must be in sorted order, got: {files:?}"
        );
    }

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_collect_scan_groups_deduplicates_groups() {
    // Two checks sharing the same directory → collect_scan_groups must produce
    // exactly one group entry (single traversal, not two).
    let dir = make_temp_dir("collect-groups-dedup");
    write_file(&dir, "src/a.rs", "// a");

    let check1 = NativeScanCheck {
        name: "check1",
        literals: &["x"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };
    let check2 = NativeScanCheck {
        name: "check2",
        literals: &["y"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let groups = collect_scan_groups(&dir, &[check1, check2]);
    assert_eq!(
        groups.len(),
        1,
        "two checks sharing the same directory must produce exactly one group"
    );
    let (_, (_, result)) = groups.into_iter().next().unwrap();
    let files = result.expect("traversal must succeed");
    assert_eq!(files.len(), 1, "one file expected");

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_collect_scan_groups_separate_directories_produce_separate_groups() {
    // Two checks with different directories → two group entries.
    let dir = make_temp_dir("collect-groups-two");
    write_file(&dir, "src/a.rs", "// a");
    write_file(&dir, "tests/b.rs", "// b");

    let check1 = NativeScanCheck {
        name: "check1",
        literals: &["x"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };
    let check2 = NativeScanCheck {
        name: "check2",
        literals: &["y"],
        directories: &["tests"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let groups = collect_scan_groups(&dir, &[check1, check2]);
    assert_eq!(
        groups.len(),
        2,
        "two checks with different directories must produce two groups"
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_collect_scan_groups_skips_excluded_transient_frontend_directories() {
    let dir = make_temp_dir("collect-groups-skip-transient-frontend");
    write_file(&dir, "ralph-gui/build.rs", "fn main() {}\n");
    write_file(&dir, "ralph-gui/src/lib.rs", "pub fn gui() {}\n");
    write_file(
        &dir,
        "ralph-gui/ui/node_modules/pkg/index.rs",
        "#[allow(clippy::all)]\n",
    );
    write_file(
        &dir,
        "ralph-gui/ui/dist/generated.rs",
        "#[allow(clippy::all)]\n",
    );

    let check = NativeScanCheck {
        name: "forbidden-allow-expect-scan",
        literals: &["#[allow("],
        directories: &["ralph-gui"],
        include_glob: "*.rs",
        exclude_globs: &["**/node_modules/**", "**/dist/**"],
        mode: MatchMode::AnyLiteralAtLineStart {
            skip_comment_lines: true,
        },
    };

    let groups = collect_scan_groups(&dir, &[check]);
    let (_, (_, result)) = groups.into_iter().next().unwrap();
    let files = result.expect("traversal must succeed");

    assert!(
        files
            .iter()
            .all(|path| !path.to_string_lossy().contains("node_modules")),
        "excluded node_modules files must not be traversed: {files:?}"
    );
    assert!(
        files
            .iter()
            .all(|path| !path.to_string_lossy().contains("/dist/")),
        "excluded dist files must not be traversed: {files:?}"
    );
    assert!(
        files
            .iter()
            .any(|path| path.ends_with(Path::new("ralph-gui/build.rs"))),
        "stable Rust-owned build.rs must still be scanned"
    );
    assert!(
        files
            .iter()
            .any(|path| path.ends_with(Path::new("ralph-gui/src/lib.rs"))),
        "stable GUI Rust sources must still be scanned"
    );

    let _ = fs::remove_dir_all(&dir);
}

// ── tw_contains_precomputed tests ─────────────────────────────────────────

/// Cross-validate `tw_contains_precomputed` against the `tw_contains` baseline.
///
/// Tests a broad set of inputs including adversarial periodic patterns that trigger
/// the Case 1 (memory-based) branch of the Two-Way algorithm, ensuring the
/// precomputed variant is bit-for-bit identical to the full preprocessing version.
///
/// Reference: TAOCP Vol. 3, §6.3 — preprocessing amortization principle.
#[test]
fn test_tw_contains_precomputed_matches_tw_contains() {
    let fixed_cases: &[(&[u8], &[u8])] = &[
        // Basic matching
        (b"hello", b"hello"),
        (b"hello world", b"world"),
        (b"hello world", b"hello"),
        (b"hello world", b"xyz"),
        // Exact same as text
        (b"abc", b"abc"),
        // Pattern at start / end
        (b"abc", b"ab"),
        (b"abc", b"bc"),
        // Single-char patterns
        (b"a", b"a"),
        (b"a", b"b"),
        (b"b", b"b"),
        // Pattern longer than text
        (b"ab", b"abcdef"),
        // Realistic NegativeLookahead inputs
        (b"#[ignore] // https://example.com", b"https://"),
        (b"#[ignore]", b"https://"),
        (b"#[ignore(reason = \"https://foo.com\")]", b"https://"),
        // Non-ASCII safe (treat as raw bytes)
        (b"caf\xc3\xa9", b"caf\xc3"),
    ];

    // Adversarial periodic patterns: trigger Case 1 in Two-Way.
    // BMH degenerates to O(n×m) on these; Two-Way guarantees O(n).
    let long_a: Vec<u8> = vec![b'a'; 100];
    let mut aaab_99 = vec![b'a'; 99];
    aaab_99.push(b'b');
    let mut aaab_4 = vec![b'a'; 3];
    aaab_4.push(b'b');

    let adversarial: Vec<(Vec<u8>, Vec<u8>)> = vec![
        // text = "aaa...ab" (100 a's + b), pattern = "aaab" → matches at the end
        (long_a.iter().chain(b"b").copied().collect(), aaab_4.clone()),
        // text = "aaa...ab" (99 a's + b), pattern = "aaa...ab" (99 a's + b) → exact match
        (aaab_99.clone(), aaab_99.clone()),
        // text = "aaa...ab" (99 a's + b), pattern = "aaa...ac" (99 a's + c) → no match
        (aaab_99.clone(), {
            let mut p = vec![b'a'; 99];
            p.push(b'c');
            p
        }),
        // Periodic pattern "abab" in "ababababab"
        (b"ababababab".to_vec(), b"abab".to_vec()),
        (b"ababababab".to_vec(), b"abcd".to_vec()),
        // Single repeated char
        (vec![b'a'; 50], vec![b'a'; 10]),
        (vec![b'a'; 50], vec![b'a'; 51]),
    ];

    // Run fixed cases.
    for &(text, pattern) in fixed_cases {
        let expected = tw_contains(text, pattern);
        let precomputed = critical_factorization(pattern);
        let got = tw_contains_precomputed(text, pattern, precomputed);
        assert_eq!(
            got, expected,
            "tw_contains_precomputed mismatch: text={:?} pattern={:?}",
            text, pattern
        );
    }

    // Run adversarial cases.
    for (text, pattern) in &adversarial {
        let expected = tw_contains(text, pattern);
        let precomputed = critical_factorization(pattern);
        let got = tw_contains_precomputed(text, pattern, precomputed);
        assert_eq!(
            got,
            expected,
            "tw_contains_precomputed mismatch (adversarial): text_len={} pattern_len={}",
            text.len(),
            pattern.len()
        );
    }
}

/// Empty pattern must return true (same contract as tw_contains).
#[test]
fn test_tw_contains_precomputed_empty_pattern_returns_true() {
    // tw_contains_precomputed skips the precomputed path for empty patterns.
    // Use critical_factorization on a non-empty dummy pattern for the call.
    let precomputed = critical_factorization(b"x");
    assert!(
        tw_contains_precomputed(b"anything", &[], precomputed),
        "empty pattern must always match"
    );
    assert!(
        tw_contains_precomputed(&[], &[], precomputed),
        "empty pattern in empty text must match"
    );
}

/// Pattern longer than text must return false.
#[test]
fn test_tw_contains_precomputed_pattern_longer_than_text_returns_false() {
    let pattern = b"longer";
    let text = b"short";
    let precomputed = critical_factorization(pattern);
    assert!(
        !tw_contains_precomputed(text, pattern, precomputed),
        "pattern longer than text must not match"
    );
}
