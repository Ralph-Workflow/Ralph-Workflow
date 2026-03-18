// Production implementation of CcsFilesystem trait using real filesystem.

use crate::agents::ccs_env::{CcsDirEntry, CcsFilesystem};
use std::path::Path;

/// Production implementation that uses real filesystem.
pub struct RealCcsFilesystem;

impl CcsFilesystem for RealCcsFilesystem {
    fn exists(&self, path: &Path) -> bool {
        path.exists()
    }

    fn read_to_string(&self, path: &Path) -> std::io::Result<String> {
        std::fs::read_to_string(path)
    }

    fn read_dir(&self, path: &Path) -> std::io::Result<Vec<CcsDirEntry>> {
        let entries = std::fs::read_dir(path)?;
        entries
            .map(|entry| {
                let entry = entry?;
                let ft = entry.file_type()?;
                Ok(CcsDirEntry {
                    path: entry.path(),
                    file_name: entry.file_name().to_string_lossy().into_owned(),
                    is_file: ft.is_file(),
                })
            })
            .collect()
    }
}
