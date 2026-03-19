//! CCS (Claude Code Switch) environment variable loading.
//!
//! This module provides support for loading environment variables from CCS
//! settings files. CCS stores profile -> settings file mappings in
//! `~/.ccs/config.json` and/or `~/.ccs/config.yaml`, and stores environment
//! variables inside the settings file under the `env` key.
//!
//! Source (CCS): `dist/utils/config-manager.js` and `dist/types/config.d.ts`.

include!("ccs_env/traits.rs");
include!("ccs_env/yaml_parser.rs");
include!("ccs_env/loader.rs");

#[cfg(test)]
#[path = "ccs_env/io_tests.rs"]
mod io_tests;
