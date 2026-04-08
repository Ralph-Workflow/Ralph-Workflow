//! Filesystem fingerprinting for content-addressed caching.
//!
//! Provides types and functions for computing stable file fingerprints
//! based on content (not mtime), enabling cache invalidation that survives
//! git checkout round-trips.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::SystemTime;

use serde::{Deserialize, Serialize};

/// A minimal FNV-1a 64-bit hasher.  Stable across Rust versions and platforms.
///
/// FNV-1a is significantly faster than SipHash (the default) for short inputs
/// (file paths), which constitute the majority of hashed bytes.
pub struct Fnv1aHasher(u64);

impl Fnv1aHasher {
    const OFFSET_BASIS: u64 = 14_695_981_039_346_656_037;
    const PRIME: u64 = 1_099_511_628_211;

    pub(crate) fn new() -> Self {
        Self(Self::OFFSET_BASIS)
    }

    pub(crate) fn write_bytes(&mut self, bytes: &[u8]) {
        for &b in bytes {
            self.0 ^= b as u64;
            self.0 = self.0.wrapping_mul(Self::PRIME);
        }
    }

    pub(crate) fn finish(self) -> u64 {
        self.0
    }
}

impl Default for Fnv1aHasher {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CacheEntry {
    /// Hash of the check scope (file paths + content bytes, sorted by path).
    pub scope_hash: u64,
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct FileTimestamp {
    seconds_since_epoch: u64,
    nanos_since_second: u32,
}

impl FileTimestamp {
    fn from_system_time(time: SystemTime) -> Option<Self> {
        let duration = time.duration_since(SystemTime::UNIX_EPOCH).ok()?;
        Some(Self {
            seconds_since_epoch: duration.as_secs(),
            nanos_since_second: duration.subsec_nanos(),
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct FileFingerprintMetadata {
    len: u64,
    modified: Option<FileTimestamp>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub struct CachedFileFingerprint {
    pub len: u64,
    pub modified: Option<FileTimestamp>,
    pub digest: u64,
    #[serde(skip)]
    trust_metadata_match: bool,
}

impl CachedFileFingerprint {
    fn metadata(self) -> FileFingerprintMetadata {
        FileFingerprintMetadata {
            len: self.len,
            modified: self.modified,
        }
    }

    fn can_reuse_for_metadata(self, path: &Path, metadata: FileFingerprintMetadata) -> bool {
        self.trust_metadata_match
            && metadata.modified.is_some()
            && self.metadata() == metadata
            && std::fs::File::open(path).is_ok()
    }
}

#[derive(Default)]
pub struct RepositoryFingerprintCache {
    pub(crate) glob_memo: Mutex<HashMap<String, Vec<PathBuf>>>,
    pub(crate) file_fingerprints: Mutex<HashMap<PathBuf, CachedFileFingerprint>>,
}

impl RepositoryFingerprintCache {
    pub(crate) fn from_persisted(
        repo_root: &Path,
        persisted: HashMap<String, CachedFileFingerprint>,
    ) -> Self {
        let file_fingerprints = persisted
            .into_iter()
            .map(|(path, mut fingerprint)| {
                fingerprint.trust_metadata_match = fingerprint.modified.is_some();
                (repo_root.join(path), fingerprint)
            })
            .collect();
        Self {
            glob_memo: Mutex::new(HashMap::new()),
            file_fingerprints: Mutex::new(file_fingerprints),
        }
    }

    pub(crate) fn persisted_file_fingerprints(
        &self,
        repo_root: &Path,
    ) -> HashMap<String, CachedFileFingerprint> {
        self.file_fingerprints
            .lock()
            .unwrap()
            .iter()
            .filter_map(|(path, fingerprint)| {
                let relative = path.strip_prefix(repo_root).ok()?;
                Some((relative.to_string_lossy().into_owned(), *fingerprint))
            })
            .collect()
    }

    pub(crate) fn collect_globbed_paths(
        &self,
        repo_root: &Path,
        rel_dir: &str,
        pattern: &str,
    ) -> std::io::Result<Vec<PathBuf>> {
        self.collect_globbed_paths_excluding(repo_root, rel_dir, pattern, &[])
    }

    pub(crate) fn collect_globbed_paths_excluding(
        &self,
        repo_root: &Path,
        rel_dir: &str,
        pattern: &str,
        exclude_globs: &[&str],
    ) -> std::io::Result<Vec<PathBuf>> {
        let key = Self::glob_memo_key(rel_dir, pattern, exclude_globs);
        if let Some(paths) = self.glob_memo.lock().unwrap().get(&key).cloned() {
            return Ok(paths);
        }

        let paths = Self::gather_globbed_paths(repo_root, rel_dir, pattern, exclude_globs)?;
        self.glob_memo.lock().unwrap().insert(key, paths.clone());
        Ok(paths)
    }

    fn glob_memo_key(rel_dir: &str, pattern: &str, exclude_globs: &[&str]) -> String {
        format!("{rel_dir}@{pattern}@{}", exclude_globs.join(","))
    }

    fn gather_globbed_paths(
        repo_root: &Path,
        rel_dir: &str,
        pattern: &str,
        exclude_globs: &[&str],
    ) -> std::io::Result<Vec<PathBuf>> {
        let full = repo_root.join(rel_dir);
        if !full.exists() {
            return Ok(Vec::new());
        }

        let mut paths = Vec::new();
        crate::io::scanner::collect_files_with_glob_excluding(
            &full,
            pattern,
            exclude_globs,
            &mut paths,
        )?;
        paths.sort();
        paths.dedup();
        Ok(paths)
    }

    fn file_metadata(path: &Path) -> std::io::Result<FileFingerprintMetadata> {
        let metadata = std::fs::metadata(path)?;
        Ok(FileFingerprintMetadata {
            len: metadata.len(),
            modified: metadata
                .modified()
                .ok()
                .and_then(FileTimestamp::from_system_time),
        })
    }

    pub(crate) fn read_file_digest(&self, path: &Path) -> std::io::Result<u64> {
        let metadata = Self::file_metadata(path)?;
        if let Some(digest) = self.try_reuse_cached_digest(path, metadata) {
            return Ok(digest);
        }
        self.compute_and_store_digest(path, metadata)
    }

    fn try_reuse_cached_digest(
        &self,
        path: &Path,
        metadata: FileFingerprintMetadata,
    ) -> Option<u64> {
        self.file_fingerprints
            .lock()
            .unwrap()
            .get(path)
            .copied()
            .and_then(|cached| {
                if cached.can_reuse_for_metadata(path, metadata) {
                    Some(cached.digest)
                } else {
                    None
                }
            })
    }

    fn compute_and_store_digest(
        &self,
        path: &Path,
        metadata: FileFingerprintMetadata,
    ) -> std::io::Result<u64> {
        let bytes = std::fs::read(path)?;
        let mut hasher = Fnv1aHasher::new();
        hasher.write_bytes(&bytes);
        let digest = hasher.finish();

        self.file_fingerprints.lock().unwrap().insert(
            path.to_path_buf(),
            CachedFileFingerprint {
                len: metadata.len,
                modified: metadata.modified,
                digest,
                trust_metadata_match: true,
            },
        );
        Ok(digest)
    }
}

/// Compute a u64 hash of the scope by iterating relevant files and
/// hashing (path, content bytes) pairs. Content-based hashing ensures
/// cache keys are stable across mtime changes (e.g., git checkout round-trips).
#[cfg(test)]
pub fn compute_scope_hash(
    repo_root: &Path,
    scope: &crate::io::scope::CheckScope,
) -> std::io::Result<u64> {
    compute_scope_hash_with_snapshot(repo_root, scope, &RepositoryFingerprintCache::default())
}

pub fn compute_scope_hash_with_snapshot(
    repo_root: &Path,
    scope: &crate::io::scope::CheckScope,
    snapshot: &RepositoryFingerprintCache,
) -> std::io::Result<u64> {
    let mut hasher = Fnv1aHasher::new();
    hasher.write_bytes(crate::io::scope::SCOPE_HASH_VERSION);
    let mut all_paths: Vec<PathBuf> = Vec::new();

    collect_scope_paths(repo_root, scope, snapshot, &mut all_paths)?;

    all_paths.sort();
    all_paths.dedup();

    for path in &all_paths {
        let relative = path.strip_prefix(repo_root).unwrap_or(path);
        hasher.write_bytes(relative.to_string_lossy().as_bytes());
        hasher.write_bytes(&snapshot.read_file_digest(path)?.to_le_bytes());
    }

    Ok(hasher.finish())
}

fn collect_scope_paths(
    repo_root: &Path,
    scope: &crate::io::scope::CheckScope,
    snapshot: &RepositoryFingerprintCache,
    paths: &mut Vec<PathBuf>,
) -> std::io::Result<()> {
    match scope {
        crate::io::scope::CheckScope::Directories(dirs)
        | crate::io::scope::CheckScope::Build(dirs)
        | crate::io::scope::CheckScope::BuildWithExtras { dirs, .. } => {
            collect_directory_scope_paths(repo_root, dirs, scope, snapshot, paths)?;
        }
        crate::io::scope::CheckScope::Patterns {
            globs,
            files,
            include_lock,
        } => {
            collect_pattern_scope_paths(repo_root, globs, files, *include_lock, snapshot, paths)?;
        }
    }
    Ok(())
}

fn collect_directory_scope_paths(
    repo_root: &Path,
    dirs: &[&str],
    scope: &crate::io::scope::CheckScope,
    snapshot: &RepositoryFingerprintCache,
    paths: &mut Vec<PathBuf>,
) -> std::io::Result<()> {
    collect_paths_for_dirs(repo_root, dirs, snapshot, paths)?;
    append_config_candidates(
        repo_root,
        matches!(
            scope,
            crate::io::scope::CheckScope::Build(_)
                | crate::io::scope::CheckScope::BuildWithExtras { .. }
        ),
        paths,
    );

    if let crate::io::scope::CheckScope::BuildWithExtras { globs, files, .. } = scope {
        collect_build_with_extras(globs, files, repo_root, snapshot, paths)?;
    }

    Ok(())
}

fn extend_paths_for_dir(
    repo_root: &Path,
    dir: &str,
    snapshot: &RepositoryFingerprintCache,
    paths: &mut Vec<PathBuf>,
) -> std::io::Result<()> {
    paths.extend(snapshot.collect_globbed_paths(repo_root, dir, "*.rs")?);
    Ok(())
}

fn append_parent_manifests(repo_root: &Path, dir: &str, paths: &mut Vec<PathBuf>) {
    let start = repo_root.join(dir);
    for ancestor in start.ancestors() {
        let manifest = ancestor.join("Cargo.toml");
        if manifest.exists() {
            paths.push(manifest);
        }
        if ancestor == repo_root {
            break;
        }
    }
}

fn append_config_candidates(repo_root: &Path, include_lock: bool, paths: &mut Vec<PathBuf>) {
    append_config_files(repo_root, paths);
    append_lock_if_needed(repo_root, include_lock, paths);
}

fn collect_paths_for_dirs(
    repo_root: &Path,
    dirs: &[&str],
    snapshot: &RepositoryFingerprintCache,
    paths: &mut Vec<PathBuf>,
) -> std::io::Result<()> {
    for dir in dirs {
        collect_paths_for_dir(repo_root, dir, snapshot, paths)?;
    }
    Ok(())
}

fn collect_paths_for_dir(
    repo_root: &Path,
    dir: &str,
    snapshot: &RepositoryFingerprintCache,
    paths: &mut Vec<PathBuf>,
) -> std::io::Result<()> {
    extend_paths_for_dir(repo_root, dir, snapshot, paths)?;
    append_parent_manifests(repo_root, dir, paths);
    Ok(())
}

fn append_config_files(repo_root: &Path, paths: &mut Vec<PathBuf>) {
    const CONFIG_FILES: &[&str] = &[
        "Cargo.toml",
        "rustfmt.toml",
        "clippy.toml",
        ".cargo/config.toml",
        ".cargo/config",
        "rust-toolchain.toml",
        "rust-toolchain",
        "Makefile",
    ];

    for rel in CONFIG_FILES {
        let path = repo_root.join(rel);
        if path.exists() {
            paths.push(path);
        }
    }
}

fn append_lock_if_needed(repo_root: &Path, include_lock: bool, paths: &mut Vec<PathBuf>) {
    if include_lock {
        let lock_path = repo_root.join("Cargo.lock");
        if lock_path.exists() {
            paths.push(lock_path);
        }
    }
}

fn collect_build_globs(
    globs: &[crate::io::scope::ScopeGlob],
    repo_root: &Path,
    snapshot: &RepositoryFingerprintCache,
    paths: &mut Vec<PathBuf>,
) -> std::io::Result<()> {
    for glob in globs {
        paths.extend(snapshot.collect_globbed_paths(repo_root, glob.dir, glob.pattern)?);
    }
    Ok(())
}

fn collect_extra_files(files: &[&str], repo_root: &Path, paths: &mut Vec<PathBuf>) {
    for rel in files {
        let path = repo_root.join(rel);
        if path.exists() {
            paths.push(path);
        }
    }
}

fn collect_pattern_globs(
    globs: &[crate::io::scope::ScopeGlob],
    repo_root: &Path,
    snapshot: &RepositoryFingerprintCache,
    paths: &mut Vec<PathBuf>,
) -> std::io::Result<()> {
    for glob in globs {
        paths.extend(snapshot.collect_globbed_paths(repo_root, glob.dir, glob.pattern)?);
    }
    Ok(())
}

fn collect_pattern_files(files: &[&str], repo_root: &Path, paths: &mut Vec<PathBuf>) {
    for rel in files {
        let path = repo_root.join(rel);
        if path.exists() {
            paths.push(path);
        }
    }
}

fn collect_build_with_extras(
    globs: &[crate::io::scope::ScopeGlob],
    files: &[&str],
    repo_root: &Path,
    snapshot: &RepositoryFingerprintCache,
    paths: &mut Vec<PathBuf>,
) -> std::io::Result<()> {
    collect_build_globs(globs, repo_root, snapshot, paths)?;
    collect_extra_files(files, repo_root, paths);
    Ok(())
}

fn collect_pattern_scope_paths(
    repo_root: &Path,
    globs: &[crate::io::scope::ScopeGlob],
    files: &[&str],
    include_lock: bool,
    snapshot: &RepositoryFingerprintCache,
    paths: &mut Vec<PathBuf>,
) -> std::io::Result<()> {
    collect_pattern_globs(globs, repo_root, snapshot, paths)?;
    collect_pattern_files(files, repo_root, paths);
    append_lock_if_needed(repo_root, include_lock, paths);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fnv1a_hasher_produces_consistent_hash() {
        let mut hasher1 = Fnv1aHasher::new();
        hasher1.write_bytes(b"hello");
        let result1 = hasher1.finish();

        let mut hasher2 = Fnv1aHasher::new();
        hasher2.write_bytes(b"hello");
        let result2 = hasher2.finish();

        assert_eq!(result1, result2);
    }

    #[test]
    fn test_fnv1a_hasher_different_inputs_produce_different_hashes() {
        let mut hasher1 = Fnv1aHasher::new();
        hasher1.write_bytes(b"hello");
        let result1 = hasher1.finish();

        let mut hasher2 = Fnv1aHasher::new();
        hasher2.write_bytes(b"world");
        let result2 = hasher2.finish();

        assert_ne!(result1, result2);
    }
}
