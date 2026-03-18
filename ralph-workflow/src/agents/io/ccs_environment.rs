// Production implementation of CcsEnvironment trait using real environment.

use crate::agents::ccs_env::CcsEnvironment;
use std::path::PathBuf;

/// Production implementation that reads from actual environment.
pub struct RealCcsEnvironment;

impl CcsEnvironment for RealCcsEnvironment {
    fn get_var(&self, name: &str) -> Option<String> {
        std::env::var(name).ok()
    }

    fn home_dir(&self) -> Option<PathBuf> {
        dirs::home_dir()
    }
}
