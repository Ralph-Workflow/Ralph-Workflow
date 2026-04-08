use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};

use crate::io::fingerprint::{
    compute_scope_hash_with_snapshot, CacheEntry, Fnv1aHasher, RepositoryFingerprintCache,
};
use crate::io::hash::{
    append_native_scan_definition_hash, compute_command_definition_hash,
    compute_native_required_hash, compute_native_scan_hash,
};
use crate::io::scope::{scope_for, scope_memo_key, CheckScope};
use crate::runtime::verify::{CommandOutput, CommandRunner, CommandSpec};

/// On-disk format for the cache file.
#[derive(Debug, Default, Serialize, Deserialize)]
struct CacheFile {
    #[serde(default)]
    entries: HashMap<String, CacheEntry>,
    #[serde(default)]
    file_fingerprints: HashMap<String, crate::io::fingerprint::CachedFileFingerprint>,
}

struct NativeCheckCacheState {
    key: String,
    hash: u64,
}

impl NativeCheckCacheState {
    fn new(name: &str, hash: u64) -> Self {
        Self {
            key: Self::native_check_cache_key(name, hash),
            hash,
        }
    }

    fn native_check_cache_key(name: &str, hash: u64) -> String {
        format!("native-required:{}:{hash}", name)
    }
}

struct NativeScanCacheState {
    key: String,
    hash: u64,
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
    repo_fingerprint: RepositoryFingerprintCache,
    prepared_native_check_hashes: Mutex<HashMap<String, u64>>,
    prepared_command_definition_hashes: Mutex<HashMap<String, u64>>,
    prepared_native_scan_hash: Mutex<Option<(u64, u64)>>,
    /// Set to true when in-memory cache has unsaved changes.
    dirty: AtomicBool,
}

impl CachingCommandRunner {
    pub fn new(inner: impl CommandRunner + 'static, repo_root: PathBuf) -> Self {
        let cache_path = repo_root.join("target/xtask-verify-cache.json");
        let cache_file = if let Ok(data) = std::fs::read_to_string(&cache_path) {
            serde_json::from_str::<CacheFile>(&data).unwrap_or_default()
        } else {
            CacheFile::default()
        };
        Self {
            inner: Box::new(inner),
            repo_root: repo_root.clone(),
            memory: Mutex::new(cache_file.entries),
            scope_memo: Mutex::new(HashMap::new()),
            repo_fingerprint: RepositoryFingerprintCache::from_persisted(
                &repo_root,
                cache_file.file_fingerprints,
            ),
            prepared_native_check_hashes: Mutex::new(HashMap::new()),
            prepared_command_definition_hashes: Mutex::new(HashMap::new()),
            prepared_native_scan_hash: Mutex::new(None),
            dirty: AtomicBool::new(false),
        }
    }

    fn cache_path(&self) -> PathBuf {
        self.repo_root.join("target/xtask-verify-cache.json")
    }

    fn persist(&self) -> std::io::Result<()> {
        let cache_file = self.build_cache_file();
        let json = Self::serialize_cache_file(&cache_file)?;
        self.write_cache_atomically(&json)
    }

    fn build_cache_file(&self) -> CacheFile {
        let entries = self.memory.lock().unwrap().clone();
        let file_fingerprints = self
            .repo_fingerprint
            .persisted_file_fingerprints(&self.repo_root);
        CacheFile {
            entries,
            file_fingerprints,
        }
    }

    fn serialize_cache_file(file: &CacheFile) -> std::io::Result<String> {
        serde_json::to_string_pretty(file).map_err(std::io::Error::other)
    }

    fn write_cache_atomically(&self, json: &str) -> std::io::Result<()> {
        let final_path = self.cache_path();
        self.ensure_cache_directory(final_path.parent())?;
        let tmp_path = self.temp_cache_path(&final_path);
        self.write_json_to_temp(&tmp_path, json)?;
        self.replace_cache_file(tmp_path, final_path)
    }

    fn ensure_cache_directory(&self, parent: Option<&Path>) -> std::io::Result<()> {
        if let Some(dir) = parent {
            std::fs::create_dir_all(dir)?;
        }
        Ok(())
    }

    fn temp_cache_path(&self, final_path: &Path) -> PathBuf {
        let file_name = final_path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("xtask-verify-cache.json");
        final_path.with_file_name(format!("{file_name}.tmp.{}", std::process::id()))
    }

    fn write_json_to_temp(&self, tmp_path: &Path, json: &str) -> std::io::Result<()> {
        std::fs::write(tmp_path, json)
    }

    fn replace_cache_file(&self, tmp_path: PathBuf, final_path: PathBuf) -> std::io::Result<()> {
        if final_path.exists() {
            let _ = std::fs::remove_file(&final_path);
        }
        std::fs::rename(&tmp_path, &final_path)
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
        match compute_scope_hash_with_snapshot(&self.repo_root, scope, &self.repo_fingerprint) {
            Ok(h) => {
                self.scope_memo.lock().unwrap().insert(key, h);
                Some(h)
            }
            Err(_) => None,
        }
    }

    fn unique_scopes_for_checks(checks: &[CommandSpec]) -> Vec<(String, CheckScope)> {
        let mut unique = HashMap::new();
        for spec in checks {
            let scope = scope_for(spec.name);
            unique.entry(scope_memo_key(&scope)).or_insert(scope);
        }

        let mut scopes: Vec<_> = unique.into_iter().collect();
        scopes.sort_by(|left, right| left.0.cmp(&right.0));
        scopes
    }

    fn precompute_scope_hashes(
        &self,
        checks: &[CommandSpec],
    ) -> std::io::Result<Vec<(String, u64)>> {
        let scopes = Self::unique_scopes_for_checks(checks);
        if scopes.is_empty() {
            return Ok(Vec::new());
        }

        let worker_count = std::thread::available_parallelism()
            .map_or(1, |count| count.get())
            .min(scopes.len());
        let chunk_size = scopes.len().div_ceil(worker_count);

        std::thread::scope(|scope| {
            let mut handles = Vec::new();
            for chunk in scopes.chunks(chunk_size) {
                handles.push(scope.spawn(move || {
                    chunk
                        .iter()
                        .map(|(key, scope)| {
                            compute_scope_hash_with_snapshot(
                                &self.repo_root,
                                scope,
                                &self.repo_fingerprint,
                            )
                            .map(|hash| (key.clone(), hash))
                        })
                        .collect::<std::io::Result<Vec<_>>>()
                }));
            }

            let mut prepared = Vec::new();
            for handle in handles {
                prepared.extend(handle.join().expect("scope hash worker panicked")?);
            }
            prepared.sort_by(|left, right| left.0.cmp(&right.0));
            Ok(prepared)
        })
    }

    fn native_scan_definition_hash(checks: &[crate::io::scanner::NativeScanCheck]) -> u64 {
        let mut hasher = Fnv1aHasher::new();
        append_native_scan_definition_hash(&mut hasher, checks);
        hasher.finish()
    }

    fn precompute_native_required_hashes(
        &self,
        checks: &[crate::runtime::verify::NativeCheck],
    ) -> std::io::Result<Vec<(String, u64)>> {
        checks
            .iter()
            .map(|check| {
                compute_native_required_hash(&self.repo_root, check, &self.repo_fingerprint)
                    .map(|hash| (check.name.to_string(), hash))
            })
            .collect()
    }

    fn precompute_command_definition_hashes(
        &self,
        checks: &[CommandSpec],
    ) -> std::io::Result<Vec<(String, u64)>> {
        checks
            .iter()
            .map(|spec| {
                compute_command_definition_hash(&self.repo_root, spec, &self.repo_fingerprint)
                    .map(|hash| (spec.name.to_string(), hash))
            })
            .collect()
    }

    fn native_required_hash(
        &self,
        repo_root: &Path,
        check: &crate::runtime::verify::NativeCheck,
    ) -> std::io::Result<u64> {
        if let Some(hash) = self
            .prepared_native_check_hashes
            .lock()
            .unwrap()
            .get(check.name)
            .copied()
        {
            return Ok(hash);
        }

        compute_native_required_hash(repo_root, check, &self.repo_fingerprint)
    }

    fn command_definition_hash(&self, spec: &CommandSpec) -> std::io::Result<u64> {
        if let Some(hash) = self
            .prepared_command_definition_hashes
            .lock()
            .unwrap()
            .get(spec.name)
            .copied()
        {
            return Ok(hash);
        }

        compute_command_definition_hash(&self.repo_root, spec, &self.repo_fingerprint)
    }

    pub fn run_native_scan(
        &self,
        repo_root: &Path,
        checks: &[crate::io::scanner::NativeScanCheck],
        progress: &(dyn Fn(&str, &str) + Sync),
    ) -> std::io::Result<Vec<crate::io::scanner::NativeScanCheckResult>> {
        let state = self.prepare_native_scan_cache_state(repo_root, checks)?;
        self.run_native_scan_impl(repo_root, checks, progress, state)
    }

    fn prepare_native_scan_cache_state(
        &self,
        repo_root: &Path,
        checks: &[crate::io::scanner::NativeScanCheck],
    ) -> std::io::Result<NativeScanCacheState> {
        let definition_hash = Self::native_scan_definition_hash(checks);
        let hash = self.ensure_native_scan_hash(repo_root, definition_hash, checks)?;
        Ok(NativeScanCacheState {
            key: Self::native_scan_cache_key(hash),
            hash,
        })
    }

    fn run_native_scan_impl(
        &self,
        repo_root: &Path,
        checks: &[crate::io::scanner::NativeScanCheck],
        progress: &(dyn Fn(&str, &str) + Sync),
        state: NativeScanCacheState,
    ) -> std::io::Result<Vec<crate::io::scanner::NativeScanCheckResult>> {
        if let Some(results) = self.native_scan_cached_results(&state.key, checks, progress) {
            return Ok(results);
        }

        let results =
            crate::io::scanner::run_native_scan_checks_reporting(repo_root, checks, progress);
        if Self::should_cache_native_scan(&results) {
            self.insert_native_scan_cache(state.key.clone(), state.hash);
        }
        Ok(results)
    }

    fn native_scan_cache_key(hash: u64) -> String {
        format!("native-scan:{hash}")
    }

    fn native_scan_cached_results(
        &self,
        key: &str,
        checks: &[crate::io::scanner::NativeScanCheck],
        progress: &(dyn Fn(&str, &str) + Sync),
    ) -> Option<Vec<crate::io::scanner::NativeScanCheckResult>> {
        let has_entry = { self.memory.lock().unwrap().contains_key(key) };
        if has_entry {
            progress("native-scan", "cache hit");
            Some(Self::native_scan_results_from_checks(checks))
        } else {
            None
        }
    }

    fn native_scan_results_from_checks(
        checks: &[crate::io::scanner::NativeScanCheck],
    ) -> Vec<crate::io::scanner::NativeScanCheckResult> {
        checks
            .iter()
            .map(|check| crate::io::scanner::NativeScanCheckResult {
                check_name: check.name,
                passed: true,
                violations: Vec::new(),
            })
            .collect()
    }

    fn should_cache_native_scan(results: &[crate::io::scanner::NativeScanCheckResult]) -> bool {
        results.iter().all(|result| result.passed)
    }

    fn insert_native_scan_cache(&self, key: String, hash: u64) {
        self.memory.lock().unwrap().insert(
            key,
            CacheEntry {
                scope_hash: hash,
                exit_code: 0,
                stdout: String::new(),
                stderr: String::new(),
            },
        );
        self.dirty.store(true, Ordering::Relaxed);
    }

    fn native_check_cached_result(
        &self,
        key: &str,
    ) -> Option<crate::runtime::verify::NativeCheckResult> {
        let memory = self.memory.lock().unwrap();
        (memory.contains_key(key)).then(|| crate::runtime::verify::NativeCheckResult {
            status: crate::runtime::verify::CheckStatus::Pass,
            message: String::new(),
        })
    }

    fn should_cache_native_check(result: &crate::runtime::verify::NativeCheckResult) -> bool {
        result.status == crate::runtime::verify::CheckStatus::Pass
    }

    fn insert_native_check_cache(&self, key: String, hash: u64, message: String) {
        self.memory.lock().unwrap().insert(
            key,
            CacheEntry {
                scope_hash: hash,
                exit_code: 0,
                stdout: message,
                stderr: String::new(),
            },
        );
        self.dirty.store(true, Ordering::Relaxed);
    }

    fn ensure_native_scan_hash(
        &self,
        repo_root: &Path,
        definition_hash: u64,
        checks: &[crate::io::scanner::NativeScanCheck],
    ) -> std::io::Result<u64> {
        self.prepared_native_scan_hash
            .lock()
            .unwrap()
            .and_then(|(prepared_definition_hash, prepared_hash)| {
                (prepared_definition_hash == definition_hash).then_some(prepared_hash)
            })
            .map_or_else(
                || compute_native_scan_hash(repo_root, checks, &self.repo_fingerprint),
                Ok,
            )
    }

    fn compute_run_cache_key(&self, spec: &CommandSpec) -> std::io::Result<Option<(u64, String)>> {
        let scope = scope_for(spec.name);
        let scope_hash = self.compute_or_cached_scope_hash(&scope);
        if let Some(hash) = scope_hash {
            let verifier_hash = self.command_definition_hash(spec)?;
            return Ok(Some((
                hash,
                format!("{}:{}:{verifier_hash}", spec.name, hash),
            )));
        }
        Ok(None)
    }

    fn try_cache_hit(&self, key: &str, hash: u64) -> Option<CommandOutput> {
        let mem = self.memory.lock().unwrap();
        if let Some(entry) = mem.get(key) {
            if entry.scope_hash == hash {
                return Some(CommandOutput {
                    exit_code: entry.exit_code,
                    stdout: entry.stdout.clone(),
                    stderr: entry.stderr.clone(),
                });
            }
        }
        None
    }

    fn record_command_output(
        &self,
        spec: &CommandSpec,
        hash: u64,
        key: String,
    ) -> std::io::Result<CommandOutput> {
        let output = self.inner.run(spec)?;

        if crate::runtime::verify::is_cacheable_success_output(
            spec.name,
            &output,
            spec.success_exit_codes,
        ) {
            self.memory.lock().unwrap().insert(
                key,
                CacheEntry {
                    scope_hash: hash,
                    exit_code: output.exit_code,
                    stdout: output.stdout.clone(),
                    stderr: output.stderr.clone(),
                },
            );
            self.dirty.store(true, Ordering::Relaxed);
        }

        Ok(output)
    }
}

impl CommandRunner for CachingCommandRunner {
    fn prepare_for_verify(
        &self,
        _repo_root: &Path,
        native_checks: &[crate::runtime::verify::NativeCheck],
        checks: &[CommandSpec],
        native_scan_checks: &[crate::io::scanner::NativeScanCheck],
    ) -> std::io::Result<()> {
        let prepared_native_checks = self.precompute_native_required_hashes(native_checks)?;
        {
            let mut memo = self.prepared_native_check_hashes.lock().unwrap();
            memo.clear();
            memo.extend(prepared_native_checks);
        }

        let prepared_command_hashes = self.precompute_command_definition_hashes(checks)?;
        {
            let mut memo = self.prepared_command_definition_hashes.lock().unwrap();
            memo.clear();
            memo.extend(prepared_command_hashes);
        }

        let prepared_hashes = self.precompute_scope_hashes(checks)?;
        {
            let mut memo = self.scope_memo.lock().unwrap();
            memo.extend(prepared_hashes);
        }

        let prepared_native_hash = if native_scan_checks.is_empty() {
            None
        } else {
            Some((
                Self::native_scan_definition_hash(native_scan_checks),
                compute_native_scan_hash(
                    &self.repo_root,
                    native_scan_checks,
                    &self.repo_fingerprint,
                )?,
            ))
        };
        *self.prepared_native_scan_hash.lock().unwrap() = prepared_native_hash;
        Ok(())
    }

    fn run_native_check(
        &self,
        repo_root: &Path,
        check: &crate::runtime::verify::NativeCheck,
    ) -> std::io::Result<crate::runtime::verify::NativeCheckResult> {
        let hash = self.native_required_hash(repo_root, check)?;
        let state = NativeCheckCacheState::new(check.name, hash);

        if let Some(cached) = self.native_check_cached_result(&state.key) {
            return Ok(cached);
        }

        let result = self.inner.run_native_check(repo_root, check)?;
        if Self::should_cache_native_check(&result) {
            self.insert_native_check_cache(state.key, state.hash, result.message.clone());
        }
        Ok(result)
    }

    fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        if let Some((hash, key)) = self.compute_run_cache_key(spec)? {
            if let Some(output) = self.try_cache_hit(&key, hash) {
                return Ok(output);
            }
            return self.record_command_output(spec, hash, key);
        }

        self.inner.run(spec)
    }

    fn run_native_scan(
        &self,
        repo_root: &Path,
        checks: &[crate::io::scanner::NativeScanCheck],
        progress: &(dyn Fn(&str, &str) + Sync),
    ) -> std::io::Result<Vec<crate::io::scanner::NativeScanCheckResult>> {
        CachingCommandRunner::run_native_scan(self, repo_root, checks, progress)
    }
}

#[cfg(test)]
mod tests;
