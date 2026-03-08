use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};

use crate::verify::{CheckStatus, CommandOutput, CommandRunner, CommandSpec};

/// Cached result for a single check.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CacheEntry {
    /// Hash of the check scope (file path+size+mtime tuples, sorted).
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

/// Returns the scope for a given check name. Checks not listed here
/// are assumed to have Build scope (most conservative: any change triggers re-run).
pub fn scope_for(check_name: &str) -> CheckScope {
    match check_name {
        // rg checks on ralph-workflow/src/
        "forbidden-allow-expect-scan"
        | "no-test-flags-cfg-test"
        | "no-test-flags-test-mode-params"
        | "no-test-flags-skip-params"
        | "no-test-flags-mock-params"
        | "no-test-flags-testing-feature"
        | "no-test-flags-cfg-not-test"
        | "audit-no-serial-src"
        | "audit-no-test-helpers-src"
        | "no-string-errors-handlers" => CheckScope::Directories(&["ralph-workflow/src"]),
        // rg checks on tests/integration_tests/
        "compliance-no-process-spawn"
        | "compliance-no-serial"
        | "audit-no-cfg-test-integration"
        | "audit-no-real-fs-integration"
        | "audit-no-real-process-integration"
        | "audit-no-env-mutations-integration"
        | "audit-no-shell-scripts" => CheckScope::Directories(&["tests/integration_tests"]),
        // checks spanning both src and tests
        "audit-ignore-has-url" => CheckScope::Directories(&["tests", "ralph-workflow/src"]),
        "audit-no-serial-process-system" | "audit-no-git2-process-system" => {
            CheckScope::Directories(&["tests/process_system_tests"])
        }
        // All cargo-based checks use the full build scope
        _ => CheckScope::Build(&["ralph-workflow/src", "tests", "xtask/src"]),
    }
}

/// Compute a u64 hash of the scope by iterating relevant files and
/// hashing (path, size, modified_time) tuples.
pub fn compute_scope_hash(repo_root: &Path, scope: &CheckScope) -> u64 {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut hasher = DefaultHasher::new();

    let dirs = match scope {
        CheckScope::Directories(dirs) => dirs,
        CheckScope::Build(dirs) => {
            // Include Cargo.lock in hash.
            let lock = repo_root.join("Cargo.lock");
            if let Ok(meta) = std::fs::metadata(&lock) {
                lock.to_string_lossy().hash(&mut hasher);
                meta.len().hash(&mut hasher);
                if let Ok(t) = meta.modified() {
                    t.hash(&mut hasher);
                }
            }
            dirs
        }
    };

    let mut entries: Vec<(PathBuf, u64, Option<std::time::SystemTime>)> = Vec::new();
    for dir in *dirs {
        let full = repo_root.join(dir);
        collect_rs_files(&full, &mut entries);
    }
    entries.sort_by(|a, b| a.0.cmp(&b.0));
    for (path, size, mtime) in entries {
        path.to_string_lossy().hash(&mut hasher);
        size.hash(&mut hasher);
        if let Some(t) = mtime {
            t.hash(&mut hasher);
        }
    }
    hasher.finish()
}

fn collect_rs_files(dir: &Path, out: &mut Vec<(PathBuf, u64, Option<std::time::SystemTime>)>) {
    let Ok(rd) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in rd.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_rs_files(&path, out);
        } else if path.extension().and_then(|e| e.to_str()) == Some("rs") {
            if let Ok(meta) = entry.metadata() {
                out.push((path, meta.len(), meta.modified().ok()));
            }
        }
    }
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
pub struct CachingCommandRunner {
    inner: Box<dyn CommandRunner + Send + Sync>,
    repo_root: PathBuf,
    memory: Mutex<HashMap<String, CacheEntry>>,
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
}

impl CommandRunner for CachingCommandRunner {
    fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        let scope = scope_for(spec.name);
        let hash = compute_scope_hash(&self.repo_root, &scope);
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
        let is_success = spec.success_exit_codes.contains(&output.exit_code)
            && classify_output(&output.stdout, &output.stderr) == CheckStatus::Pass;
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
            self.persist();
        }

        Ok(output)
    }
}

/// Check whether output contains error or warning diagnostics.
fn classify_output(stdout: &str, stderr: &str) -> CheckStatus {
    fn has_prefix(s: &str, prefix: &str) -> bool {
        s.lines().any(|l| l.trim_start().starts_with(prefix))
    }
    if has_prefix(stderr, "error:")
        || has_prefix(stdout, "error:")
        || has_prefix(stderr, "Error:")
        || has_prefix(stdout, "Error:")
    {
        return CheckStatus::Error;
    }
    if has_prefix(stderr, "warning:") || has_prefix(stdout, "warning:") {
        return CheckStatus::Warning;
    }
    CheckStatus::Pass
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
}
