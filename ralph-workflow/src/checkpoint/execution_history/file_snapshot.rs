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
        let mut content = None;
        let mut compressed_content = None;

        if exists {
            let is_key_file = path.contains("PROMPT.md")
                || path.contains("PLAN.md")
                || path.contains("ISSUES.md")
                || path.contains("NOTES.md");

            let path_ref = Path::new(path);

            if size < max_size {
                // For small files, read and store content directly
                content = workspace.read(path_ref).ok();
            } else if is_key_file && size < MAX_COMPRESS_SIZE {
                // For larger key files, compress the content
                if let Ok(data) = workspace.read_bytes(path_ref) {
                    compressed_content = compress_data(&data).ok();
                }
            }
        }

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
            self.compressed_content
                .as_ref()
                .and_then(|compressed| decompress_data(compressed).ok())
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

/// Compress data using gzip and encode as base64.
///
/// This is used to store larger file content in checkpoints without
/// bloating the checkpoint file size too much.
fn compress_data(data: &[u8]) -> Result<String, std::io::Error> {
    use base64::{engine::general_purpose::STANDARD, Engine};
    use flate2::write::GzEncoder;
    use flate2::Compression;
    use std::io::Write;

    let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
    encoder.write_all(data)?;
    let compressed = encoder.finish()?;

    Ok(STANDARD.encode(&compressed))
}

const MAX_DECOMPRESSED_SNAPSHOT_BYTES: usize = 1024 * 1024;

/// Decompress data that was compressed with `compress_data`.
fn decompress_data(encoded: &str) -> Result<String, std::io::Error> {
    use base64::{engine::general_purpose::STANDARD, Engine};
    use flate2::read::GzDecoder;
    use std::io::Read;

    let compressed = STANDARD.decode(encoded).map_err(|e| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("Base64 decode error: {e}"),
        )
    })?;

    let mut decoder = GzDecoder::new(compressed.as_slice());
    let mut decompressed = Vec::new();
    let mut buf = [0u8; 8 * 1024];

    loop {
        let n = decoder.read(&mut buf)?;
        if n == 0 {
            break;
        }

        if decompressed.len().saturating_add(n) > MAX_DECOMPRESSED_SNAPSHOT_BYTES {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!(
                    "Decompressed payload exceeds max size ({MAX_DECOMPRESSED_SNAPSHOT_BYTES} bytes)"
                ),
            ));
        }

        decompressed.extend_from_slice(&buf[..n]);
    }

    String::from_utf8(decompressed).map_err(|e| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!("UTF-8 decode error: {e}"),
        )
    })
}
