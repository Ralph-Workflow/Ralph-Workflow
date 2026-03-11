use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::SystemTime;

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
    /// Hash a build scope plus additional non-Rust files or directories watched at compile time.
    BuildWithExtras {
        dirs: &'static [&'static str],
        globs: &'static [ScopeGlob],
        files: &'static [&'static str],
    },
    /// Hash explicitly selected files and globbed inputs.
    Patterns {
        globs: &'static [ScopeGlob],
        files: &'static [&'static str],
        include_lock: bool,
    },
}

#[derive(Default)]
pub struct RepositoryFingerprintCache {
    glob_memo: Mutex<HashMap<String, Vec<PathBuf>>>,
    file_fingerprints: Mutex<HashMap<PathBuf, CachedFileFingerprint>>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
struct FileTimestamp {
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
struct CachedFileFingerprint {
    len: u64,
    modified: Option<FileTimestamp>,
    digest: u64,
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

    fn can_reuse_for_metadata(self, metadata: FileFingerprintMetadata) -> bool {
        self.trust_metadata_match && metadata.modified.is_some() && self.metadata() == metadata
    }
}

impl RepositoryFingerprintCache {
    fn from_persisted(repo_root: &Path, persisted: HashMap<String, CachedFileFingerprint>) -> Self {
        let file_fingerprints = persisted
            .into_iter()
            .map(|(path, mut fingerprint)| {
                fingerprint.trust_metadata_match = false;
                (repo_root.join(path), fingerprint)
            })
            .collect();
        Self {
            glob_memo: Mutex::new(HashMap::new()),
            file_fingerprints: Mutex::new(file_fingerprints),
        }
    }

    fn persisted_file_fingerprints(
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

    fn collect_globbed_paths(
        &self,
        repo_root: &Path,
        rel_dir: &str,
        pattern: &str,
    ) -> std::io::Result<Vec<PathBuf>> {
        let key = format!("{rel_dir}@{pattern}");
        if let Some(paths) = self.glob_memo.lock().unwrap().get(&key).cloned() {
            return Ok(paths);
        }

        let full = repo_root.join(rel_dir);
        let mut paths = Vec::new();
        if full.exists() {
            crate::scanner::collect_files_with_glob(&full, pattern, &mut paths)?;
            paths.sort();
            paths.dedup();
        }

        self.glob_memo.lock().unwrap().insert(key, paths.clone());
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

    fn read_file_digest(&self, path: &Path) -> std::io::Result<u64> {
        let metadata = Self::file_metadata(path)?;

        if let Some(cached) = self.file_fingerprints.lock().unwrap().get(path).copied() {
            if cached.can_reuse_for_metadata(metadata) {
                return Ok(cached.digest);
            }
        }

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

const RALPH_GUI_RUST_SCOPE_DIRS: &[&str] = &["ralph-gui", "ralph-workflow/src"];
const RALPH_GUI_BUILD_EXTRA_GLOBS: &[ScopeGlob] = &[
    ScopeGlob {
        dir: "ralph-gui/capabilities",
        pattern: "*",
    },
    ScopeGlob {
        dir: "ralph-gui/icons",
        pattern: "*",
    },
];
const RALPH_GUI_BUILD_EXTRA_FILES: &[&str] = &["ralph-gui/tauri.conf.json"];
const RALPH_WORKFLOW_COMPILE_TIME_EXTRA_GLOBS: &[ScopeGlob] = &[
    ScopeGlob {
        dir: "templates/prompts",
        pattern: "*",
    },
    ScopeGlob {
        dir: "ralph-workflow/src/prompts/templates",
        pattern: "*",
    },
    ScopeGlob {
        dir: "ralph-workflow/src/files/llm_output_extraction",
        pattern: "*",
    },
];
const RALPH_WORKFLOW_COMPILE_TIME_EXTRA_FILES: &[&str] = &[];
const INTEGRATION_TEST_AND_RALPH_WORKFLOW_EXTRA_GLOBS: &[ScopeGlob] = &[
    ScopeGlob {
        dir: "tests/integration_tests/artifacts",
        pattern: "*",
    },
    ScopeGlob {
        dir: "templates/prompts",
        pattern: "*",
    },
    ScopeGlob {
        dir: "ralph-workflow/src/prompts/templates",
        pattern: "*",
    },
    ScopeGlob {
        dir: "ralph-workflow/src/files/llm_output_extraction",
        pattern: "*",
    },
];
const DYLINT_SCOPE_GLOBS: &[ScopeGlob] = &[
    ScopeGlob {
        dir: "ralph-workflow/src",
        pattern: "*.rs",
    },
    ScopeGlob {
        dir: "lints/file_too_long/src",
        pattern: "*.rs",
    },
];
const DYLINT_SCOPE_FILES: &[&str] = &[
    "Cargo.toml",
    "Cargo.lock",
    "Makefile",
    "rust-toolchain.toml",
    "rust-toolchain",
    ".cargo/config.toml",
    ".cargo/config",
    "clippy.toml",
    "lints/file_too_long/Cargo.toml",
    "lints/file_too_long/Cargo.lock",
    "lints/file_too_long/.cargo/config.toml",
    "lints/file_too_long/rust-toolchain.toml",
    "lints/file_too_long/dylint-link",
    "lints/file_too_long/rustc-nightly",
];
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
const FORBIDDEN_ALLOW_EXPECT_SCOPE_GLOBS: &[ScopeGlob] = &[
    ScopeGlob {
        dir: "ralph-workflow/src",
        pattern: "*.rs",
    },
    ScopeGlob {
        dir: "tests",
        pattern: "*.rs",
    },
    ScopeGlob {
        dir: "xtask/src",
        pattern: "*.rs",
    },
    ScopeGlob {
        dir: "test-helpers/src",
        pattern: "*.rs",
    },
    ScopeGlob {
        dir: "ralph-gui/src",
        pattern: "*.rs",
    },
];
const FORBIDDEN_ALLOW_EXPECT_SCOPE_FILES: &[&str] = &["ralph-gui/build.rs"];
const SCOPE_HASH_VERSION: &[u8] = b"scope-v2";
const NATIVE_SCAN_HASH_VERSION: &[u8] = b"native-scan-v2";
const NATIVE_REQUIRED_HASH_VERSION: &[u8] = b"native-required-v1";
const COMMAND_DEFINITION_HASH_VERSION: &[u8] = b"command-definition-v1";

/// Returns a stable string key for a scope, used for in-process memoization.
/// The key encodes both the scope type (directories vs build) and the directory list.
pub fn scope_memo_key(scope: &CheckScope) -> String {
    match scope {
        CheckScope::Directories(dirs) => format!("d:{}", dirs.join(",")),
        CheckScope::Build(dirs) => format!("b:{}", dirs.join(",")),
        CheckScope::BuildWithExtras { dirs, globs, files } => {
            let glob_key = globs
                .iter()
                .map(|glob| format!("{}@{}", glob.dir, glob.pattern))
                .collect::<Vec<_>>()
                .join(",");
            format!("bx:{}:{glob_key}:{}", dirs.join(","), files.join(","))
        }
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
        "forbidden-allow-expect-scan" => CheckScope::Patterns {
            globs: FORBIDDEN_ALLOW_EXPECT_SCOPE_GLOBS,
            files: FORBIDDEN_ALLOW_EXPECT_SCOPE_FILES,
            include_lock: false,
        },
        // fmt-check: only .rs file content matters, not Cargo.lock
        "fmt-check" => CheckScope::Directories(&[
            "ralph-workflow/src",
            "tests",
            "xtask/src",
            "test-helpers/src",
            "ralph-gui",
        ]),
        // clippy-core spans ralph-workflow + ralph-workflow-tests + test-helpers
        "clippy-core" => CheckScope::BuildWithExtras {
            dirs: &["ralph-workflow/src", "tests", "test-helpers/src"],
            globs: RALPH_WORKFLOW_COMPILE_TIME_EXTRA_GLOBS,
            files: RALPH_WORKFLOW_COMPILE_TIME_EXTRA_FILES,
        },

        "test-ralph-workflow-lib" => CheckScope::BuildWithExtras {
            dirs: &["ralph-workflow/src"],
            globs: RALPH_WORKFLOW_COMPILE_TIME_EXTRA_GLOBS,
            files: RALPH_WORKFLOW_COMPILE_TIME_EXTRA_FILES,
        },

        "dylint" => CheckScope::Patterns {
            globs: DYLINT_SCOPE_GLOBS,
            files: DYLINT_SCOPE_FILES,
            include_lock: false,
        },

        "clippy-xtask" | "test-xtask" => CheckScope::Build(&["xtask/src"]),

        "clippy-ralph-gui" | "test-ralph-gui-lib" => CheckScope::BuildWithExtras {
            dirs: RALPH_GUI_RUST_SCOPE_DIRS,
            globs: RALPH_GUI_BUILD_EXTRA_GLOBS,
            files: RALPH_GUI_BUILD_EXTRA_FILES,
        },

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

        "test-integration" => CheckScope::BuildWithExtras {
            dirs: &[
                "ralph-workflow/src",
                "tests/integration_tests",
                "test-helpers/src",
            ],
            globs: INTEGRATION_TEST_AND_RALPH_WORKFLOW_EXTRA_GLOBS,
            files: RALPH_WORKFLOW_COMPILE_TIME_EXTRA_FILES,
        },

        "release-build" => CheckScope::BuildWithExtras {
            dirs: &["ralph-workflow/src", "test-helpers/src", "xtask/src"],
            globs: RALPH_WORKFLOW_COMPILE_TIME_EXTRA_GLOBS,
            files: RALPH_WORKFLOW_COMPILE_TIME_EXTRA_FILES,
        },

        // conservative fallback for any unrecognised check
        _ => CheckScope::Build(&["ralph-workflow/src", "tests", "xtask/src"]),
    }
}

fn native_required_scope_for(check_name: &str) -> CheckScope {
    match check_name {
        "compliance-timeout-wrapper" => CheckScope::Patterns {
            globs: &[ScopeGlob {
                dir: "tests/integration_tests",
                pattern: "*.rs",
            }],
            files: &[],
            include_lock: false,
        },
        "audit-no-shell-scripts" => CheckScope::Patterns {
            globs: &[
                ScopeGlob {
                    dir: "scripts",
                    pattern: "*.sh",
                },
                ScopeGlob {
                    dir: "tests/integration_tests",
                    pattern: "*.sh",
                },
            ],
            files: &[],
            include_lock: false,
        },
        _ => CheckScope::Build(&["tests", "xtask/src"]),
    }
}

/// Compute a u64 hash of the scope by iterating relevant files and
/// hashing (path, content bytes) pairs. Content-based hashing ensures
/// cache keys are stable across mtime changes (e.g., git checkout round-trips).
#[cfg(test)]
pub fn compute_scope_hash(repo_root: &Path, scope: &CheckScope) -> std::io::Result<u64> {
    compute_scope_hash_with_snapshot(repo_root, scope, &RepositoryFingerprintCache::default())
}

pub fn compute_scope_hash_with_snapshot(
    repo_root: &Path,
    scope: &CheckScope,
    snapshot: &RepositoryFingerprintCache,
) -> std::io::Result<u64> {
    let mut hasher = Fnv1aHasher::new();
    hasher.write_bytes(SCOPE_HASH_VERSION);
    let mut all_paths: Vec<PathBuf> = Vec::new();

    match scope {
        CheckScope::Directories(dirs)
        | CheckScope::Build(dirs)
        | CheckScope::BuildWithExtras { dirs, .. } => {
            for dir in *dirs {
                all_paths.extend(snapshot.collect_globbed_paths(repo_root, dir, "*.rs")?);

                let full = repo_root.join(dir);
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
            if matches!(
                scope,
                CheckScope::Build(_) | CheckScope::BuildWithExtras { .. }
            ) {
                config_candidates.push("Cargo.lock");
            }
            for rel in config_candidates {
                let path = repo_root.join(rel);
                if path.exists() {
                    all_paths.push(path);
                }
            }

            if let CheckScope::BuildWithExtras { globs, files, .. } = scope {
                for glob in *globs {
                    all_paths.extend(snapshot.collect_globbed_paths(
                        repo_root,
                        glob.dir,
                        glob.pattern,
                    )?);
                }
                for rel in *files {
                    let path = repo_root.join(rel);
                    if path.exists() {
                        all_paths.push(path);
                    }
                }
            }
        }
        CheckScope::Patterns {
            globs,
            files,
            include_lock,
        } => {
            for glob in *globs {
                all_paths.extend(snapshot.collect_globbed_paths(
                    repo_root,
                    glob.dir,
                    glob.pattern,
                )?);
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

    all_paths.sort();
    all_paths.dedup();

    for path in &all_paths {
        let relative = path.strip_prefix(repo_root).unwrap_or(path);
        hasher.write_bytes(relative.to_string_lossy().as_bytes());
        hasher.write_bytes(&snapshot.read_file_digest(path)?.to_le_bytes());
    }

    Ok(hasher.finish())
}

fn append_native_scan_definition_hash(
    hasher: &mut Fnv1aHasher,
    checks: &[crate::scanner::NativeScanCheck],
) {
    for check in checks {
        hasher.write_bytes(check.name.as_bytes());
        for literal in check.literals {
            hasher.write_bytes(literal.as_bytes());
        }
        for dir in check.directories {
            hasher.write_bytes(dir.as_bytes());
        }
        hasher.write_bytes(check.include_glob.as_bytes());
        for exclude in check.exclude_globs {
            hasher.write_bytes(exclude.as_bytes());
        }
        match check.mode {
            crate::scanner::MatchMode::AnyLiteral { skip_comment_lines } => {
                hasher.write_bytes(b"any-literal");
                hasher.write_bytes(&[u8::from(skip_comment_lines)]);
            }
            crate::scanner::MatchMode::StemWithBoolSuffix => {
                hasher.write_bytes(b"stem-with-bool-suffix");
            }
            crate::scanner::MatchMode::AnyLiteralAtLineStart { skip_comment_lines } => {
                hasher.write_bytes(b"any-literal-at-line-start");
                hasher.write_bytes(&[u8::from(skip_comment_lines)]);
            }
            crate::scanner::MatchMode::NegativeLookahead {
                negative_context,
                word_boundary_at_end,
            } => {
                hasher.write_bytes(b"negative-lookahead");
                hasher.write_bytes(negative_context.as_bytes());
                hasher.write_bytes(&[u8::from(word_boundary_at_end)]);
            }
        }
    }
}

fn compute_native_scan_hash(
    repo_root: &Path,
    checks: &[crate::scanner::NativeScanCheck],
    snapshot: &RepositoryFingerprintCache,
) -> std::io::Result<u64> {
    let mut hasher = Fnv1aHasher::new();
    hasher.write_bytes(NATIVE_SCAN_HASH_VERSION);
    append_native_scan_definition_hash(&mut hasher, checks);

    let mut all_paths: Vec<PathBuf> = Vec::new();
    for check in checks {
        for dir in check.directories {
            all_paths.extend(snapshot.collect_globbed_paths(repo_root, dir, check.include_glob)?);
        }
    }

    all_paths.extend(snapshot.collect_globbed_paths(repo_root, "xtask/src", "*.rs")?);

    for rel in [
        "Cargo.toml",
        "Cargo.lock",
        "xtask/Cargo.toml",
        "rust-toolchain.toml",
        "rust-toolchain",
    ] {
        let path = repo_root.join(rel);
        if path.exists() {
            all_paths.push(path);
        }
    }

    all_paths.sort();
    all_paths.dedup();

    for path in &all_paths {
        let relative = path.strip_prefix(repo_root).unwrap_or(path);
        hasher.write_bytes(relative.to_string_lossy().as_bytes());
        hasher.write_bytes(&snapshot.read_file_digest(path)?.to_le_bytes());
    }

    Ok(hasher.finish())
}

fn compute_native_required_hash(
    repo_root: &Path,
    check: &crate::verify::NativeCheck,
    snapshot: &RepositoryFingerprintCache,
) -> std::io::Result<u64> {
    let mut hasher = Fnv1aHasher::new();
    hasher.write_bytes(NATIVE_REQUIRED_HASH_VERSION);
    hasher.write_bytes(check.name.as_bytes());

    let input_hash = compute_scope_hash_with_snapshot(
        repo_root,
        &native_required_scope_for(check.name),
        snapshot,
    )?;
    hasher.write_bytes(&input_hash.to_le_bytes());

    let implementation_hash =
        compute_scope_hash_with_snapshot(repo_root, &CheckScope::Build(&["xtask/src"]), snapshot)?;
    hasher.write_bytes(&implementation_hash.to_le_bytes());

    Ok(hasher.finish())
}

fn compute_xtask_implementation_hash(
    repo_root: &Path,
    snapshot: &RepositoryFingerprintCache,
) -> std::io::Result<u64> {
    compute_scope_hash_with_snapshot(repo_root, &CheckScope::Build(&["xtask/src"]), snapshot)
}

fn compute_command_definition_hash(
    repo_root: &Path,
    spec: &CommandSpec,
    snapshot: &RepositoryFingerprintCache,
) -> std::io::Result<u64> {
    let mut hasher = Fnv1aHasher::new();
    hasher.write_bytes(COMMAND_DEFINITION_HASH_VERSION);
    hasher.write_bytes(spec.name.as_bytes());
    hasher.write_bytes(spec.program.as_bytes());
    for arg in spec.args {
        hasher.write_bytes(arg.as_bytes());
    }
    for exit_code in spec.success_exit_codes {
        hasher.write_bytes(&exit_code.to_le_bytes());
    }
    for (key, value) in spec.extra_env {
        hasher.write_bytes(key.as_bytes());
        hasher.write_bytes(value.as_bytes());
    }
    hasher.write_bytes(&compute_xtask_implementation_hash(repo_root, snapshot)?.to_le_bytes());
    Ok(hasher.finish())
}

/// On-disk format for the cache file.
#[derive(Debug, Default, Serialize, Deserialize)]
struct CacheFile {
    #[serde(default)]
    entries: HashMap<String, CacheEntry>,
    #[serde(default)]
    file_fingerprints: HashMap<String, CachedFileFingerprint>,
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
        let entries = self.memory.lock().unwrap().clone();
        let file_fingerprints = self
            .repo_fingerprint
            .persisted_file_fingerprints(&self.repo_root);
        let file = CacheFile {
            entries,
            file_fingerprints,
        };

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

    fn native_scan_definition_hash(checks: &[crate::scanner::NativeScanCheck]) -> u64 {
        let mut hasher = Fnv1aHasher::new();
        append_native_scan_definition_hash(&mut hasher, checks);
        hasher.finish()
    }

    fn precompute_native_required_hashes(
        &self,
        checks: &[crate::verify::NativeCheck],
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
        check: &crate::verify::NativeCheck,
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
        checks: &[crate::scanner::NativeScanCheck],
        progress: &(dyn Fn(&str, &str) + Sync),
    ) -> std::io::Result<Vec<crate::scanner::NativeScanCheckResult>> {
        let definition_hash = Self::native_scan_definition_hash(checks);
        let hash = self
            .prepared_native_scan_hash
            .lock()
            .unwrap()
            .and_then(|(prepared_definition_hash, prepared_hash)| {
                (prepared_definition_hash == definition_hash).then_some(prepared_hash)
            })
            .map_or_else(
                || compute_native_scan_hash(repo_root, checks, &self.repo_fingerprint),
                Ok,
            )?;
        let key = format!("native-scan:{hash}");

        {
            let memory = self.memory.lock().unwrap();
            if memory.contains_key(&key) {
                progress("native-scan", "cache hit");
                return Ok(checks
                    .iter()
                    .map(|check| crate::scanner::NativeScanCheckResult {
                        check_name: check.name,
                        passed: true,
                        violations: Vec::new(),
                    })
                    .collect());
            }
        }

        let results = crate::scanner::run_native_scan_checks_reporting(repo_root, checks, progress);
        if results.iter().all(|result| result.passed) {
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
        Ok(results)
    }
}

impl CommandRunner for CachingCommandRunner {
    fn prepare_for_verify(
        &self,
        _repo_root: &Path,
        native_checks: &[crate::verify::NativeCheck],
        checks: &[CommandSpec],
        native_scan_checks: &[crate::scanner::NativeScanCheck],
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
        check: &crate::verify::NativeCheck,
    ) -> std::io::Result<crate::verify::NativeCheckResult> {
        let hash = self.native_required_hash(repo_root, check)?;
        let key = format!("native-required:{}:{hash}", check.name);

        {
            let memory = self.memory.lock().unwrap();
            if memory.contains_key(&key) {
                return Ok(crate::verify::NativeCheckResult {
                    status: crate::verify::CheckStatus::Pass,
                    message: String::new(),
                });
            }
        }

        let result = self.inner.run_native_check(repo_root, check)?;
        if result.status == crate::verify::CheckStatus::Pass {
            self.memory.lock().unwrap().insert(
                key,
                CacheEntry {
                    scope_hash: hash,
                    exit_code: 0,
                    stdout: result.message.clone(),
                    stderr: String::new(),
                },
            );
            self.dirty.store(true, Ordering::Relaxed);
        }
        Ok(result)
    }

    fn run(&self, spec: &CommandSpec) -> std::io::Result<CommandOutput> {
        let scope = scope_for(spec.name);
        let Some(hash) = self.compute_or_cached_scope_hash(&scope) else {
            // If scope hashing fails (unreadable files, directory walk errors, etc.),
            // bypass caching completely to avoid incorrect cache hits.
            return self.inner.run(spec);
        };
        let verifier_hash = self.command_definition_hash(spec)?;
        let key = format!("{}:{}:{verifier_hash}", spec.name, hash);

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

        if crate::verify::is_cacheable_success_output(spec.name, &output, spec.success_exit_codes) {
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

    fn run_native_scan(
        &self,
        repo_root: &Path,
        checks: &[crate::scanner::NativeScanCheck],
        progress: &(dyn Fn(&str, &str) + Sync),
    ) -> std::io::Result<Vec<crate::scanner::NativeScanCheckResult>> {
        CachingCommandRunner::run_native_scan(self, repo_root, checks, progress)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
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

    fn make_spec(name: &'static str) -> CommandSpec {
        CommandSpec {
            name,
            program: "rg",
            args: &[],
            success_exit_codes: &[1],
            extra_env: &[],
        }
    }

    fn make_zero_exit_spec(name: &'static str) -> CommandSpec {
        CommandSpec {
            name,
            program: "npm",
            args: &[],
            success_exit_codes: &[0],
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

    fn zero_exit_success_output() -> CommandOutput {
        CommandOutput {
            exit_code: 0,
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

    fn allowed_frontend_warning_output() -> CommandOutput {
        CommandOutput {
            exit_code: 0,
            stdout: String::new(),
            stderr:
                "Warning: An update to Configuration inside a test was not wrapped in act(...)\n"
                    .to_string(),
        }
    }

    static NATIVE_REQUIRED_CHECK_CALLS: AtomicUsize = AtomicUsize::new(0);

    fn counted_native_required_check(repo_root: &Path) -> crate::verify::NativeCheckResult {
        NATIVE_REQUIRED_CHECK_CALLS.fetch_add(1, Ordering::SeqCst);
        let sentinel = repo_root.join("tests/integration_tests/sentinel.rs");
        if sentinel.exists() {
            crate::verify::NativeCheckResult {
                status: crate::verify::CheckStatus::Pass,
                message: String::new(),
            }
        } else {
            crate::verify::NativeCheckResult {
                status: crate::verify::CheckStatus::Warning,
                message: "missing sentinel".to_string(),
            }
        }
    }

    fn native_required_check(name: &'static str) -> crate::verify::NativeCheck {
        crate::verify::NativeCheck {
            name,
            run: counted_native_required_check,
        }
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

    #[test]
    fn test_caching_runner_caches_allowed_frontend_test_warning_output() {
        let tmp = unique_test_dir("xtask-cache-test-frontend-warning-cache");
        let _ = std::fs::create_dir_all(&tmp);

        let spec = make_zero_exit_spec("ralph-gui-frontend-test");

        let (inner, count) = CountingRunner::new(allowed_frontend_warning_output());
        let runner = CachingCommandRunner::new(inner, tmp.clone());

        let first = runner.run(&spec).unwrap();
        let second = runner.run(&spec).unwrap();

        assert_eq!(
            first.exit_code, 0,
            "frontend test output must still be returned"
        );
        assert_eq!(
            second.exit_code, 0,
            "cache hit must preserve the original frontend test output"
        );
        assert_eq!(
            count.load(Ordering::SeqCst),
            1,
            "allowed frontend test warning output must be cached after the first run"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_caching_runner_does_not_cache_allowed_frontend_warning_for_other_checks() {
        let tmp = unique_test_dir("xtask-cache-test-non-frontend-warning");
        let _ = std::fs::create_dir_all(&tmp);

        let spec = make_zero_exit_spec("some-other-check");

        let (inner, count) = CountingRunner::new(allowed_frontend_warning_output());
        let runner = CachingCommandRunner::new(inner, tmp.clone());

        let _ = runner.run(&spec).unwrap();
        let _ = runner.run(&spec).unwrap();

        assert_eq!(
            count.load(Ordering::SeqCst),
            2,
            "the allowed warning exception must stay limited to the frontend test check"
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
                    globs.iter().any(|glob| {
                        glob.dir == "ralph-workflow/src/files/llm_output_extraction"
                            && glob.pattern == "*"
                    }),
                    "clippy-core must track embedded XSD files consumed by ralph-workflow"
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
    fn test_scope_for_clippy_ralph_gui_is_granular() {
        match scope_for("clippy-ralph-gui") {
            CheckScope::BuildWithExtras { dirs, globs, files } => {
                assert_eq!(dirs, RALPH_GUI_RUST_SCOPE_DIRS);
                assert!(
                    globs
                        .iter()
                        .any(|glob| glob.dir == "ralph-gui/capabilities" && glob.pattern == "*"),
                    "GUI verify scope must track Tauri capabilities watched by build.rs"
                );
                assert!(
                    globs
                        .iter()
                        .any(|glob| glob.dir == "ralph-gui/icons" && glob.pattern == "*"),
                    "GUI verify scope must track Tauri icons watched by build.rs"
                );
                assert!(
                    files.contains(&"ralph-gui/tauri.conf.json"),
                    "GUI verify scope must track tauri.conf.json watched by build.rs"
                );
            }
            CheckScope::Directories(_) | CheckScope::Build(_) | CheckScope::Patterns { .. } => {
                panic!("clippy-ralph-gui must use BuildWithExtras scope")
            }
        }
    }

    #[test]
    fn test_scope_for_test_ralph_gui_lib_is_granular() {
        match scope_for("test-ralph-gui-lib") {
            CheckScope::BuildWithExtras { dirs, globs, files } => {
                assert_eq!(dirs, RALPH_GUI_RUST_SCOPE_DIRS);
                assert!(
                    globs
                        .iter()
                        .any(|glob| glob.dir == "ralph-gui/capabilities" && glob.pattern == "*"),
                    "GUI lib test scope must track Tauri capabilities watched by build.rs"
                );
                assert!(
                    globs
                        .iter()
                        .any(|glob| glob.dir == "ralph-gui/icons" && glob.pattern == "*"),
                    "GUI lib test scope must track Tauri icons watched by build.rs"
                );
                assert!(
                    files.contains(&"ralph-gui/tauri.conf.json"),
                    "GUI lib test scope must track tauri.conf.json watched by build.rs"
                );
            }
            CheckScope::Directories(_) | CheckScope::Build(_) | CheckScope::Patterns { .. } => {
                panic!("test-ralph-gui-lib must use BuildWithExtras scope")
            }
        }
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
                    globs.iter().any(|glob| {
                        glob.dir == "ralph-workflow/src/files/llm_output_extraction"
                            && glob.pattern == "*"
                    }),
                    "integration test scope must track embedded XSD files used by integration tests and prompts"
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
            CheckScope::Directories(dirs) => {
                assert!(
                    dirs.contains(&"ralph-workflow/src"),
                    "fmt-check must scan ralph-workflow/src"
                );
                assert!(dirs.contains(&"tests"), "fmt-check must scan tests");
                assert!(dirs.contains(&"xtask/src"), "fmt-check must scan xtask/src");
                assert!(
                    dirs.contains(&"test-helpers/src"),
                    "fmt-check must scan test-helpers/src"
                );
                assert!(
                    dirs.contains(&"ralph-gui"),
                    "fmt-check must scan ralph-gui because cargo fmt --all --check formats GUI Rust too"
                );
            }
            CheckScope::Build(_)
            | CheckScope::BuildWithExtras { .. }
            | CheckScope::Patterns { .. } => {
                panic!("fmt-check must use Directories scope")
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
                        .any(|glob| glob.dir == "ralph-gui/src" && glob.pattern == "*.rs"),
                    "forbidden allow/expect scan must cover stable GUI Rust sources"
                );
                assert!(
                    files.contains(&"ralph-gui/build.rs"),
                    "forbidden allow/expect scan must cover ralph-gui/build.rs"
                );
                assert!(
                    !include_lock,
                    "forbidden allow/expect scan should not depend on Cargo.lock"
                );
            }
            CheckScope::Directories(_)
            | CheckScope::Build(_)
            | CheckScope::BuildWithExtras { .. } => {
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
                    globs.iter().any(|glob| {
                        glob.dir == "lints/file_too_long/src" && glob.pattern == "*.rs"
                    }),
                    "dylint must include the custom lint crate sources"
                );
                for required_file in [
                    "Makefile",
                    "lints/file_too_long/Cargo.toml",
                    "lints/file_too_long/Cargo.lock",
                    "lints/file_too_long/.cargo/config.toml",
                    "lints/file_too_long/rust-toolchain.toml",
                    "lints/file_too_long/dylint-link",
                    "lints/file_too_long/rustc-nightly",
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
            CheckScope::Directories(_)
            | CheckScope::Build(_)
            | CheckScope::BuildWithExtras { .. } => {
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
                assert!(
                    globs.iter().any(|glob| {
                        glob.dir == "ralph-workflow/src/files/llm_output_extraction"
                            && glob.pattern == "*"
                    }),
                    "ralph-workflow lib tests must track embedded XSD files"
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
                assert!(
                    globs.iter().any(|glob| {
                        glob.dir == "ralph-workflow/src/files/llm_output_extraction"
                            && glob.pattern == "*"
                    }),
                    "release-build must track embedded XSD files consumed by ralph-workflow"
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
        let _ =
            compute_scope_hash_with_snapshot(&tmp, &CheckScope::Directories(&["src"]), &snapshot)
                .expect("first hash should succeed");
        let file_count_after_first = snapshot.file_fingerprints.lock().unwrap().len();
        let _ =
            compute_scope_hash_with_snapshot(&tmp, &CheckScope::Directories(&["src"]), &snapshot)
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

        let check = crate::scanner::NativeScanCheck {
            name: "native-scan-cache-test",
            literals: &["definitely_missing_literal"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: crate::scanner::MatchMode::AnyLiteral {
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

        let check = crate::scanner::NativeScanCheck {
            name: "native-scan-cache-invalidation",
            literals: &["blocked_literal"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: crate::scanner::MatchMode::AnyLiteral {
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

        let check = crate::scanner::NativeScanCheck {
            name: "native-scan-cache-irrelevant-change",
            literals: &["blocked_literal"],
            directories: &["src"],
            include_glob: "*.rs",
            exclude_globs: &[],
            mode: crate::scanner::MatchMode::AnyLiteral {
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
    fn test_compute_scope_hash_test_integration_changes_when_embedded_xsd_changes() {
        let tmp = unique_test_dir("xtask-cache-test-integration-xsd-change");
        let _ = std::fs::remove_dir_all(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("ralph-workflow/src"));
        let _ = std::fs::create_dir_all(tmp.join("ralph-workflow/src/files/llm_output_extraction"));
        let _ = std::fs::create_dir_all(tmp.join("test-helpers/src"));
        let _ = std::fs::create_dir_all(tmp.join("tests/integration_tests"));

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
            tmp.join("tests/integration_tests/development_xml.rs"),
            b"const XSD: &str = include_str!(\"../../ralph-workflow/src/files/llm_output_extraction/development_result.xsd\");\n#[test]\nfn integration() { assert!(!XSD.is_empty()); }\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-workflow/src/files/llm_output_extraction/development_result.xsd"),
            b"<schema>one</schema>\n",
        )
        .unwrap();

        let scope = scope_for("test-integration");
        let hash_before = compute_scope_hash(&tmp, &scope).unwrap();

        std::fs::write(
            tmp.join("ralph-workflow/src/files/llm_output_extraction/development_result.xsd"),
            b"<schema>two</schema>\n",
        )
        .unwrap();

        let hash_after = compute_scope_hash(&tmp, &scope).unwrap();

        assert_ne!(
            hash_before, hash_after,
            "integration test scope must invalidate when embedded XSD dependencies change"
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

        std::fs::write(
            tmp.join("tests/integration_tests/release_scope.rs"),
            b"#[test]\nfn integration() { panic!(\"changed\"); }\n",
        )
        .unwrap();

        let hash_after = compute_scope_hash(&tmp, &scope).unwrap();

        assert_eq!(
            hash_before, hash_after,
            "release-build scope must ignore tests/ changes because cargo build --release only builds workspace default members"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_release_build_scope_changes_when_default_member_source_changes() {
        let tmp = unique_test_dir("xtask-cache-test-release-build-member-change");
        let _ = std::fs::remove_dir_all(&tmp);
        write_release_build_scope_fixture(&tmp);

        let scope = scope_for("release-build");
        let hash_before = compute_scope_hash(&tmp, &scope).unwrap();

        std::fs::write(
            tmp.join("xtask/src/main.rs"),
            b"fn main() { println!(\"changed\"); }\n",
        )
        .unwrap();

        let hash_after = compute_scope_hash(&tmp, &scope).unwrap();

        assert_ne!(
            hash_before, hash_after,
            "release-build scope must still invalidate when a default-member source file changes"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_release_build_scope_changes_when_embedded_xsd_changes() {
        let tmp = unique_test_dir("xtask-cache-test-release-build-xsd-change");
        let _ = std::fs::remove_dir_all(&tmp);
        write_release_build_scope_fixture(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("ralph-workflow/src/files/llm_output_extraction"));
        std::fs::write(
            tmp.join("ralph-workflow/src/files/llm_output_extraction/development_result.xsd"),
            b"<schema>one</schema>\n",
        )
        .unwrap();

        let scope = scope_for("release-build");
        let hash_before = compute_scope_hash(&tmp, &scope).unwrap();

        std::fs::write(
            tmp.join("ralph-workflow/src/files/llm_output_extraction/development_result.xsd"),
            b"<schema>two</schema>\n",
        )
        .unwrap();

        let hash_after = compute_scope_hash(&tmp, &scope).unwrap();

        assert_ne!(
            hash_before, hash_after,
            "release-build scope must invalidate when embedded XSD dependencies change"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_frontend_scope_hash_changes_when_ui_source_changes() {
        let tmp = unique_test_dir("xtask-cache-test-frontend-source-change");
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
        let tmp = unique_test_dir("xtask-cache-test-frontend-unrelated-rust");
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

    #[test]
    fn test_forbidden_allow_expect_scope_ignores_transient_frontend_files() {
        let tmp = unique_test_dir("xtask-cache-test-forbidden-allow-scope-excludes-transient");
        let _ = std::fs::remove_dir_all(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("ralph-workflow/src"));
        let _ = std::fs::create_dir_all(tmp.join("tests"));
        let _ = std::fs::create_dir_all(tmp.join("xtask/src"));
        let _ = std::fs::create_dir_all(tmp.join("test-helpers/src"));
        let _ = std::fs::create_dir_all(tmp.join("ralph-gui/src"));
        let _ = std::fs::create_dir_all(tmp.join("ralph-gui/ui/node_modules/pkg"));

        std::fs::write(tmp.join("Cargo.toml"), b"[workspace]\nmembers = []\n").unwrap();
        std::fs::write(
            tmp.join("ralph-workflow/src/lib.rs"),
            b"pub fn workflow() {}\n",
        )
        .unwrap();
        std::fs::write(tmp.join("tests/smoke.rs"), b"#[test]\nfn smoke() {}\n").unwrap();
        std::fs::write(tmp.join("xtask/src/main.rs"), b"fn main() {}\n").unwrap();
        std::fs::write(tmp.join("test-helpers/src/lib.rs"), b"pub fn helper() {}\n").unwrap();
        std::fs::write(tmp.join("ralph-gui/build.rs"), b"fn main() {}\n").unwrap();
        std::fs::write(tmp.join("ralph-gui/src/lib.rs"), b"pub fn gui() {}\n").unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/node_modules/pkg/index.rs"),
            b"pub fn transient() {}\n",
        )
        .unwrap();

        let scope = scope_for("forbidden-allow-expect-scan");
        let hash_before = compute_scope_hash(&tmp, &scope).unwrap();

        std::fs::write(
            tmp.join("ralph-gui/ui/node_modules/pkg/index.rs"),
            b"pub fn changed_transient() {}\n",
        )
        .unwrap();

        let hash_after = compute_scope_hash(&tmp, &scope).unwrap();

        assert_eq!(
            hash_before, hash_after,
            "forbidden-allow-expect scope must ignore transient frontend directories under ralph-gui"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_gui_scope_hash_changes_when_build_script_input_changes() {
        let tmp = unique_test_dir("xtask-cache-test-gui-build-inputs");
        let _ = std::fs::remove_dir_all(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("ralph-gui/src"));
        let _ = std::fs::create_dir_all(tmp.join("ralph-gui/capabilities"));
        let _ = std::fs::create_dir_all(tmp.join("ralph-gui/icons"));
        let _ = std::fs::create_dir_all(tmp.join("ralph-workflow/src"));

        std::fs::write(
            tmp.join("Cargo.toml"),
            b"[workspace]\nmembers = [\"ralph-gui\", \"ralph-workflow\"]\n",
        )
        .unwrap();
        std::fs::write(tmp.join("Cargo.lock"), b"# lock\n").unwrap();
        std::fs::write(
            tmp.join("ralph-gui/Cargo.toml"),
            b"[package]\nname = \"ralph-gui\"\nversion = \"0.1.0\"\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-workflow/Cargo.toml"),
            b"[package]\nname = \"ralph-workflow\"\nversion = \"0.1.0\"\n",
        )
        .unwrap();
        std::fs::write(tmp.join("ralph-gui/build.rs"), b"fn main() {}\n").unwrap();
        std::fs::write(tmp.join("ralph-gui/src/lib.rs"), b"pub fn gui() {}\n").unwrap();
        std::fs::write(
            tmp.join("ralph-workflow/src/lib.rs"),
            b"pub fn workflow() {}\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/tauri.conf.json"),
            b"{\"app\":\"one\"}\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/capabilities/default.json"),
            b"{\"capability\":\"one\"}\n",
        )
        .unwrap();
        std::fs::write(tmp.join("ralph-gui/icons/icon.png"), b"icon-one\n").unwrap();

        let scope = scope_for("clippy-ralph-gui");
        let hash_before = compute_scope_hash(&tmp, &scope).unwrap();

        std::fs::write(
            tmp.join("ralph-gui/capabilities/default.json"),
            b"{\"capability\":\"two\"}\n",
        )
        .unwrap();

        let hash_after_capabilities = compute_scope_hash(&tmp, &scope).unwrap();
        assert_ne!(
            hash_before, hash_after_capabilities,
            "GUI verify scope must invalidate when capabilities watched by build.rs change"
        );

        std::fs::write(
            tmp.join("ralph-gui/tauri.conf.json"),
            b"{\"app\":\"three\"}\n",
        )
        .unwrap();

        let hash_after_tauri = compute_scope_hash(&tmp, &scope).unwrap();
        assert_ne!(
            hash_after_capabilities, hash_after_tauri,
            "GUI verify scope must invalidate when tauri.conf.json watched by build.rs changes"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_cached_file_fingerprint_requires_available_modified_time_for_reuse() {
        let cached = CachedFileFingerprint {
            len: 11,
            modified: None,
            digest: 42,
            trust_metadata_match: false,
        };

        assert!(
            !cached.can_reuse_for_metadata(FileFingerprintMetadata {
                len: 11,
                modified: None,
            }),
            "persisted digests must not be trusted when metadata.modified() is unavailable"
        );
    }

    #[test]
    fn test_persisted_file_fingerprint_is_rehashed_even_when_metadata_matches() {
        let tmp = unique_test_dir("xtask-cache-test-persisted-rehash");
        let _ = std::fs::remove_dir_all(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("src"));

        let file_path = tmp.join("src/lib.rs");
        std::fs::write(&file_path, b"fn fresh() {}\n").unwrap();
        let metadata = RepositoryFingerprintCache::file_metadata(&file_path)
            .expect("file metadata should be readable");

        let cache = RepositoryFingerprintCache::from_persisted(
            &tmp,
            HashMap::from([(
                "src/lib.rs".to_string(),
                CachedFileFingerprint {
                    len: metadata.len,
                    modified: metadata.modified,
                    digest: 7,
                    trust_metadata_match: false,
                },
            )]),
        );

        let digest = cache
            .read_file_digest(&file_path)
            .expect("persisted fingerprint should fall back to rehashing file bytes");

        assert_ne!(
            digest, 7,
            "persisted digest metadata alone must not be trusted"
        );
        assert_eq!(
            cache
                .file_fingerprints
                .lock()
                .unwrap()
                .get(&file_path)
                .copied()
                .expect("fresh fingerprint should be stored after rehashing")
                .digest,
            digest,
            "rehashing should replace the stale persisted digest with fresh file content"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[cfg(unix)]
    #[test]
    fn test_caching_runner_bypasses_cache_when_scope_hash_cannot_be_computed() {
        use std::os::unix::fs::PermissionsExt;

        // If a file is unreadable, scope hashing must not collapse it to empty content,
        // otherwise we can incorrectly reuse cached successes.
        let tmp = unique_test_dir("xtask-cache-test-unreadable");
        let _ = std::fs::remove_dir_all(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("ralph-workflow/src"));
        let _ = std::fs::create_dir_all(tmp.join("target"));

        std::fs::write(tmp.join("Cargo.lock"), b"# lock").unwrap();

        let file_path = tmp.join("ralph-workflow/src/lib.rs");
        std::fs::write(&file_path, b"fn foo() {}\n").unwrap();

        let mut perms = std::fs::metadata(&file_path).unwrap().permissions();
        perms.set_mode(0o000);
        std::fs::set_permissions(&file_path, perms).unwrap();

        let spec = make_spec("clippy-core");

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
        let tmp = unique_test_dir("xtask-cache-test-reuse-scope");
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

    #[test]
    fn test_prepare_for_verify_precomputes_unique_scope_hashes() {
        let tmp = unique_test_dir("xtask-cache-test-prepare-shared-scopes");
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

        let (inner, _count) = CountingRunner::new(success_output());
        let runner = CachingCommandRunner::new(inner, tmp.clone());
        let checks = [make_spec("clippy-xtask"), make_spec("test-xtask")];

        runner
            .prepare_for_verify(&tmp, &[], &checks, &[])
            .expect("prepare_for_verify should succeed");

        let memo = runner.scope_memo.lock().unwrap();
        assert_eq!(
            memo.len(),
            1,
            "prepare_for_verify should precompute each unique scope once before dispatch"
        );
        assert!(
            memo.contains_key(&scope_memo_key(&scope_for("clippy-xtask"))),
            "prepare_for_verify should populate the shared xtask scope hash"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_prepare_for_verify_precomputes_native_required_check_hashes() {
        let tmp = unique_test_dir("xtask-cache-test-prepare-native-required");
        let _ = std::fs::remove_dir_all(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("tests/integration_tests"));
        std::fs::write(
            tmp.join("tests/integration_tests/sentinel.rs"),
            b"#[test]\nfn sentinel() {}\n",
        )
        .unwrap();

        let (inner, _count) = CountingRunner::new(success_output());
        let runner = CachingCommandRunner::new(inner, tmp.clone());

        runner
            .prepare_for_verify(
                &tmp,
                &[native_required_check("compliance-timeout-wrapper")],
                &[],
                &[],
            )
            .expect("prepare_for_verify should succeed");

        assert!(
            runner
                .prepared_native_check_hashes
                .lock()
                .unwrap()
                .contains_key("compliance-timeout-wrapper"),
            "prepare_for_verify should precompute native required hashes before dispatch"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_native_required_check_clean_result_is_cached_for_unchanged_run() {
        NATIVE_REQUIRED_CHECK_CALLS.store(0, Ordering::SeqCst);

        let tmp = unique_test_dir("xtask-cache-native-required-hit");
        let _ = std::fs::remove_dir_all(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("tests/integration_tests"));
        std::fs::write(
            tmp.join("tests/integration_tests/sentinel.rs"),
            b"#[test]\nfn sentinel() {}\n",
        )
        .unwrap();

        let (inner, _count) = CountingRunner::new(success_output());
        let runner = CachingCommandRunner::new(inner, tmp.clone());
        let check = native_required_check("compliance-timeout-wrapper");

        let first = runner.run_native_check(&tmp, &check).unwrap();
        let second = runner.run_native_check(&tmp, &check).unwrap();

        assert_eq!(first.status, crate::verify::CheckStatus::Pass);
        assert_eq!(second.status, crate::verify::CheckStatus::Pass);
        assert_eq!(
            NATIVE_REQUIRED_CHECK_CALLS.load(Ordering::SeqCst),
            1,
            "unchanged native required checks should hit the in-process cache on the second run"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    // ── FNV-1a hasher tests ───────────────────────────────────────────────────

    #[test]
    fn test_fnv_hasher_is_deterministic_for_same_content() {
        // FNV-1a must produce the same hash for identical file content
        // regardless of when it is computed (no DefaultHasher randomisation).
        let tmp = unique_test_dir("xtask-fnv-deterministic");
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
        let tmp = unique_test_dir("xtask-fnv-differs");
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
        let tmp = unique_test_dir("xtask-fnv-empty-scope");
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
        let tmp = unique_test_dir("xtask-cache-deferred-persist");
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
        let tmp = unique_test_dir("xtask-cache-flush-writes");
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

    #[cfg(unix)]
    #[test]
    fn test_cross_process_unreadable_file_bypasses_persisted_digest_cache() {
        use std::os::unix::fs::PermissionsExt;

        let tmp = unique_test_dir("xtask-cache-cross-process-persisted-fingerprint");
        let _ = std::fs::remove_dir_all(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("xtask/src"));
        let _ = std::fs::create_dir_all(tmp.join("target"));
        std::fs::write(
            tmp.join("Cargo.toml"),
            b"[workspace]\nmembers = [\"xtask\"]\n",
        )
        .unwrap();
        std::fs::write(tmp.join("Cargo.lock"), b"# lock\n").unwrap();
        std::fs::write(
            tmp.join("xtask/Cargo.toml"),
            b"[package]\nname = \"xtask\"\nversion = \"0.1.0\"\n",
        )
        .unwrap();
        let source_path = tmp.join("xtask/src/lib.rs");
        std::fs::write(&source_path, b"pub fn xtask() {}\n").unwrap();

        let spec = make_spec("clippy-xtask");

        let (inner_first, first_count) = CountingRunner::new(success_output());
        let runner_first = CachingCommandRunner::new(inner_first, tmp.clone());
        let _ = runner_first.run(&spec).unwrap();
        runner_first.flush();
        assert_eq!(first_count.load(Ordering::SeqCst), 1);

        let mut unreadable = std::fs::metadata(&source_path).unwrap().permissions();
        unreadable.set_mode(0o000);
        std::fs::set_permissions(&source_path, unreadable).unwrap();

        let (inner_second, second_count) = CountingRunner::new(success_output());
        let runner_second = CachingCommandRunner::new(inner_second, tmp.clone());
        let result = runner_second.run(&spec);

        let mut readable = std::fs::metadata(&source_path).unwrap().permissions();
        readable.set_mode(0o644);
        let _ = std::fs::set_permissions(&source_path, readable);

        let output = result.expect("unreadable warm cross-process run should still succeed");
        assert_eq!(output.exit_code, 1);
        assert_eq!(
            second_count.load(Ordering::SeqCst),
            1,
            "persisted digests must not be trusted for unreadable files in a new process; the command should rerun"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_flush_is_idempotent() {
        // Calling flush() multiple times must not cause errors or duplicated writes.
        let tmp = unique_test_dir("xtask-cache-flush-idempotent");
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

        let tmp = unique_test_dir("xtask-cache-flush-retry-after-fail");
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

    #[cfg(unix)]
    #[test]
    fn test_cross_process_relevant_edit_invalidates_only_affected_scope() {
        let tmp = unique_test_dir("xtask-cache-cross-process-scope-invalidation");
        let _ = std::fs::remove_dir_all(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("xtask/src"));
        let _ = std::fs::create_dir_all(tmp.join("ralph-gui/ui/src"));
        let _ = std::fs::create_dir_all(tmp.join("target"));

        std::fs::write(
            tmp.join("Cargo.toml"),
            b"[workspace]\nmembers = [\"xtask\"]\n",
        )
        .unwrap();
        std::fs::write(tmp.join("Cargo.lock"), b"# lock\n").unwrap();
        std::fs::write(
            tmp.join("xtask/Cargo.toml"),
            b"[package]\nname = \"xtask\"\nversion = \"0.1.0\"\n",
        )
        .unwrap();
        let xtask_source = tmp.join("xtask/src/lib.rs");
        std::fs::write(&xtask_source, b"pub fn xtask() {}\n").unwrap();

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
            tmp.join("ralph-gui/ui/tsconfig.node.json"),
            b"{\"compilerOptions\":{}}\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/vite.config.ts"),
            b"export default {};\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/eslint.config.mjs"),
            b"export default [];\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/index.html"),
            b"<div id=\"app\"></div>\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/src/App.tsx"),
            b"export function App() { return <div>one</div>; }\n",
        )
        .unwrap();

        let xtask_spec = make_spec("clippy-xtask");
        let frontend_spec = make_zero_exit_spec("ralph-gui-frontend-test");

        let (inner_first_xtask, first_xtask_count) = CountingRunner::new(success_output());
        let runner_first_xtask = CachingCommandRunner::new(inner_first_xtask, tmp.clone());
        let _ = runner_first_xtask.run(&xtask_spec).unwrap();
        runner_first_xtask.flush();

        let (inner_first_frontend, first_frontend_count) =
            CountingRunner::new(zero_exit_success_output());
        let runner_first_frontend = CachingCommandRunner::new(inner_first_frontend, tmp.clone());
        let _ = runner_first_frontend.run(&frontend_spec).unwrap();
        runner_first_frontend.flush();

        assert_eq!(
            first_xtask_count.load(Ordering::SeqCst),
            1,
            "cold xtask run should execute before the cache is populated"
        );
        assert_eq!(
            first_frontend_count.load(Ordering::SeqCst),
            1,
            "cold frontend run should execute before the cache is populated"
        );

        std::fs::write(
            tmp.join("ralph-gui/ui/src/App.tsx"),
            b"export function App() { return <div>two</div>; }\n",
        )
        .unwrap();

        let (inner_xtask, xtask_count) = CountingRunner::new(success_output());
        let runner_xtask = CachingCommandRunner::new(inner_xtask, tmp.clone());
        let _ = runner_xtask
            .run(&xtask_spec)
            .expect("xtask warm cache hit should succeed");
        assert_eq!(
            xtask_count.load(Ordering::SeqCst),
            0,
            "frontend-only edits should not invalidate unchanged xtask scope fingerprints when xtask inputs can be rehashed"
        );

        let (inner_frontend, frontend_count) = CountingRunner::new(zero_exit_success_output());
        let runner_frontend = CachingCommandRunner::new(inner_frontend, tmp.clone());
        let _ = runner_frontend.run(&frontend_spec).unwrap();
        assert_eq!(
            frontend_count.load(Ordering::SeqCst),
            1,
            "frontend edits must still invalidate the affected frontend scope"
        );

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_frontend_cache_invalidates_when_xtask_verifier_definition_changes() {
        let tmp = unique_test_dir("xtask-cache-test-verifier-definition");
        let _ = std::fs::remove_dir_all(&tmp);
        let _ = std::fs::create_dir_all(tmp.join("ralph-gui/ui/src"));
        let _ = std::fs::create_dir_all(tmp.join("xtask/src"));
        let _ = std::fs::create_dir_all(tmp.join("target"));

        std::fs::write(
            tmp.join("Cargo.toml"),
            b"[workspace]\nmembers = [\"xtask\"]\n",
        )
        .unwrap();
        std::fs::write(tmp.join("Cargo.lock"), b"# lock\n").unwrap();
        std::fs::write(
            tmp.join("xtask/Cargo.toml"),
            b"[package]\nname = \"xtask\"\nversion = \"0.1.0\"\n",
        )
        .unwrap();
        std::fs::write(tmp.join("xtask/src/main.rs"), b"fn main() {}\n").unwrap();
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
            tmp.join("ralph-gui/ui/tsconfig.node.json"),
            b"{\"compilerOptions\":{}}\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/vite.config.ts"),
            b"export default {};\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/eslint.config.mjs"),
            b"export default [];\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/index.html"),
            b"<div id=\"app\"></div>\n",
        )
        .unwrap();
        std::fs::write(
            tmp.join("ralph-gui/ui/src/App.tsx"),
            b"export function App() { return <div>one</div>; }\n",
        )
        .unwrap();

        let spec = make_zero_exit_spec("ralph-gui-frontend-test");

        let (inner_first, first_count) = CountingRunner::new(zero_exit_success_output());
        let runner_first = CachingCommandRunner::new(inner_first, tmp.clone());
        let _ = runner_first.run(&spec).unwrap();
        runner_first.flush();
        assert_eq!(first_count.load(Ordering::SeqCst), 1);

        std::fs::write(
            tmp.join("xtask/src/main.rs"),
            b"fn main() { println!(\"verifier changed\"); }\n",
        )
        .unwrap();

        let (inner_second, second_count) = CountingRunner::new(zero_exit_success_output());
        let runner_second = CachingCommandRunner::new(inner_second, tmp.clone());
        let _ = runner_second.run(&spec).unwrap();

        assert_eq!(
            second_count.load(Ordering::SeqCst),
            1,
            "changes to xtask verifier code must invalidate cached command successes even when the command scope is unchanged"
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
}
