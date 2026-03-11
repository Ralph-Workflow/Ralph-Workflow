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
#[derive(Clone, Copy)]
pub struct ScopeGlob {
    pub dir: &'static str,
    pub pattern: &'static str,
}

pub enum CheckScope {
    /// Hash all .rs files under the given directory paths.
    Directories(&'static [&'static str]),
    /// Hash Cargo.lock plus all .rs files under the given paths.
    Build(&'static [&'static str]),
    /// Hash explicitly selected files and globbed inputs.
    Patterns {
        globs: &'static [ScopeGlob],
        files: &'static [&'static str],
        include_lock: bool,
    },
}

const RALPH_GUI_RUST_SCOPE_DIRS: &[&str] = &["ralph-gui", "ralph-workflow/src"];
const RALPH_GUI_FRONTEND_INSTALL_FILES: &[&str] = &[
    "ralph-gui/ui/package.json",
    "ralph-gui/ui/package-lock.json",
];
const RALPH_GUI_FRONTEND_CHECK_FILES: &[&str] = &[
    "ralph-gui/ui/package.json",
    "ralph-gui/ui/package-lock.json",
    "ralph-gui/ui/tsconfig.json",
    "ralph-gui/ui/tsconfig.node.json",
    "ralph-gui/ui/vite.config.ts",
    "ralph-gui/ui/eslint.config.mjs",
    "ralph-gui/ui/index.html",
];
const RALPH_GUI_FRONTEND_SRC_GLOBS: &[ScopeGlob] = &[ScopeGlob {
    dir: "ralph-gui/ui/src",
    pattern: "*",
}];

/// Returns a stable string key for a scope, used for in-process memoization.
/// The key encodes both the scope type (directories vs build) and the directory list.
pub fn scope_memo_key(scope: &CheckScope) -> String {
    match scope {
        CheckScope::Directories(dirs) => format!("d:{}", dirs.join(",")),
        CheckScope::Build(dirs) => format!("b:{}", dirs.join(",")),
        CheckScope::Patterns {
            globs,
            files,
            include_lock,
        } => {
            let glob_key = globs
                .iter()
                .map(|glob| format!("{}@{}", glob.dir, glob.pattern))
                .collect::<Vec<_>>()
                .join(",");
            format!("p:{include_lock}:{glob_key}:{}", files.join(","))
        }
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

        "clippy-ralph-gui" | "test-ralph-gui-lib" => CheckScope::Build(RALPH_GUI_RUST_SCOPE_DIRS),

        "ralph-gui-frontend-install" => CheckScope::Patterns {
            globs: &[],
            files: RALPH_GUI_FRONTEND_INSTALL_FILES,
            include_lock: false,
        },

        "ralph-gui-frontend-lint" | "ralph-gui-frontend-test" => CheckScope::Patterns {
            globs: RALPH_GUI_FRONTEND_SRC_GLOBS,
            files: RALPH_GUI_FRONTEND_CHECK_FILES,
            include_lock: false,
        },

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
fn io_worker_count(len: usize) -> usize {
    const MAX_IO_WORKERS: usize = 32;
    let avail = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1);
    std::cmp::min(len, std::cmp::min(avail, MAX_IO_WORKERS)).max(1)
}

fn read_files_parallel(paths: &[PathBuf]) -> std::io::Result<Vec<Vec<u8>>> {
    if paths.is_empty() {
        return Ok(Vec::new());
    }
    // For small file lists, thread overhead exceeds benefit; use serial reads.
    const PARALLEL_THRESHOLD: usize = 4;
    if paths.len() < PARALLEL_THRESHOLD {
        return paths.iter().map(std::fs::read).collect();
    }

    // Spawn a bounded number of workers and reassemble in order.
    let workers = io_worker_count(paths.len());
    let mut slots: Vec<Option<std::io::Result<Vec<u8>>>> = (0..paths.len()).map(|_| None).collect();

    std::thread::scope(|s| {
        let handles: Vec<_> = (0..workers)
            .map(|worker_id| {
                s.spawn(move || {
                    let mut out: Vec<(usize, std::io::Result<Vec<u8>>)> = Vec::new();
                    for i in (worker_id..paths.len()).step_by(workers) {
                        out.push((i, std::fs::read(&paths[i])));
                    }
                    out
                })
            })
            .collect();

        for h in handles {
            for (i, r) in h.join().expect("file read worker panicked") {
                slots[i] = Some(r);
            }
        }
    });

    let mut results: Vec<Vec<u8>> = Vec::with_capacity(paths.len());
    for slot in slots {
        // Every index must be populated by exactly one worker.
        let r = slot.expect("worker must write slot");
        results.push(r?);
    }
    Ok(results)
}

/// Compute a u64 hash of the scope by iterating relevant files and
/// hashing (path, content bytes) pairs. Content-based hashing ensures
/// cache keys are stable across mtime changes (e.g., git checkout round-trips).
pub fn compute_scope_hash(repo_root: &Path, scope: &CheckScope) -> std::io::Result<u64> {
    let mut hasher = Fnv1aHasher::new();

    let mut all_paths: Vec<PathBuf> = Vec::new();
    match scope {
        CheckScope::Directories(dirs) | CheckScope::Build(dirs) => {
            // Include check-specific sources.
            for dir in *dirs {
                let full = repo_root.join(dir);
                if full.exists() {
                    crate::scanner::collect_files_with_glob(&full, "*.rs", &mut all_paths)?;
                }

                // Include Cargo.toml from this directory's crate (and any parent crates)
                // so build/lint caches invalidate on manifest changes.
                let mut cur = full.as_path();
                while let Some(parent) = cur.parent() {
                    let manifest = cur.join("Cargo.toml");
                    if manifest.exists() {
                        all_paths.push(manifest);
                    }
                    if cur == repo_root {
                        break;
                    }
                    cur = parent;
                }
            }

            // Include repo-wide config inputs that affect verification results.
            let mut config_candidates: Vec<&str> = vec![
                "Cargo.toml",
                "rustfmt.toml",
                "clippy.toml",
                ".cargo/config.toml",
                ".cargo/config",
                "rust-toolchain.toml",
                "rust-toolchain",
                "Makefile",
            ];
            if matches!(scope, CheckScope::Build(_)) {
                config_candidates.push("Cargo.lock");
            }
            for rel in config_candidates {
                let p = repo_root.join(rel);
                if p.exists() {
                    all_paths.push(p);
                }
            }
        }
        CheckScope::Patterns {
            globs,
            files,
            include_lock,
        } => {
            for glob in *globs {
                let full = repo_root.join(glob.dir);
                if full.exists() {
                    crate::scanner::collect_files_with_glob(&full, glob.pattern, &mut all_paths)?;
                }
            }
            for rel in *files {
                let path = repo_root.join(rel);
                if path.exists() {
                    all_paths.push(path);
                }
            }
            if *include_lock {
                let lock_path = repo_root.join("Cargo.lock");
                if lock_path.exists() {
                    all_paths.push(lock_path);
                }
            }
        }
    }

    // Deduplicate + sort for deterministic hashing.
    all_paths.sort();
    all_paths.dedup();

    // Read files in parallel (bounded); hash results in sorted order.
    let file_bytes = read_files_parallel(&all_paths)?;
    for (path, bytes) in all_paths.iter().zip(file_bytes.iter()) {
        hasher.write_bytes(path.to_string_lossy().as_bytes());
        hasher.write_bytes(bytes);
    }

    Ok(hasher.finish())
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
    pub fn new(inner: impl CommandRunner + 'static, repo_root: PathBuf) -> Self {
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

    fn persist(&self) -> std::io::Result<()> {
        let entries = self.memory.lock().unwrap().clone();
        let file = CacheFile { entries };

        let json = serde_json::to_string_pretty(&file).map_err(std::io::Error::other)?;

        let final_path = self.cache_path();
        if let Some(parent) = final_path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        // Write to a temp file and rename for best-effort atomic update.
        let file_name = final_path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("xtask-verify-cache.json");
        let tmp_path = final_path.with_file_name(format!("{file_name}.tmp.{}", std::process::id()));

        std::fs::write(&tmp_path, json)?;

        // On Windows rename may fail if destination exists; remove first.
        if final_path.exists() {
            let _ = std::fs::remove_file(&final_path);
        }
        std::fs::rename(&tmp_path, &final_path)?;
        Ok(())
    }

    /// Flush any pending cache updates to disk.  Call once at program exit.
    /// Idempotent: safe to call multiple times.
    pub fn flush(&self) {
        if self.dirty.load(Ordering::Relaxed) && self.persist().is_ok() {
            self.dirty.store(false, Ordering::Relaxed);
        }
    }

    fn compute_or_cached_scope_hash(&self, scope: &CheckScope) -> Option<u64> {
        let key = scope_memo_key(scope);
        {
            let memo = self.scope_memo.lock().unwrap();
            if let Some(&h) = memo.get(&key) {
                return Some(h);
            }
        }
        match compute_scope_hash(&self.repo_root, scope) {
            Ok(h) => {
                self.scope_memo.lock().unwrap().insert(key, h);
                Some(h)
            }
            Err(_) => None,
        }
    }
}

impl CommandRunner for CachingCommandRunner {
    fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        let scope = scope_for(spec.name);
        let Some(hash) = self.compute_or_cached_scope_hash(&scope) else {
            // If scope hashing fails (unreadable files, directory walk errors, etc.),
            // bypass caching completely to avoid incorrect cache hits.
            return self.inner.run(spec);
        };
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
    fn test_scope_for_clippy_ralph_gui_is_granular() {
        let key = scope_memo_key(&scope_for("clippy-ralph-gui"));
        assert_eq!(key, "b:ralph-gui,ralph-workflow/src");
    }

    #[test]
    fn test_scope_for_test_ralph_gui_lib_is_granular() {
        let key = scope_memo_key(&scope_for("test-ralph-gui-lib"));
        assert_eq!(key, "b:ralph-gui,ralph-workflow/src");
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
        let tmp = std::env::temp_dir().join("xtask-cache-test-content-change");
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
        let tmp = std::env::temp_dir().join("xtask-cache-test-cargo-toml");
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
    fn test_frontend_scope_hash_changes_when_ui_source_changes() {
        let tmp = std::env::temp_dir().join("xtask-cache-test-frontend-source-change");
        let _ = std::fs::remove_dir_all(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("ralph-gui/ui/src"));
        let _ = std::fs::create_dir_all(tmp.join("ralph-workflow/src"));

        std::fs::write(tmp.join("Cargo.toml"), b"[workspace]\nmembers = []\n").unwrap();
        std::fs::write(tmp.join("Cargo.lock"), b"# lock\n").unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/package.json"),
            b"{\"name\":\"ralph-workflow-ui\"}\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/package-lock.json"),
            b"{\"name\":\"ralph-workflow-ui\",\"lockfileVersion\":3}\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/tsconfig.json"),
            b"{\"compilerOptions\":{}}\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/src/App.tsx"),
            b"export function App() { return <div>one</div>; }\n",
        )
        .unwrap();

        let scope = scope_for("ralph-gui-frontend-test");
        let hash_before = compute_scope_hash(&tmp, &scope).unwrap();

        std::fs::write(
            tmp.join("ralph-gui/ui/src/App.tsx"),
            b"export function App() { return <div>two</div>; }\n",
        )
        .unwrap();

        let hash_after = compute_scope_hash(&tmp, &scope).unwrap();

        assert_ne!(
            hash_before, hash_after,
            "frontend scope hash must change when UI source content changes"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_frontend_scope_hash_ignores_unrelated_rust_changes() {
        let tmp = std::env::temp_dir().join("xtask-cache-test-frontend-unrelated-rust");
        let _ = std::fs::remove_dir_all(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("ralph-gui/ui/src"));
        let _ = std::fs::create_dir_all(tmp.join("ralph-workflow/src"));

        std::fs::write(tmp.join("Cargo.toml"), b"[workspace]\nmembers = []\n").unwrap();
        std::fs::write(tmp.join("Cargo.lock"), b"# lock\n").unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/package.json"),
            b"{\"name\":\"ralph-workflow-ui\"}\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/package-lock.json"),
            b"{\"name\":\"ralph-workflow-ui\",\"lockfileVersion\":3}\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/tsconfig.json"),
            b"{\"compilerOptions\":{}}\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/src/App.tsx"),
            b"export function App() { return <div>one</div>; }\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-workflow/src/lib.rs"),
            b"pub fn workflow() {}\n",
        )
        .unwrap();

        let scope = scope_for("ralph-gui-frontend-lint");
        let hash_before = compute_scope_hash(&tmp, &scope).unwrap();

        std::fs::write(
            tmp.join("ralph-workflow/src/lib.rs"),
            b"pub fn workflow() { println!(\"changed\"); }\n",
        )
        .unwrap();

        let hash_after = compute_scope_hash(&tmp, &scope).unwrap();

        assert_eq!(
            hash_before, hash_after,
            "frontend scope hash must ignore unrelated Rust source changes"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[cfg(unix)]
    #[test]
    fn test_caching_runner_bypasses_cache_when_scope_hash_cannot_be_computed() {
        use std::os::unix::fs::PermissionsExt;

        // If a file is unreadable, scope hashing must not collapse it to empty content,
        // otherwise we can incorrectly reuse cached successes.
        let tmp = std::env::temp_dir().join("xtask-cache-test-unreadable");
        let _ = std::fs::remove_dir_all(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("ralph-workflow/src"));
        let _ = std::fs::create_dir_all(tmp.join("target"));

        std::fs::write(tmp.join("Cargo.lock"), b"# lock").unwrap();

        let file_path = tmp.join("ralph-workflow/src/lib.rs");
        std::fs::write(&file_path, b"fn foo() {}\n").unwrap();

        let mut perms = std::fs::metadata(&file_path).unwrap().permissions();
        perms.set_mode(0o000);
        std::fs::set_permissions(&file_path, perms).unwrap();

        let spec = make_spec("clippy-ralph-workflow");

        let (inner, count) = CountingRunner::new(success_output());
        let runner = CachingCommandRunner::new(inner, tmp.clone());

        let _ = runner.run(&spec).unwrap();
        let _ = runner.run(&spec).unwrap();

        // Restore permissions so cleanup works.
        let mut perms_restore = std::fs::metadata(&file_path).unwrap().permissions();
        perms_restore.set_mode(0o644);
        let _ = std::fs::set_permissions(&file_path, perms_restore);

        assert_eq!(
            count.load(Ordering::SeqCst),
            2,
            "unreadable scope inputs must bypass caching (inner must run each time)"
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
        let h1 = compute_scope_hash(&tmp, &scope).unwrap();
        let h2 = compute_scope_hash(&tmp, &scope).unwrap();
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
        let h1 = compute_scope_hash(&tmp, &scope).unwrap();

        std::fs::write(src.join("lib.rs"), b"fn bar() {}").unwrap();
        let h2 = compute_scope_hash(&tmp, &scope).unwrap();

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
        let h1 = compute_scope_hash(&tmp, &scope).unwrap();
        let h2 = compute_scope_hash(&tmp, &scope).unwrap();
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

    #[cfg(unix)]
    #[test]
    fn test_flush_retries_after_persist_failure() {
        use std::os::unix::fs::PermissionsExt;

        let tmp = std::env::temp_dir().join("xtask-cache-flush-retry-after-fail");
        let _ = std::fs::create_dir_all(&tmp);
        let target_dir = tmp.join("target");
        let _ = std::fs::create_dir_all(&target_dir);
        let cache_path = target_dir.join("xtask-verify-cache.json");

        let (inner, _count) = CountingRunner::new(success_output());
        let runner = CachingCommandRunner::new(inner, tmp.clone());
        let spec = make_spec("no-test-flags-cfg-test");

        let _ = runner.run(&spec).unwrap();

        // Make the target dir unreadable/unwritable so persist fails.
        let mut perms = std::fs::metadata(&target_dir).unwrap().permissions();
        perms.set_mode(0o000);
        std::fs::set_permissions(&target_dir, perms).unwrap();

        runner.flush();
        assert!(
            !cache_path.exists(),
            "cache file must not exist when flush cannot persist"
        );

        // Restore permissions and flush again; dirty flag must still be set so the
        // cache can be persisted on a later successful flush.
        let mut perms_restore = std::fs::metadata(&target_dir).unwrap().permissions();
        perms_restore.set_mode(0o755);
        let _ = std::fs::set_permissions(&target_dir, perms_restore);

        runner.flush();
        assert!(
            cache_path.exists(),
            "cache must be written after permissions are restored"
        );

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
        let h1 = compute_scope_hash(&tmp, &scope).unwrap();
        let h2 = compute_scope_hash(&tmp, &scope).unwrap();
        assert_eq!(h1, h2, "parallel scope hash must be deterministic");

        // Content change must still be detected.
        std::fs::write(src.join("file0.rs"), b"fn changed() {}").unwrap();
        let h3 = compute_scope_hash(&tmp, &scope).unwrap();
        assert_ne!(h1, h3, "hash must change when file content changes");

        let _ = std::fs::remove_dir_all(&tmp);
    }
}
