//! Tests for the CachingCommandRunner.

use super::*;
use crate::io::fingerprint::compute_scope_hash;
use crate::runtime::verify::{CheckStatus, NativeCheck, NativeCheckResult};
use serde_json;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

static TEST_DIR_COUNTER: AtomicUsize = AtomicUsize::new(0);

fn unique_test_dir(prefix: &str) -> std::path::PathBuf {
    let id = TEST_DIR_COUNTER.fetch_add(1, Ordering::Relaxed);
    std::env::temp_dir().join(format!("{prefix}-{}-{id}", std::process::id()))
}

/// A runner that records call count and returns a preset output.
struct CountingRunner {
    call_count: Arc<AtomicUsize>,
    output: CommandOutput,
}

impl CountingRunner {
    fn new(output: CommandOutput) -> (Self, Arc<AtomicUsize>) {
        let count = Arc::new(AtomicUsize::new(0));
        (
            Self {
                call_count: Arc::clone(&count),
                output,
            },
            count,
        )
    }
}

impl CommandRunner for CountingRunner {
    fn run(&self, _spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        self.call_count.fetch_add(1, Ordering::SeqCst);
        Ok(self.output.clone())
    }
}

struct NativeCheckCountingRunner {
    call_count: Arc<AtomicUsize>,
    status: CheckStatus,
    message: String,
}

impl NativeCheckCountingRunner {
    fn new(status: CheckStatus, message: impl Into<String>) -> (Self, Arc<AtomicUsize>) {
        let counter = Arc::new(AtomicUsize::new(0));
        (
            Self {
                call_count: Arc::clone(&counter),
                status,
                message: message.into(),
            },
            counter,
        )
    }
}

impl CommandRunner for NativeCheckCountingRunner {
    fn run(&self, _spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        Ok(CommandOutput {
            exit_code: 0,
            stdout: String::new(),
            stderr: String::new(),
        })
    }

    fn run_native_check(
        &self,
        _repo_root: &std::path::Path,
        _check: &NativeCheck,
    ) -> std::io::Result<NativeCheckResult> {
        self.call_count.fetch_add(1, Ordering::SeqCst);
        Ok(NativeCheckResult {
            status: self.status,
            message: self.message.clone(),
        })
    }
}

fn make_spec(name: &'static str) -> CommandSpec {
    CommandSpec {
        name,
        program: "rg",
        args: &[],
        success_exit_codes: &[1],
        extra_env: &[],
    }
}

fn success_output() -> CommandOutput {
    CommandOutput {
        exit_code: 1, // exit 1 = no matches = success for rg checks
        stdout: String::new(),
        stderr: String::new(),
    }
}

fn failure_output() -> CommandOutput {
    CommandOutput {
        exit_code: 0, // exit 0 = matches found = failure for rg checks
        stdout: "match found".to_string(),
        stderr: String::new(),
    }
}

#[test]
fn test_persist_writes_cache_file() -> std::io::Result<()> {
    let tmp = unique_test_dir("xtask-cache-persist");
    let _ = std::fs::create_dir_all(&tmp);

    let (inner, _) = CountingRunner::new(success_output());
    let runner = CachingCommandRunner::new(inner, tmp.clone());

    {
        let mut memory = runner.memory.lock().unwrap();
        memory.insert(
            "persist-test".to_string(),
            CacheEntry {
                scope_hash: 123,
                exit_code: 0,
                stdout: "generated".to_string(),
                stderr: "info".to_string(),
            },
        );
    }

    runner.persist()?;

    let cache_path = tmp.join("target/xtask-verify-cache.json");
    let data = std::fs::read_to_string(&cache_path)?;
    let cache_file: CacheFile = serde_json::from_str(&data)?;

    if cache_file.entries.len() != 1 {
        return Err(std::io::Error::other(format!(
            "expected exactly one entry but found {}",
            cache_file.entries.len()
        )));
    }

    let entry = cache_file.entries.get("persist-test").ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::NotFound, "persisted entry missing")
    })?;

    if entry.scope_hash != 123 {
        return Err(std::io::Error::other(format!(
            "expected scope hash 123 but got {}",
            entry.scope_hash
        )));
    }

    if entry.stdout != "generated" {
        return Err(std::io::Error::other(format!(
            "expected stdout 'generated' but got {:?}",
            entry.stdout
        )));
    }

    if entry.stderr != "info" {
        return Err(std::io::Error::other(format!(
            "expected stderr 'info' but got {:?}",
            entry.stderr
        )));
    }

    let _ = std::fs::remove_dir_all(&tmp);
    Ok(())
}

#[test]
fn test_caching_runner_skips_check_on_cache_hit() {
    // Use a temp directory so compute_scope_hash works without real repo files.
    let tmp = unique_test_dir("xtask-cache-test-hit");
    let _ = std::fs::create_dir_all(&tmp);

    let spec = make_spec("no-test-flags-cfg-test");

    let (inner, count) = CountingRunner::new(success_output());
    let runner = CachingCommandRunner::new(inner, tmp.clone());

    // First call: cache miss, inner runner is called.
    let _ = runner.run(&spec).unwrap();
    assert_eq!(
        count.load(Ordering::SeqCst),
        1,
        "first call must invoke inner runner"
    );

    // Second call with same state: cache hit, inner runner is NOT called again.
    let _ = runner.run(&spec).unwrap();
    assert_eq!(
        count.load(Ordering::SeqCst),
        1,
        "second call must be served from cache"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_caching_runner_reruns_check_on_cache_miss() {
    let tmp = unique_test_dir("xtask-cache-test-miss");
    let _ = std::fs::create_dir_all(&tmp);

    let spec = make_spec("no-test-flags-cfg-test");

    let (inner, count) = CountingRunner::new(success_output());
    let runner = CachingCommandRunner::new(inner, tmp.clone());

    // Prime cache with a wrong hash by injecting a stale entry.
    {
        let mut mem = runner.memory.lock().unwrap();
        let stale_key = format!("{}:{}", spec.name, 0u64); // hash 0 will never match
        mem.insert(
            stale_key,
            CacheEntry {
                scope_hash: 0,
                exit_code: 1,
                stdout: String::new(),
                stderr: String::new(),
            },
        );
    }

    // Call: no entry for the real hash, so inner runner must be called.
    let _ = runner.run(&spec).unwrap();
    assert_eq!(
        count.load(Ordering::SeqCst),
        1,
        "inner runner must be called on cache miss"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_caching_runner_does_not_cache_failures() {
    let tmp = unique_test_dir("xtask-cache-test-no-failure-cache");
    let _ = std::fs::create_dir_all(&tmp);

    let spec = make_spec("no-test-flags-cfg-test");

    let (inner, count) = CountingRunner::new(failure_output());
    let runner = CachingCommandRunner::new(inner, tmp.clone());

    // First call: failure, must NOT be cached.
    let out1 = runner.run(&spec).unwrap();
    assert_eq!(out1.exit_code, 0, "first call returns failure output");
    assert_eq!(count.load(Ordering::SeqCst), 1);

    // Second call: failure again, inner runner must be called again (not cached).
    let out2 = runner.run(&spec).unwrap();
    assert_eq!(out2.exit_code, 0, "second call returns failure output");
    assert_eq!(
        count.load(Ordering::SeqCst),
        2,
        "failures must not be cached; inner runner called again"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

// ── Granular scope tests ──────────────────────────────────────────────────

#[test]
fn test_scope_for_clippy_core_is_granular() {
    match scope_for("clippy-core") {
        CheckScope::BuildWithExtras { dirs, globs, files } => {
            assert_eq!(dirs, &["ralph-workflow/src", "tests", "test-helpers/src"]);
            assert!(
                globs
                    .iter()
                    .any(|glob| glob.dir == "templates/prompts" && glob.pattern == "*"),
                "clippy-core must track embedded prompt markdown files consumed by ralph-workflow"
            );
            assert!(
                globs.iter().any(|glob| {
                    glob.dir == "ralph-workflow/src/prompts/templates" && glob.pattern == "*"
                }),
                "clippy-core must track embedded prompt template text files consumed by ralph-workflow"
            );
            assert!(
                files.is_empty(),
                "clippy-core should track compile-time resources via directory extras"
            );
        }
        CheckScope::Directories(_) | CheckScope::Build(_) | CheckScope::Patterns { .. } => {
            panic!("clippy-core must use BuildWithExtras scope")
        }
    }
}

#[test]
fn test_scope_for_clippy_xtask_is_granular() {
    let key = scope_memo_key(&scope_for("clippy-xtask"));
    assert_eq!(key, "b:xtask/src");
}

#[test]
fn test_scope_for_test_xtask_is_granular() {
    let key = scope_memo_key(&scope_for("test-xtask"));
    assert_eq!(key, "b:xtask/src");
}

#[test]
fn test_scope_for_test_integration_tracks_compile_time_artifacts() {
    match scope_for("test-integration") {
        CheckScope::BuildWithExtras { dirs, globs, files } => {
            assert_eq!(
                dirs,
                &[
                    "ralph-workflow/src",
                    "tests/integration_tests",
                    "test-helpers/src"
                ]
            );
            assert!(
                globs.iter().any(|glob| {
                    glob.dir == "tests/integration_tests/artifacts" && glob.pattern == "*"
                }),
                "integration test scope must track compile-time fixtures included from tests/integration_tests/artifacts"
            );
            assert!(
                globs
                    .iter()
                    .any(|glob| glob.dir == "templates/prompts" && glob.pattern == "*"),
                "integration test scope must track embedded prompt markdown files consumed by ralph-workflow"
            );
            assert!(
                globs.iter().any(|glob| {
                    glob.dir == "ralph-workflow/src/prompts/templates" && glob.pattern == "*"
                }),
                "integration test scope must track embedded prompt template text files consumed by ralph-workflow"
            );
            assert!(
                files.is_empty(),
                "integration test scope should use directory extras instead of ad hoc files"
            );
        }
        CheckScope::Directories(_) | CheckScope::Build(_) | CheckScope::Patterns { .. } => {
            panic!("test-integration must use BuildWithExtras scope")
        }
    }
}

#[test]
fn test_scope_for_fmt_check_uses_directories_not_build() {
    match scope_for("fmt-check") {
        CheckScope::Patterns {
            globs,
            files,
            include_lock,
        } => {
            assert!(
                globs
                    .iter()
                    .any(|glob| glob.dir == "ralph-workflow/src" && glob.pattern == "*.rs"),
                "fmt-check must scan ralph-workflow/src"
            );
            assert!(
                globs
                    .iter()
                    .any(|glob| glob.dir == "tests" && glob.pattern == "*.rs"),
                "fmt-check must scan tests"
            );
            assert!(
                globs
                    .iter()
                    .any(|glob| glob.dir == "xtask/src" && glob.pattern == "*.rs"),
                "fmt-check must scan xtask/src"
            );
            assert!(
                globs
                    .iter()
                    .any(|glob| glob.dir == "test-helpers/src" && glob.pattern == "*.rs"),
                "fmt-check must scan test-helpers/src"
            );
            assert!(files.is_empty(), "fmt-check should have no extra files");
            assert!(!include_lock, "fmt-check should not depend on Cargo.lock");
        }
        CheckScope::Directories(_) | CheckScope::Build(_) | CheckScope::BuildWithExtras { .. } => {
            panic!("fmt-check must use a stable Patterns scope")
        }
    }
}

#[test]
fn test_scope_for_forbidden_allow_expect_scan_covers_all_scanned_rust_trees() {
    match scope_for("forbidden-allow-expect-scan") {
        CheckScope::Patterns {
            globs,
            files,
            include_lock,
        } => {
            assert!(
                globs
                    .iter()
                    .any(|glob| glob.dir == "ralph-workflow/src" && glob.pattern == "*.rs"),
                "forbidden allow/expect scan must cover ralph-workflow/src"
            );
            assert!(
                globs
                    .iter()
                    .any(|glob| glob.dir == "tests" && glob.pattern == "*.rs"),
                "forbidden allow/expect scan must cover tests"
            );
            assert!(
                globs
                    .iter()
                    .any(|glob| glob.dir == "xtask/src" && glob.pattern == "*.rs"),
                "forbidden allow/expect scan must cover xtask/src"
            );
            assert!(
                globs
                    .iter()
                    .any(|glob| glob.dir == "test-helpers/src" && glob.pattern == "*.rs"),
                "forbidden allow/expect scan must cover test-helpers/src"
            );
            assert!(
                globs
                    .iter()
                    .any(|glob| glob.dir == "lints" && glob.pattern == "*.rs"),
                "forbidden allow/expect scan must cover lints"
            );
            assert!(
                files.is_empty(),
                "forbidden allow/expect scan should have no extra files"
            );
            assert!(
                !include_lock,
                "forbidden allow/expect scan should not depend on Cargo.lock"
            );
        }
        CheckScope::Directories(_) | CheckScope::Build(_) | CheckScope::BuildWithExtras { .. } => {
            panic!("forbidden-allow-expect-scan must use a stable Patterns scope")
        }
    }
}

#[test]
fn test_scope_for_dylint_tracks_custom_lint_inputs() {
    match scope_for("dylint") {
        CheckScope::Patterns {
            globs,
            files,
            include_lock,
        } => {
            assert!(
                globs
                    .iter()
                    .any(|glob| { glob.dir == "ralph-workflow/src" && glob.pattern == "*.rs" }),
                "dylint must still include ralph-workflow Rust sources"
            );
            assert!(
                globs
                    .iter()
                    .any(|glob| { glob.dir == "lints/ralph_lints/src" && glob.pattern == "*.rs" }),
                "dylint must include the custom lint crate sources"
            );
            for required_file in [
                "Makefile",
                "lints/ralph_lints/Cargo.toml",
                "lints/ralph_lints/Cargo.lock",
                "lints/ralph_lints/.cargo/config.toml",
                "lints/ralph_lints/rust-toolchain.toml",
                "lints/ralph_lints/dylint-link",
                "lints/ralph_lints/rustc-nightly",
            ] {
                assert!(
                    files.contains(&required_file),
                    "dylint must track {required_file} because make dylint depends on it"
                );
            }
            assert!(
                !include_lock,
                "dylint uses explicit files for its lockfiles instead of the workspace lock toggle"
            );
        }
        CheckScope::Directories(_) | CheckScope::Build(_) | CheckScope::BuildWithExtras { .. } => {
            panic!("dylint must use a dedicated Patterns scope")
        }
    }
}

#[test]
fn test_scope_for_test_ralph_workflow_lib_tracks_ralph_workflow_compile_time_resources() {
    match scope_for("test-ralph-workflow-lib") {
        CheckScope::BuildWithExtras { dirs, globs, files } => {
            assert_eq!(dirs, &["ralph-workflow/src"]);
            assert!(
                globs
                    .iter()
                    .any(|glob| glob.dir == "templates/prompts" && glob.pattern == "*"),
                "ralph-workflow lib tests must track embedded prompt markdown files"
            );
            assert!(
                globs.iter().any(|glob| {
                    glob.dir == "ralph-workflow/src/prompts/templates" && glob.pattern == "*"
                }),
                "ralph-workflow lib tests must track embedded prompt template text files"
            );
            assert!(files.is_empty());
        }
        CheckScope::Directories(_) | CheckScope::Build(_) | CheckScope::Patterns { .. } => {
            panic!("test-ralph-workflow-lib must use BuildWithExtras scope")
        }
    }
}

#[test]
fn test_scope_for_release_build_tracks_ralph_workflow_compile_time_resources() {
    match scope_for("release-build") {
        CheckScope::BuildWithExtras { dirs, globs, files } => {
            assert_eq!(
                dirs,
                &["ralph-workflow/src", "test-helpers/src", "xtask/src"]
            );
            assert!(
                globs
                    .iter()
                    .any(|glob| glob.dir == "templates/prompts" && glob.pattern == "*"),
                "release-build must track embedded prompt markdown files consumed by ralph-workflow"
            );
            assert!(
                globs.iter().any(|glob| {
                    glob.dir == "ralph-workflow/src/prompts/templates" && glob.pattern == "*"
                }),
                "release-build must track embedded prompt template text files consumed by ralph-workflow"
            );
            assert!(files.is_empty());
        }
        CheckScope::Directories(_) | CheckScope::Build(_) | CheckScope::Patterns { .. } => {
            panic!("release-build must use BuildWithExtras scope")
        }
    }
}

#[test]
fn test_scope_for_release_build_tracks_default_members_only() {
    match scope_for("release-build") {
        CheckScope::BuildWithExtras { dirs, .. } => {
            assert_eq!(
                dirs,
                &["ralph-workflow/src", "test-helpers/src", "xtask/src"]
            );
        }
        CheckScope::Directories(_) | CheckScope::Build(_) | CheckScope::Patterns { .. } => {
            panic!("release-build must keep tracking workspace default members via BuildWithExtras")
        }
    }
}

#[test]
fn test_scope_memo_key_is_stable() {
    // Same scope must always produce the same key.
    let k1 = scope_memo_key(&CheckScope::Build(&["ralph-workflow/src"]));
    let k2 = scope_memo_key(&CheckScope::Build(&["ralph-workflow/src"]));
    assert_eq!(k1, k2);

    let k3 = scope_memo_key(&CheckScope::Directories(&["ralph-workflow/src"]));
    // Build and Directories keys for same dirs must differ.
    assert_ne!(k1, k3);
}

#[test]
fn test_scope_memo_deduplicates_traversals() {
    // Two checks that share the same scope should only traverse directories once.
    // We verify this by checking that both checks produce the same hash AND
    // that after the first run the scope_memo is populated.
    let tmp = unique_test_dir("xtask-cache-test-scope-memo");
    let _ = std::fs::create_dir_all(&tmp);

    let (inner, _count) = CountingRunner::new(success_output());
    let runner = CachingCommandRunner::new(inner, tmp.clone());

    // clippy-xtask and test-xtask share the same scope: Build(&["xtask/src"])
    let scope1 = scope_for("clippy-xtask");
    let scope2 = scope_for("test-xtask");
    let key1 = scope_memo_key(&scope1);
    let key2 = scope_memo_key(&scope2);
    assert_eq!(
        key1, key2,
        "clippy-xtask and test-xtask must share the same scope key"
    );

    // First hash computation populates the memo.
    let h1 = runner
        .compute_or_cached_scope_hash(&scope_for("clippy-xtask"))
        .expect("scope hash should be computable in test");
    {
        let memo = runner.scope_memo.lock().unwrap();
        assert!(
            memo.contains_key(&key1),
            "scope memo must be populated after first hash"
        );
    }

    // Second computation for same key returns same hash from memo.
    let h2 = runner
        .compute_or_cached_scope_hash(&scope_for("test-xtask"))
        .expect("scope hash should be computable in test");
    assert_eq!(h1, h2, "same scope must produce same hash");

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_compute_scope_hash_with_snapshot_reuses_glob_collection_for_shared_scope() {
    let tmp = unique_test_dir("xtask-cache-test-shared-snapshot");
    let _ = std::fs::remove_dir_all(&tmp);
    let _ = std::fs::create_dir_all(tmp.join("xtask/src"));
    std::fs::write(tmp.join("xtask/src/lib.rs"), b"pub fn xtask() {}\n").unwrap();
    std::fs::write(
        tmp.join("xtask/Cargo.toml"),
        b"[package]\nname = \"xtask\"\nversion = \"0.1.0\"\n",
    )
    .unwrap();
    std::fs::write(
        tmp.join("Cargo.toml"),
        b"[workspace]\nmembers = [\"xtask\"]\n",
    )
    .unwrap();
    std::fs::write(tmp.join("Cargo.lock"), b"# lock\n").unwrap();

    let snapshot = RepositoryFingerprintCache::default();
    let first = compute_scope_hash_with_snapshot(&tmp, &scope_for("clippy-xtask"), &snapshot)
        .expect("first hash should succeed");
    let second = compute_scope_hash_with_snapshot(&tmp, &scope_for("test-xtask"), &snapshot)
        .expect("second hash should succeed");

    assert_eq!(first, second, "same shared scope must hash identically");
    assert_eq!(
        snapshot.glob_memo.lock().unwrap().len(),
        1,
        "shared scope hashing should memoize one directory walk for both xtask checks"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_compute_scope_hash_with_snapshot_reuses_file_bytes_across_calls() {
    let tmp = unique_test_dir("xtask-cache-test-shared-file-bytes");
    let _ = std::fs::remove_dir_all(&tmp);
    let _ = std::fs::create_dir_all(tmp.join("src"));
    std::fs::write(tmp.join("src/lib.rs"), b"pub fn demo() {}\n").unwrap();

    let snapshot = RepositoryFingerprintCache::default();
    let _ = compute_scope_hash_with_snapshot(&tmp, &CheckScope::Directories(&["src"]), &snapshot)
        .expect("first hash should succeed");
    let file_count_after_first = snapshot.file_fingerprints.lock().unwrap().len();
    let _ = compute_scope_hash_with_snapshot(&tmp, &CheckScope::Directories(&["src"]), &snapshot)
        .expect("second hash should succeed");
    let file_count_after_second = snapshot.file_fingerprints.lock().unwrap().len();

    assert!(
        file_count_after_first > 0,
        "snapshot hashing should memoize file bytes after the first call"
    );
    assert_eq!(
        file_count_after_first, file_count_after_second,
        "second hash should reuse file bytes instead of rereading the same files"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_native_scan_clean_result_is_cached_for_unchanged_run() {
    let tmp = unique_test_dir("xtask-cache-test-native-scan-cache");
    let _ = std::fs::remove_dir_all(&tmp);
    let _ = std::fs::create_dir_all(tmp.join("src"));
    let _ = std::fs::create_dir_all(tmp.join("target"));
    std::fs::write(tmp.join("src/lib.rs"), b"pub fn ok() {}\n").unwrap();

    let check = crate::io::scanner::NativeScanCheck {
        name: "native-scan-cache-test",
        literals: &["definitely_missing_literal"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: crate::io::scanner::MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let (inner, _count) = CountingRunner::new(success_output());
    let runner = CachingCommandRunner::new(inner, tmp.clone());

    let first_progress = Mutex::new(Vec::new());
    let first = runner
        .run_native_scan(&tmp, std::slice::from_ref(&check), &|name, info| {
            first_progress
                .lock()
                .unwrap()
                .push(format!("{name}:{info}"));
        })
        .expect("first native scan should succeed");
    let second_progress = Mutex::new(Vec::new());
    let second = runner
        .run_native_scan(&tmp, std::slice::from_ref(&check), &|name, info| {
            second_progress
                .lock()
                .unwrap()
                .push(format!("{name}:{info}"));
        })
        .expect("second native scan should succeed");

    assert!(first[0].passed, "first native scan should pass");
    assert!(second[0].passed, "second native scan should pass");
    assert!(
        !first_progress
            .lock()
            .unwrap()
            .iter()
            .any(|entry| entry.contains("cache hit")),
        "cold native scan should not report a cache hit"
    );
    assert!(
        second_progress
            .lock()
            .unwrap()
            .iter()
            .any(|entry| entry.contains("cache hit")),
        "unchanged native scan should report a cache hit on the warm run"
    );
    assert!(
        runner
            .memory
            .lock()
            .unwrap()
            .keys()
            .any(|key| key.starts_with("native-scan:")),
        "clean native scan results should be stored in the verify cache"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_native_scan_cache_invalidates_when_relevant_file_changes() {
    let tmp = unique_test_dir("xtask-cache-test-native-scan-invalidation");
    let _ = std::fs::remove_dir_all(&tmp);
    let _ = std::fs::create_dir_all(tmp.join("src"));
    let _ = std::fs::create_dir_all(tmp.join("target"));
    std::fs::write(tmp.join("src/lib.rs"), b"pub fn ok() {}\n").unwrap();

    let check = crate::io::scanner::NativeScanCheck {
        name: "native-scan-cache-invalidation",
        literals: &["blocked_literal"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: crate::io::scanner::MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let (inner, _count) = CountingRunner::new(success_output());
    let runner = CachingCommandRunner::new(inner, tmp.clone());

    let first = runner
        .run_native_scan(&tmp, std::slice::from_ref(&check), &|_, _| {})
        .expect("first native scan should succeed");
    assert!(first[0].passed, "first native scan should pass");

    std::fs::write(tmp.join("src/lib.rs"), b"pub fn blocked_literal() {}\n").unwrap();

    let second_progress = Mutex::new(Vec::new());
    let second = runner
        .run_native_scan(&tmp, std::slice::from_ref(&check), &|name, info| {
            second_progress
                .lock()
                .unwrap()
                .push(format!("{name}:{info}"));
        })
        .expect("second native scan should succeed");

    assert!(
        !second[0].passed,
        "relevant file changes must invalidate the cached clean native scan result"
    );
    assert!(
        !second_progress
            .lock()
            .unwrap()
            .iter()
            .any(|entry| entry.contains("cache hit")),
        "native scan must not report a cache hit after relevant file content changes"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_native_scan_cache_ignores_irrelevant_file_changes() {
    let tmp = unique_test_dir("xtask-cache-test-native-scan-irrelevant-change");
    let _ = std::fs::remove_dir_all(&tmp);
    let _ = std::fs::create_dir_all(tmp.join("src"));
    let _ = std::fs::create_dir_all(tmp.join("docs"));
    let _ = std::fs::create_dir_all(tmp.join("target"));
    std::fs::write(tmp.join("src/lib.rs"), b"pub fn ok() {}\n").unwrap();
    std::fs::write(tmp.join("docs/readme.md"), b"first\n").unwrap();

    let check = crate::io::scanner::NativeScanCheck {
        name: "native-scan-cache-irrelevant-change",
        literals: &["blocked_literal"],
        directories: &["src"],
        include_glob: "*.rs",
        exclude_globs: &[],
        mode: crate::io::scanner::MatchMode::AnyLiteral {
            skip_comment_lines: false,
        },
    };

    let (inner, _count) = CountingRunner::new(success_output());
    let runner = CachingCommandRunner::new(inner, tmp.clone());

    let first = runner
        .run_native_scan(&tmp, std::slice::from_ref(&check), &|_, _| {})
        .expect("first native scan should succeed");
    assert!(first[0].passed, "first native scan should pass");

    std::fs::write(tmp.join("docs/readme.md"), b"second\n").unwrap();

    let second_progress = Mutex::new(Vec::new());
    let second = runner
        .run_native_scan(&tmp, std::slice::from_ref(&check), &|name, info| {
            second_progress
                .lock()
                .unwrap()
                .push(format!("{name}:{info}"));
        })
        .expect("second native scan should succeed");

    assert!(
        second[0].passed,
        "irrelevant file changes must preserve the cached clean native scan result"
    );
    assert!(
        second_progress
            .lock()
            .unwrap()
            .iter()
            .any(|entry| entry.contains("cache hit")),
        "native scan should report a cache hit after irrelevant file changes"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_native_scan_handles_empty_checks() {
    let tmp = unique_test_dir("xtask-cache-test-native-scan-empty-checks");
    let _ = std::fs::remove_dir_all(&tmp);
    let (inner, _) = CountingRunner::new(success_output());
    let runner = CachingCommandRunner::new(inner, tmp.clone());

    let results = runner
        .run_native_scan(&tmp, &[], &|_, _| {
            panic!("native scan should not report progress when no checks are provided")
        })
        .expect("native scan should tolerate empty check lists");

    assert!(
        results.is_empty(),
        "no checks should return an empty result set"
    );
    let _ = std::fs::remove_dir_all(&tmp);
}

fn dummy_native_check_run(_repo_root: &std::path::Path) -> NativeCheckResult {
    NativeCheckResult {
        status: CheckStatus::Pass,
        message: "native-check".to_string(),
    }
}

#[test]
fn test_native_check_caches_successful_results() {
    let repo_root = std::env::current_dir().unwrap();
    let (inner, count) = NativeCheckCountingRunner::new(CheckStatus::Pass, "native-check");
    let runner = CachingCommandRunner::new(inner, repo_root.clone());

    let check = NativeCheck {
        name: "native-check-cache-test",
        run: dummy_native_check_run,
    };

    let first = runner
        .run_native_check(&repo_root, &check)
        .expect("first native check should succeed");
    assert_eq!(first.status, CheckStatus::Pass);

    let _ = runner
        .run_native_check(&repo_root, &check)
        .expect("second native check should succeed");

    assert_eq!(
        count.load(Ordering::SeqCst),
        1,
        "cached native check should skip inner runner"
    );
}

#[test]
fn test_compute_scope_hash_stable_across_mtime_change() {
    // TDD: same file content must produce same hash even after mtime changes.
    let tmp = unique_test_dir("xtask-cache-test-content-stable");
    let _ = std::fs::create_dir_all(&tmp);
    let src_dir = tmp.join("src");
    let _ = std::fs::create_dir_all(&src_dir);

    // Write a file with known content.
    let file_path = src_dir.join("lib.rs");
    std::fs::write(&file_path, b"fn foo() {}").unwrap();

    let scope = CheckScope::Directories(&["src"]);
    let hash1 = compute_scope_hash(&tmp, &scope).unwrap();

    // Re-write same content (changes mtime but not content).
    std::fs::write(&file_path, b"fn foo() {}").unwrap();

    let hash2 = compute_scope_hash(&tmp, &scope).unwrap();

    assert_eq!(
        hash1, hash2,
        "same file content must produce same scope hash regardless of mtime"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_compute_scope_hash_differs_on_content_change() {
    // TDD: different file content must produce different hash.
    let tmp = unique_test_dir("xtask-cache-test-content-change");
    let _ = std::fs::create_dir_all(&tmp);
    let src_dir = tmp.join("src");
    let _ = std::fs::create_dir_all(&src_dir);

    let file_path = src_dir.join("lib.rs");
    std::fs::write(&file_path, b"fn foo() {}").unwrap();

    let scope = CheckScope::Directories(&["src"]);
    let hash1 = compute_scope_hash(&tmp, &scope).unwrap();

    // Write different content.
    std::fs::write(&file_path, b"fn bar() {}").unwrap();

    let hash2 = compute_scope_hash(&tmp, &scope).unwrap();

    assert_ne!(
        hash1, hash2,
        "different file content must produce different scope hash"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_compute_scope_hash_build_scope_includes_cargo_toml_inputs() {
    // Cache invalidation must include Cargo.toml inputs for build-related checks.
    // Regression: hashing only Cargo.lock + *.rs can produce false cache hits.
    let tmp = unique_test_dir("xtask-cache-test-cargo-toml");
    let _ = std::fs::remove_dir_all(&tmp);
    let _ = std::fs::create_dir_all(tmp.join("xtask/src"));

    // Required for Build scope.
    std::fs::write(tmp.join("Cargo.lock"), b"# lock").unwrap();

    // A source file so the scope isn't empty.
    std::fs::write(tmp.join("xtask/src/lib.rs"), b"fn foo() {}").unwrap();

    // Create an initial manifest.
    std::fs::create_dir_all(tmp.join("xtask")).unwrap();
    std::fs::write(
        tmp.join("xtask/Cargo.toml"),
        b"[package]\nname = \"xtask\"\nversion = \"0.1.0\"\n",
    )
    .unwrap();

    let scope = CheckScope::Build(&["xtask/src"]);
    let h1 = compute_scope_hash(&tmp, &scope).unwrap();

    // Changing Cargo.toml should invalidate the scope hash.
    std::fs::write(
        tmp.join("xtask/Cargo.toml"),
        b"[package]\nname = \"xtask\"\nversion = \"0.2.0\"\n",
    )
    .unwrap();
    let h2 = compute_scope_hash(&tmp, &scope).unwrap();

    assert_ne!(h1, h2, "Cargo.toml change must change scope hash");

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_compute_scope_hash_test_integration_changes_when_fixture_changes() {
    let tmp = unique_test_dir("xtask-cache-test-integration-artifact-change");
    let _ = std::fs::remove_dir_all(&tmp);
    let _ = std::fs::create_dir_all(tmp.join("ralph-workflow/src"));
    let _ = std::fs::create_dir_all(tmp.join("test-helpers/src"));
    let _ = std::fs::create_dir_all(tmp.join("tests/integration_tests/artifacts"));

    std::fs::write(
        tmp.join("Cargo.toml"),
        b"[workspace]\nmembers = [\"ralph-workflow\", \"test-helpers\", \"tests\"]\n",
    )
    .unwrap();
    std::fs::write(tmp.join("Cargo.lock"), b"# lock\n").unwrap();
    std::fs::write(
        tmp.join("ralph-workflow/Cargo.toml"),
        b"[package]\nname = \"ralph-workflow\"\nversion = \"0.1.0\"\n",
    )
    .unwrap();
    std::fs::write(
        tmp.join("test-helpers/Cargo.toml"),
        b"[package]\nname = \"test-helpers\"\nversion = \"0.1.0\"\n",
    )
    .unwrap();
    std::fs::write(
        tmp.join("tests/Cargo.toml"),
        b"[package]\nname = \"tests\"\nversion = \"0.1.0\"\n",
    )
    .unwrap();
    std::fs::write(
        tmp.join("ralph-workflow/src/lib.rs"),
        b"pub fn workflow() {}\n",
    )
    .unwrap();
    std::fs::write(tmp.join("test-helpers/src/lib.rs"), b"pub fn helper() {}\n").unwrap();
    std::fs::write(
        tmp.join("tests/integration_tests/sample.rs"),
        b"const LOG: &str = include_str!(\"artifacts/example_log.log\");\n#[test]\nfn integration() { assert!(!LOG.is_empty()); }\n",
    )
    .unwrap();
    std::fs::write(
        tmp.join("tests/integration_tests/artifacts/example_log.log"),
        b"first fixture\n",
    )
    .unwrap();

    let scope = scope_for("test-integration");
    let hash_before = compute_scope_hash(&tmp, &scope).unwrap();

    std::fs::write(
        tmp.join("tests/integration_tests/artifacts/example_log.log"),
        b"second fixture\n",
    )
    .unwrap();

    let hash_after = compute_scope_hash(&tmp, &scope).unwrap();

    assert_ne!(
        hash_before, hash_after,
        "integration test scope must invalidate when compile-time fixtures change"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_compute_scope_hash_clippy_core_changes_when_embedded_prompt_changes() {
    let tmp = unique_test_dir("xtask-cache-test-clippy-core-template-change");
    let _ = std::fs::remove_dir_all(&tmp);
    let _ = std::fs::create_dir_all(tmp.join("ralph-workflow/src/templates"));
    let _ = std::fs::create_dir_all(tmp.join("templates/prompts"));
    let _ = std::fs::create_dir_all(tmp.join("tests/integration_tests"));
    let _ = std::fs::create_dir_all(tmp.join("test-helpers/src"));

    std::fs::write(
        tmp.join("Cargo.toml"),
        b"[workspace]\nmembers = [\"ralph-workflow\", \"test-helpers\", \"tests\"]\n",
    )
    .unwrap();
    std::fs::write(tmp.join("Cargo.lock"), b"# lock\n").unwrap();
    std::fs::write(
        tmp.join("ralph-workflow/Cargo.toml"),
        b"[package]\nname = \"ralph-workflow\"\nversion = \"0.1.0\"\n",
    )
    .unwrap();
    std::fs::write(
        tmp.join("test-helpers/Cargo.toml"),
        b"[package]\nname = \"test-helpers\"\nversion = \"0.1.0\"\n",
    )
    .unwrap();
    std::fs::write(
        tmp.join("tests/Cargo.toml"),
        b"[package]\nname = \"tests\"\nversion = \"0.1.0\"\n",
    )
    .unwrap();
    std::fs::write(
        tmp.join("ralph-workflow/src/templates/mod.rs"),
        b"pub const TEMPLATE: &str = include_str!(\"../../templates/prompts/feature-spec.md\");\n",
    )
    .unwrap();
    std::fs::write(tmp.join("test-helpers/src/lib.rs"), b"pub fn helper() {}\n").unwrap();
    std::fs::write(
        tmp.join("tests/integration_tests/smoke.rs"),
        b"#[test]\nfn smoke() {}\n",
    )
    .unwrap();
    std::fs::write(
        tmp.join("templates/prompts/feature-spec.md"),
        b"prompt one\n",
    )
    .unwrap();

    let scope = scope_for("clippy-core");
    let hash_before = compute_scope_hash(&tmp, &scope).unwrap();

    std::fs::write(
        tmp.join("templates/prompts/feature-spec.md"),
        b"prompt two\n",
    )
    .unwrap();

    let hash_after = compute_scope_hash(&tmp, &scope).unwrap();

    assert_ne!(
        hash_before, hash_after,
        "clippy-core scope must invalidate when embedded prompt markdown changes"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

fn write_release_build_scope_fixture(tmp: &Path) {
    let _ = std::fs::create_dir_all(tmp.join("ralph-workflow/src"));
    let _ = std::fs::create_dir_all(tmp.join("test-helpers/src"));
    let _ = std::fs::create_dir_all(tmp.join("xtask/src"));
    let _ = std::fs::create_dir_all(tmp.join("tests/integration_tests"));

    std::fs::write(
        tmp.join("Cargo.toml"),
        br#"[workspace]
members = ["ralph-workflow", "test-helpers", "tests", "xtask"]
default-members = ["ralph-workflow", "test-helpers", "xtask"]
"#,
    )
    .unwrap();
    std::fs::write(tmp.join("Cargo.lock"), b"# lock\n").unwrap();
    std::fs::write(
        tmp.join("ralph-workflow/Cargo.toml"),
        b"[package]\nname = \"ralph-workflow\"\nversion = \"0.1.0\"\n",
    )
    .unwrap();
    std::fs::write(
        tmp.join("test-helpers/Cargo.toml"),
        b"[package]\nname = \"test-helpers\"\nversion = \"0.1.0\"\n",
    )
    .unwrap();
    std::fs::write(
        tmp.join("tests/Cargo.toml"),
        b"[package]\nname = \"tests\"\nversion = \"0.1.0\"\n",
    )
    .unwrap();
    std::fs::write(
        tmp.join("xtask/Cargo.toml"),
        b"[package]\nname = \"xtask\"\nversion = \"0.1.0\"\n",
    )
    .unwrap();
    std::fs::write(
        tmp.join("ralph-workflow/src/lib.rs"),
        b"pub fn workflow() {}\n",
    )
    .unwrap();
    std::fs::write(tmp.join("test-helpers/src/lib.rs"), b"pub fn helper() {}\n").unwrap();
    std::fs::write(tmp.join("xtask/src/main.rs"), b"fn main() {}\n").unwrap();
    std::fs::write(
        tmp.join("tests/integration_tests/release_scope.rs"),
        b"#[test]\nfn integration() {}\n",
    )
    .unwrap();
}

#[test]
fn test_release_build_scope_ignores_non_default_member_tests_changes() {
    let tmp = unique_test_dir("xtask-cache-test-release-build-ignores-tests");
    let _ = std::fs::remove_dir_all(&tmp);
    write_release_build_scope_fixture(&tmp);

    let scope = scope_for("release-build");
    let hash_before = compute_scope_hash(&tmp, &scope).unwrap();

    // Change a test file that is NOT a default member.
    std::fs::write(
        tmp.join("tests/integration_tests/release_scope.rs"),
        b"#[test]\nfn integration_changed() {}\n",
    )
    .unwrap();

    let hash_after = compute_scope_hash(&tmp, &scope).unwrap();

    assert_eq!(
        hash_before, hash_after,
        "release-build scope must ignore changes to non-default member tests"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_release_build_scope_tracks_default_member_sources() {
    let tmp = unique_test_dir("xtask-cache-test-release-build-default-members");
    let _ = std::fs::remove_dir_all(&tmp);
    write_release_build_scope_fixture(&tmp);

    let scope = scope_for("release-build");
    let hash_before = compute_scope_hash(&tmp, &scope).unwrap();

    // Change a default member source file.
    std::fs::write(
        tmp.join("ralph-workflow/src/lib.rs"),
        b"pub fn workflow_changed() {}\n",
    )
    .unwrap();

    let hash_after = compute_scope_hash(&tmp, &scope).unwrap();

    assert_ne!(
        hash_before, hash_after,
        "release-build scope must track changes to default member sources"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_release_build_scope_tracks_xtask_changes() {
    let tmp = unique_test_dir("xtask-cache-test-release-build-xtask");
    let _ = std::fs::remove_dir_all(&tmp);
    write_release_build_scope_fixture(&tmp);

    let scope = scope_for("release-build");
    let hash_before = compute_scope_hash(&tmp, &scope).unwrap();

    // Change xtask (a default member).
    std::fs::write(
        tmp.join("xtask/src/main.rs"),
        b"fn main() { println!(\"changed\"); }\n",
    )
    .unwrap();

    let hash_after = compute_scope_hash(&tmp, &scope).unwrap();

    assert_ne!(
        hash_before, hash_after,
        "release-build scope must track xtask changes"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_release_build_scope_tracks_transitive_compile_time_dependencies() {
    let tmp = unique_test_dir("xtask-cache-test-release-build-compile-time");
    let _ = std::fs::remove_dir_all(&tmp);
    write_release_build_scope_fixture(&tmp);

    // Add prompt template that ralph-workflow embeds at compile time.
    let _ = std::fs::create_dir_all(tmp.join("templates/prompts"));
    std::fs::write(
        tmp.join("templates/prompts/feature-spec.md"),
        b"# Feature Spec\n",
    )
    .unwrap();

    let scope = scope_for("release-build");
    let hash_before = compute_scope_hash(&tmp, &scope).unwrap();

    // Change the embedded prompt.
    std::fs::write(
        tmp.join("templates/prompts/feature-spec.md"),
        b"# Updated Feature Spec\n",
    )
    .unwrap();

    let hash_after = compute_scope_hash(&tmp, &scope).unwrap();

    assert_ne!(
        hash_before, hash_after,
        "release-build scope must track transitive compile-time dependencies (embedded prompts)"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

// ── Parallel file reading tests ───────────────────────────────────────────

#[test]
fn test_parallel_file_read_same_hash_as_sequential() {
    // Parallel read_files_parallel must produce the same bytes as sequential read.
    let tmp = unique_test_dir("xtask-parallel-hash");
    let _ = std::fs::create_dir_all(&tmp);
    let src = tmp.join("src");
    let _ = std::fs::create_dir_all(&src);

    // Write multiple files to exercise the parallel path (>= PARALLEL_THRESHOLD).
    for i in 0..8u32 {
        std::fs::write(
            src.join(format!("file{i}.rs")),
            format!("fn f{i}() {{}}").as_bytes(),
        )
        .unwrap();
    }

    let scope = CheckScope::Directories(&["src"]);
    // Compute hash twice — parallel impl must be deterministic.
    let h1 = compute_scope_hash(&tmp, &scope).unwrap();
    let h2 = compute_scope_hash(&tmp, &scope).unwrap();
    assert_eq!(h1, h2, "parallel scope hash must be deterministic");

    // Content change must still be detected.
    std::fs::write(src.join("file0.rs"), b"fn changed() {}").unwrap();
    let h3 = compute_scope_hash(&tmp, &scope).unwrap();
    assert_ne!(h1, h3, "hash must change when file content changes");

    let _ = std::fs::remove_dir_all(&tmp);
}
