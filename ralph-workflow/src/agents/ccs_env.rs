//! CCS (Claude Code Switch) environment variable loading.
//!
//! This module provides support for loading environment variables from CCS
//! settings files. CCS stores profile -> settings file mappings in
//! `~/.ccs/config.json` and/or `~/.ccs/config.yaml`, and stores environment
//! variables inside the settings file under the `env` key.
//!
//! Source (CCS): `dist/utils/config-manager.js` and `dist/types/config.d.ts`.

use std::path::{Path, PathBuf};

include!("ccs_env/traits.rs");
include!("ccs_env/yaml_parser.rs");
include!("ccs_env/loader.rs");

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

    fn read_to_string(&self, path: &Path) -> Result<String, std::io::Error> {
        std::fs::read_to_string(path)
    }

    fn read_dir(&self, path: &Path) -> Result<Vec<CcsDirEntry>, std::io::Error> {
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

#[cfg(test)]
#[path = "ccs_env/io_tests.rs"]
mod io_tests;
