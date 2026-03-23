// agents/ccs_env/io.rs — boundary module for CCS environment variable loading.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

pub struct RealCcsEnvironment;

impl CcsEnvironment for RealCcsEnvironment {
    fn get_var(&self, name: &str) -> Option<String> {
        std::env::var(name).ok()
    }

    fn home_dir(&self) -> Option<std::path::PathBuf> {
        dirs::home_dir()
    }
}

pub struct RealCcsFilesystem;

impl CcsFilesystem for RealCcsFilesystem {
    fn exists(&self, path: &std::path::Path) -> bool {
        path.exists()
    }

    fn read_to_string(&self, path: &std::path::Path) -> Result<String, std::io::Error> {
        std::fs::read_to_string(path)
    }

    fn read_dir(&self, path: &std::path::Path) -> Result<Vec<CcsDirEntry>, std::io::Error> {
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
