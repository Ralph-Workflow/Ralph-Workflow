/// Default threshold for storing file content in snapshots (10KB).
///
/// Files smaller than this threshold will have their full content stored
/// in the checkpoint for automatic recovery on resume.
const DEFAULT_CONTENT_THRESHOLD: u64 = 10 * 1024;

/// Maximum file size that will be compressed in snapshots (100KB).
///
/// Files between `DEFAULT_CONTENT_THRESHOLD` and this size that are key files
/// (PROMPT.md, PLAN.md, ISSUES.md) will be compressed before storing.
const MAX_COMPRESS_SIZE: u64 = 100 * 1024;

/// Snapshot of a file's state at a point in time.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct FileSnapshot {
    /// Path to the file
    pub path: String,
    /// SHA-256 checksum of file contents
    pub checksum: String,
    /// File size in bytes
    pub size: u64,
    /// For small files (< 10KB by default), store full content
    pub content: Option<String>,
    /// Compressed content (base64-encoded gzip) for larger key files
    pub compressed_content: Option<String>,
    /// Whether the file existed
    pub exists: bool,
}

impl FileSnapshot {
    /// Create a new file snapshot with the default content threshold (10KB).
    ///
    /// This version does not capture file content (content and `compressed_content` will be None).
    /// Use `from_workspace` to create a snapshot with content from a workspace.
    #[must_use]
    pub fn new(path: &str, checksum: String, size: u64, exists: bool) -> Self {
        Self {
            path: path.to_string(),
            checksum,
            size,
            content: None,
            compressed_content: None,
            exists,
        }
    }

    /// Create a file snapshot from a workspace using the default content threshold (10KB).
    ///
    /// Files smaller than 10KB will have their content stored.
    /// Key files (PROMPT.md, PLAN.md, ISSUES.md, NOTES.md) may be compressed if they
    /// are between 10KB and 100KB.
    pub fn from_workspace_default(
        workspace: &dyn Workspace,
        path: &str,
        checksum: String,
        size: u64,
        exists: bool,
    ) -> Self {
        Self::from_workspace(
            workspace,
            path,
            checksum,
            size,
            exists,
            DEFAULT_CONTENT_THRESHOLD,
        )
    }

    /// Create a file snapshot from a workspace, optionally capturing content.
    ///
    /// Files smaller than `max_size` bytes will have their content stored.
    /// Key files (PROMPT.md, PLAN.md, ISSUES.md, NOTES.md) may be compressed if they
    /// are between `max_size` and `MAX_COMPRESS_SIZE`.
    pub fn from_workspace(
        workspace: &dyn Workspace,
        path: &str,
        checksum: String,
        size: u64,
        exists: bool,
        max_size: u64,
    ) -> Self {
        let content = if exists && size < max_size {
            workspace.read(Path::new(path)).ok()
        } else {
            None
        };

        let compressed_content = if exists
            && (path.contains("PROMPT.md")
                || path.contains("PLAN.md")
                || path.contains("ISSUES.md")
                || path.contains("NOTES.md"))
            && size >= max_size
            && size < MAX_COMPRESS_SIZE
        {
            workspace.read_bytes(Path::new(path)).ok().and_then(|data| {
                crate::checkpoint::execution_history::compression::compress(&data).ok()
            })
        } else {
            None
        };

        Self {
            path: path.to_string(),
            checksum,
            size,
            content,
            compressed_content,
            exists,
        }
    }

    /// Get the file content, decompressing if necessary.
    #[must_use]
    pub fn get_content(&self) -> Option<String> {
        self.content.clone().or_else(|| {
            self.compressed_content.as_ref().and_then(|compressed| {
                crate::checkpoint::execution_history::compression::decompress(compressed).ok()
            })
        })
    }

    /// Create a snapshot for a non-existent file.
    #[must_use]
    pub fn not_found(path: &str) -> Self {
        Self {
            path: path.to_string(),
            checksum: String::new(),
            size: 0,
            content: None,
            compressed_content: None,
            exists: false,
        }
    }

    /// Verify that the current file state matches this snapshot using a workspace.
    pub fn verify_with_workspace(&self, workspace: &dyn Workspace) -> bool {
        let path = Path::new(&self.path);

        if !self.exists {
            return !workspace.exists(path);
        }

        let Ok(content) = workspace.read_bytes(path) else {
            return false;
        };

        if content.len() as u64 != self.size {
            return false;
        }

        let checksum = crate::checkpoint::state::calculate_checksum_from_bytes(&content);
        checksum == self.checksum
    }
}
