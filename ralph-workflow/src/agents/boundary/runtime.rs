use std::io;
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::agents::ccs_env::{CcsDirEntry, CcsEnvironment, CcsFilesystem};

pub struct RealCcsEnvironment;

impl CcsEnvironment for RealCcsEnvironment {
    fn get_var(&self, name: &str) -> Option<String> {
        std::env::var(name).ok()
    }

    fn home_dir(&self) -> Option<PathBuf> {
        dirs::home_dir()
    }
}

pub struct RealCcsFilesystem;

impl CcsFilesystem for RealCcsFilesystem {
    fn exists(&self, path: &Path) -> bool {
        path.exists()
    }

    fn read_to_string(&self, path: &Path) -> io::Result<String> {
        std::fs::read_to_string(path)
    }

    fn read_dir(&self, path: &Path) -> io::Result<Vec<CcsDirEntry>> {
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

pub fn fetch_api_catalog_json(url: &str) -> Result<String, String> {
    let agent = ureq::Agent::new_with_config(
        ureq::config::Config::builder()
            .timeout_global(Some(Duration::from_secs(10)))
            .build(),
    );

    agent
        .get(url)
        .call()
        .map_err(|e: ureq::Error| e.to_string())?
        .body_mut()
        .read_to_string()
        .map_err(|e: ureq::Error| e.to_string())
}

pub fn get_env_var(name: &str) -> Option<String> {
    std::env::var(name).ok()
}
