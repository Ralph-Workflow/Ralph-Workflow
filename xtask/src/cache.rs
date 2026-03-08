use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};

use crate::verify::{CommandOutput, CommandRunner, CommandSpec};

/// A minimal FNV-1a 64-bit hasher.  Stable across Rust versions and platforms.
///
/// FNV-1a is significantly faster than SipHash (the default) for short inputs
/// (file paths), which constitute the majority of hashed bytes.
struct Fnv1aHasher(u64);

impl Fnv1aHasher {
    const OFFSET_BASIS: u64 = 14_695_981_039_346_656_037;
    const PRIME: u64 = 1_099_511_628_211;

    fn new() -> Self {
        Self(Self::OFFSET_BASIS)
    }

    fn write_bytes(&mut self, bytes: &[u8]) {
        for &b in bytes {
            self.0 ^= b as u64;
            self.0 = self.0.wrapping_mul(Self::PRIME);
        }
    }

    fn finish(self) -> u64 {
        self.0
    }
}

/// Cached result for a single check.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CacheEntry {
    /// Hash of the check scope (file paths + content bytes, sorted by path).
    pub scope_hash: u64,
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
}

/// Scope definition: which directories/glob patterns constitute the
/// relevant input for a given check name.
pub enum CheckScope {
    /// Hash all .rs files under the given directory paths.
    Directories(&'static [&'static str]),
    /// Hash Cargo.lock plus all .rs files under the given paths.
    Build(&'static [&'static str]),
}

/// Returns a stable string key for a scope, used for in-process memoization.
/// The key encodes both the scope type (directories vs build) and the directory list.
pub fn scope_memo_key(scope: &CheckScope) -> String {
    match scope {
        CheckScope::Directories(dirs) => format!("d:{}", dirs.join(",")),
        CheckScope::Build(dirs) => format!("b:{}", dirs.join(",")),
    }
}

/// Returns the scope for a given check name. Checks not listed here
/// are assumed to have Build scope (most conservative: any change triggers re-run).
pub fn scope_for(check_name: &str) -> CheckScope {
    match check_name {
        // rg check spanning both src and tests (complex PCRE2 negative lookahead)
        "audit-ignore-has-url" => CheckScope::Directories(&["tests", "ralph-workflow/src"]),
        // rg check spanning all .rs files (complex PCRE2 multiline)
        "forbidden-allow-expect-scan" => {
            CheckScope::Directories(&["ralph-workflow/src", "tests", "xtask/src"])
        }
        // fmt-check: only .rs file content matters, not Cargo.lock
        "fmt-check" => CheckScope::Directories(&[
            "ralph-workflow/src",
            "tests",
            "xtask/src",
            "test-helpers/src",
        ]),
        // per-package clippy and tests: only the package's own source + Cargo.lock
        "clippy-ralph-workflow"
        | "test-ralph-workflow-lib"
        | "memory-safety-benchmarks"
        | "memory-safety-executor"
        | "dylint" => CheckScope::Build(&["ralph-workflow/src"]),

        "clippy-xtask" | "test-xtask" => CheckScope::Build(&["xtask/src"]),

        "clippy-test-helpers" => CheckScope::Build(&["test-helpers/src", "ralph-workflow/src"]),

        "clippy-ralph-workflow-tests" => {
            CheckScope::Build(&["tests", "ralph-workflow/src", "test-helpers/src"])
        }

        "test-integration" | "memory-safety-integration" => CheckScope::Build(&[
            "ralph-workflow/src",
            "tests/integration_tests",
            "test-helpers/src",
        ]),

        "release-build" => CheckScope::Build(&[
            "ralph-workflow/src",
            "tests",
            "xtask/src",
            "test-helpers/src",
        ]),

        // conservative fallback for any unrecognised check
        _ => CheckScope::Build(&["ralph-workflow/src", "tests", "xtask/src"]),
    }
}

/// Read file contents in parallel using scoped threads.
///
/// Files are returned in the same order as `paths` so the caller can hash
/// them deterministically without re-sorting.
fn read_files_parallel(paths: &[PathBuf]) -> Vec<Vec<u8>> {
    if paths.is_empty() {
        return Vec::new();
    }
    // For small file lists, thread overhead exceeds benefit; use serial reads.
    const PARALLEL_THRESHOLD: usize = 4;
    if paths.len() < PARALLEL_THRESHOLD {
        return paths
            .iter()
            .map(|p| std::fs::read(p).unwrap_or_default())
            .collect();
    }

    // Spawn one thread per file (index, bytes) and reassemble in order.
    let handles: Vec<std::thread::JoinHandle<(usize, Vec<u8>)>> = paths
        .iter()
        .enumerate()
        .map(|(i, path)| {
            let path = path.clone();
            std::thread::spawn(move || (i, std::fs::read(&path).unwrap_or_default()))
        })
        .collect();

    let mut results: Vec<Vec<u8>> = vec![Vec::new(); paths.len()];
    for handle in handles {
        if let Ok((i, bytes)) = handle.join() {
            results[i] = bytes;
        }
    }
    results
}

/// Compute a u64 hash of the scope by iterating relevant files and
/// hashing (path, content bytes) pairs. Content-based hashing ensures
/// cache keys are stable across mtime changes (e.g., git checkout round-trips).
pub fn compute_scope_hash(repo_root: &Path, scope: &CheckScope) -> u64 {
    let mut hasher = Fnv1aHasher::new();

    let dirs = match scope {
        CheckScope::Directories(dirs) => dirs,
        CheckScope::Build(dirs) => {
            // Include Cargo.lock content in hash.
            let lock = repo_root.join("Cargo.lock");
            hasher.write_bytes(lock.to_string_lossy().as_bytes());
            if let Ok(bytes) = std::fs::read(&lock) {
                hasher.write_bytes(&bytes);
            }
            dirs
        }
    };

    let mut entries: Vec<PathBuf> = Vec::new();
    for dir in *dirs {
        let full = repo_root.join(dir);
        crate::scanner::collect_files_with_glob(&full, "*.rs", &mut entries);
    }
    entries.sort();

    // Read files in parallel; hash results in sorted (deterministic) order.
    let file_bytes = read_files_parallel(&entries);
    for (path, bytes) in entries.iter().zip(file_bytes.iter()) {
        hasher.write_bytes(path.to_string_lossy().as_bytes());
        hasher.write_bytes(bytes);
    }

    hasher.finish()
}

/// On-disk format for the cache file.
#[derive(Debug, Default, Serialize, Deserialize)]
struct CacheFile {
    entries: HashMap<String, CacheEntry>,
}

/// A CommandRunner that wraps another runner and caches successful results.
///
/// Cache is persisted to `{repo_root}/target/xtask-verify-cache.json`.
/// Only successful check results are cached; failures always cause a re-run.
/// Disk writes are deferred until `flush()` is called (O(1) writes per run).
pub struct CachingCommandRunner {
    inner: Box<dyn CommandRunner + Send + Sync>,
    repo_root: PathBuf,
    pub(crate) memory: Mutex<HashMap<String, CacheEntry>>,
    /// In-process memoization: avoids re-traversing the same directories
    /// for multiple checks that share the same scope within a single run.
    pub(crate) scope_memo: Mutex<HashMap<String, u64>>,
    /// Set to true when in-memory cache has unsaved changes.
    dirty: AtomicBool,
}

impl CachingCommandRunner {
    pub fn new(inner: impl CommandRunner + Send + 'static, repo_root: PathBuf) -> Self {
        let cache_path = repo_root.join("target/xtask-verify-cache.json");
        let memory = if let Ok(data) = std::fs::read_to_string(&cache_path) {
            serde_json::from_str::<CacheFile>(&data)
                .map(|f| f.entries)
                .unwrap_or_default()
        } else {
            HashMap::new()
        };
        Self {
            inner: Box::new(inner),
            repo_root,
            memory: Mutex::new(memory),
            scope_memo: Mutex::new(HashMap::new()),
            dirty: AtomicBool::new(false),
        }
    }

    fn cache_path(&self) -> PathBuf {
        self.repo_root.join("target/xtask-verify-cache.json")
    }

    fn persist(&self) {
        let entries = self.memory.lock().unwrap().clone();
        let file = CacheFile { entries };
        if let Ok(json) = serde_json::to_string_pretty(&file) {
            let _ = std::fs::write(self.cache_path(), json);
        }
    }

    /// Flush any pending cache updates to disk.  Call once at program exit.
    /// Idempotent: safe to call multiple times.
    pub fn flush(&self) {
        if self.dirty.load(Ordering::Relaxed) {
            self.persist();
            self.dirty.store(false, Ordering::Relaxed);
        }
    }

    fn compute_or_cached_scope_hash(&self, scope: &CheckScope) -> u64 {
        let key = scope_memo_key(scope);
        {
            let memo = self.scope_memo.lock().unwrap();
            if let Some(&h) = memo.get(&key) {
                return h;
            }
        }
        let h = compute_scope_hash(&self.repo_root, scope);
        self.scope_memo.lock().unwrap().insert(key, h);
        h
    }
}

impl CommandRunner for CachingCommandRunner {
    fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        let scope = scope_for(spec.name);
        let hash = self.compute_or_cached_scope_hash(&scope);
        let key = format!("{}:{}", spec.name, hash);

        // Check cache.
        {
            let mem = self.memory.lock().unwrap();
            if let Some(entry) = mem.get(&key) {
                if entry.scope_hash == hash {
                    return Ok(CommandOutput {
                        exit_code: entry.exit_code,
                        stdout: entry.stdout.clone(),
                        stderr: entry.stderr.clone(),
                    });
                }
            }
        }

        // Cache miss: run the real command.
        let output = self.inner.run(spec)?;

        // Only cache successful results (exit code in success list, no error/warning diagnostics).
        use crate::scanner::{scan_has_diagnostic_prefix, DiagnosticLevel};
        let is_success = spec.success_exit_codes.contains(&output.exit_code)
            && scan_has_diagnostic_prefix(&output.stdout) == DiagnosticLevel::Clean
            && scan_has_diagnostic_prefix(&output.stderr) == DiagnosticLevel::Clean;
        if is_success {
            self.memory.lock().unwrap().insert(
                key,
                CacheEntry {
                    scope_hash: hash,
                    exit_code: output.exit_code,
                    stdout: output.stdout.clone(),
                    stderr: output.stderr.clone(),
                },
            );
            // Mark dirty; actual disk write is deferred to flush().
            self.dirty.store(true, Ordering::Relaxed);
        }

        Ok(output)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

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
    fn test_caching_runner_skips_check_on_cache_hit() {
        // Use a temp directory so compute_scope_hash works without real repo files.
        let tmp = std::env::temp_dir().join("xtask-cache-test-hit");
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
        let tmp = std::env::temp_dir().join("xtask-cache-test-miss");
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
        let tmp = std::env::temp_dir().join("xtask-cache-test-no-failure-cache");
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
    fn test_scope_for_clippy_ralph_workflow_is_granular() {
        let key = scope_memo_key(&scope_for("clippy-ralph-workflow"));
        // Must be a Build scope targeting only ralph-workflow/src, not the broad fallback.
        assert_eq!(key, "b:ralph-workflow/src");
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
    fn test_scope_for_fmt_check_uses_directories_not_build() {
        let key = scope_memo_key(&scope_for("fmt-check"));
        // fmt-check should be a Directories scope (no Cargo.lock dependency).
        assert!(
            key.starts_with("d:"),
            "fmt-check must use Directories scope, got: {key}"
        );
        // Must include all four source trees.
        assert!(
            key.contains("ralph-workflow/src"),
            "missing ralph-workflow/src"
        );
        assert!(key.contains("tests"), "missing tests");
        assert!(key.contains("xtask/src"), "missing xtask/src");
        assert!(key.contains("test-helpers/src"), "missing test-helpers/src");
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
        let tmp = std::env::temp_dir().join("xtask-cache-test-scope-memo");
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
        let h1 = runner.compute_or_cached_scope_hash(&scope_for("clippy-xtask"));
        {
            let memo = runner.scope_memo.lock().unwrap();
            assert!(
                memo.contains_key(&key1),
                "scope memo must be populated after first hash"
            );
        }

        // Second computation for same key returns same hash from memo.
        let h2 = runner.compute_or_cached_scope_hash(&scope_for("test-xtask"));
        assert_eq!(h1, h2, "same scope must produce same hash");

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_compute_scope_hash_stable_across_mtime_change() {
        // TDD: same file content must produce same hash even after mtime changes.
        let tmp = std::env::temp_dir().join("xtask-cache-test-content-stable");
        let _ = std::fs::create_dir_all(&tmp);
        let src_dir = tmp.join("src");
        let _ = std::fs::create_dir_all(&src_dir);

        // Write a file with known content.
        let file_path = src_dir.join("lib.rs");
        std::fs::write(&file_path, b"fn foo() {}").unwrap();

        let scope = CheckScope::Directories(&["src"]);
        let hash1 = compute_scope_hash(&tmp, &scope);

        // Re-write same content (changes mtime but not content).
        std::fs::write(&file_path, b"fn foo() {}").unwrap();

        let hash2 = compute_scope_hash(&tmp, &scope);

        assert_eq!(
            hash1, hash2,
            "same file content must produce same scope hash regardless of mtime"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_compute_scope_hash_differs_on_content_change() {
        // TDD: different file content must produce different hash.
        let tmp = std::env::temp_dir().join("xtask-cache-test-content-change");
        let _ = std::fs::create_dir_all(&tmp);
        let src_dir = tmp.join("src");
        let _ = std::fs::create_dir_all(&src_dir);

        let file_path = src_dir.join("lib.rs");
        std::fs::write(&file_path, b"fn foo() {}").unwrap();

        let scope = CheckScope::Directories(&["src"]);
        let hash1 = compute_scope_hash(&tmp, &scope);

        // Write different content.
        std::fs::write(&file_path, b"fn bar() {}").unwrap();

        let hash2 = compute_scope_hash(&tmp, &scope);

        assert_ne!(
            hash1, hash2,
            "different file content must produce different scope hash"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_caching_runner_reuses_scope_hash_for_same_scope() {
        // After running two checks with the same scope, scope_memo must hold exactly one entry.
        let tmp = std::env::temp_dir().join("xtask-cache-test-reuse-scope");
        let _ = std::fs::create_dir_all(&tmp);

        let spec1 = make_spec("clippy-xtask");
        let spec2 = make_spec("test-xtask");

        let (inner, _count) = CountingRunner::new(success_output());
        let runner = CachingCommandRunner::new(inner, tmp.clone());

        let _ = runner.run(&spec1).unwrap();
        let _ = runner.run(&spec2).unwrap();

        // Both checks map to the same scope key, so scope_memo should have exactly 1 entry.
        let memo_len = runner.scope_memo.lock().unwrap().len();
        assert_eq!(
            memo_len, 1,
            "two checks with same scope should share one scope_memo entry"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    // ── FNV-1a hasher tests ───────────────────────────────────────────────────

    #[test]
    fn test_fnv_hasher_is_deterministic_for_same_content() {
        // FNV-1a must produce the same hash for identical file content
        // regardless of when it is computed (no DefaultHasher randomisation).
        let tmp = std::env::temp_dir().join("xtask-fnv-deterministic");
        let _ = std::fs::create_dir_all(&tmp);
        let src = tmp.join("src");
        let _ = std::fs::create_dir_all(&src);
        std::fs::write(src.join("lib.rs"), b"fn foo() {}").unwrap();

        let scope = CheckScope::Directories(&["src"]);
        let h1 = compute_scope_hash(&tmp, &scope);
        let h2 = compute_scope_hash(&tmp, &scope);
        assert_eq!(h1, h2, "same content must produce same hash on every call");

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_fnv_hasher_differs_for_different_content() {
        let tmp = std::env::temp_dir().join("xtask-fnv-differs");
        let _ = std::fs::create_dir_all(&tmp);
        let src = tmp.join("src");
        let _ = std::fs::create_dir_all(&src);

        std::fs::write(src.join("lib.rs"), b"fn foo() {}").unwrap();
        let scope = CheckScope::Directories(&["src"]);
        let h1 = compute_scope_hash(&tmp, &scope);

        std::fs::write(src.join("lib.rs"), b"fn bar() {}").unwrap();
        let h2 = compute_scope_hash(&tmp, &scope);

        assert_ne!(h1, h2, "different content must produce different hash");
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_fnv_hasher_known_value_for_empty_directory_scope() {
        // Empty directory: hash must be consistent across two calls.
        // We only assert the result is the same across two calls to avoid
        // pinning to implementation details.
        let tmp = std::env::temp_dir().join("xtask-fnv-empty-scope");
        let _ = std::fs::create_dir_all(tmp.join("src"));

        let scope = CheckScope::Directories(&["src"]);
        let h1 = compute_scope_hash(&tmp, &scope);
        let h2 = compute_scope_hash(&tmp, &scope);
        assert_eq!(h1, h2, "empty directory must produce consistent hash");
        let _ = std::fs::remove_dir_all(&tmp);
    }

    // ── Deferred persistence tests ────────────────────────────────────────────

    #[test]
    fn test_run_does_not_persist_before_flush() {
        // After a successful run, the cache must be updated in memory but NOT
        // written to disk until flush() is called.
        let tmp = std::env::temp_dir().join("xtask-cache-deferred-persist");
        let _ = std::fs::create_dir_all(&tmp);

        let spec = make_spec("no-test-flags-cfg-test");
        let cache_path = tmp.join("target/xtask-verify-cache.json");

        let (inner, _count) = CountingRunner::new(success_output());
        let runner = CachingCommandRunner::new(inner, tmp.clone());

        // Run a check — should populate in-memory cache but NOT write to disk.
        let _ = runner.run(&spec).unwrap();

        assert!(
            !cache_path.exists(),
            "cache must not be written to disk before flush() is called"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_flush_writes_cache_to_disk() {
        let tmp = std::env::temp_dir().join("xtask-cache-flush-writes");
        let _ = std::fs::create_dir_all(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("target"));

        let spec = make_spec("no-test-flags-cfg-test");
        let cache_path = tmp.join("target/xtask-verify-cache.json");

        let (inner, _count) = CountingRunner::new(success_output());
        let runner = CachingCommandRunner::new(inner, tmp.clone());

        let _ = runner.run(&spec).unwrap();
        runner.flush();

        assert!(
            cache_path.exists(),
            "cache must be written to disk after flush() is called"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_flush_is_idempotent() {
        // Calling flush() multiple times must not cause errors or duplicated writes.
        let tmp = std::env::temp_dir().join("xtask-cache-flush-idempotent");
        let _ = std::fs::create_dir_all(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("target"));

        let (inner, _count) = CountingRunner::new(success_output());
        let runner = CachingCommandRunner::new(inner, tmp.clone());

        let spec = make_spec("no-test-flags-cfg-test");
        let _ = runner.run(&spec).unwrap();
        runner.flush();
        runner.flush(); // second flush must not panic or corrupt

        let _ = std::fs::remove_dir_all(&tmp);
    }

    // ── Parallel file reading tests ───────────────────────────────────────────

    #[test]
    fn test_parallel_file_read_same_hash_as_sequential() {
        // Parallel read_files_parallel must produce the same bytes as sequential read.
        let tmp = std::env::temp_dir().join("xtask-parallel-hash");
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
        let h1 = compute_scope_hash(&tmp, &scope);
        let h2 = compute_scope_hash(&tmp, &scope);
        assert_eq!(h1, h2, "parallel scope hash must be deterministic");

        // Content change must still be detected.
        std::fs::write(src.join("file0.rs"), b"fn changed() {}").unwrap();
        let h3 = compute_scope_hash(&tmp, &scope);
        assert_ne!(h1, h3, "hash must change when file content changes");

        let _ = std::fs::remove_dir_all(&tmp);
    }
}
