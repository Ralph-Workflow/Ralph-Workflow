//! Shared boundary detection logic for all lints.
//!
//! Boundary modules are directories where effectful code (mutation, I/O) is
//! permitted. This mirrors the Haskell separation between pure computation
//! and the `IO` monad.

use rustc_lint::LintContext;
use rustc_span::FileName;
use std::path::{Component, Path};

/// Boundary module path components where effects are permitted.
///
/// Code in a directory whose path contains one of these components is exempt
/// from functional purity restrictions:
///
/// - `io/` — filesystem and external data transport
/// - `runtime/` — OS-facing capabilities (process, env, time)
/// - `ffi/` — foreign function interface bindings
/// - `boundary/` — thin composition seams between pure and effectful code
pub const BOUNDARY_MODULES: &[&str] = &["io", "runtime", "ffi", "boundary"];

/// Check whether a path contains a boundary module component.
///
/// Returns `true` if any path component (directory or file stem) exactly
/// matches one of the boundary markers. Substring matches are rejected:
/// `iostream/` does NOT match `io`.
pub fn path_contains_boundary_component(path: &Path) -> bool {
    path.components().any(|component| {
        matches!(component, Component::Normal(name) if {
            let name_str = name.to_str().unwrap_or("");
            let stem = name_str.strip_suffix(".rs").unwrap_or(name_str);
            BOUNDARY_MODULES.iter().any(|b| *b == stem)
        })
    })
}

/// Check whether a span is located in a boundary module.
///
/// This is the primary entry point for lints to check whether code at a
/// given span should be exempt from purity restrictions.
pub fn is_in_boundary_module<C: HasSourceMap>(cx: &C, span: rustc_span::Span) -> bool {
    let source_map = cx.source_map();
    let filename = source_map.span_to_filename(span);

    match &filename {
        FileName::Real(real_name) => real_name
            .local_path()
            .map_or(false, path_contains_boundary_component),
        _ => false,
    }
}

/// Trait for contexts that provide access to the source map.
///
/// This allows the boundary check to work with both `EarlyContext` and
/// `LateContext` without code duplication.
pub trait HasSourceMap {
    fn source_map(&self) -> &rustc_span::source_map::SourceMap;
}

impl HasSourceMap for rustc_lint::EarlyContext<'_> {
    fn source_map(&self) -> &rustc_span::source_map::SourceMap {
        self.sess().source_map()
    }
}

impl<'tcx> HasSourceMap for rustc_lint::LateContext<'tcx> {
    fn source_map(&self) -> &rustc_span::source_map::SourceMap {
        self.sess().source_map()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── All four boundary modules must be detected ──

    #[test]
    fn boundary_io_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/io/writer.rs"
        )));
    }

    #[test]
    fn boundary_runtime_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/runtime/executor.rs"
        )));
    }

    #[test]
    fn boundary_ffi_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/ffi/bindings.rs"
        )));
    }

    #[test]
    fn boundary_dir_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/boundary/adapter.rs"
        )));
    }

    // ── Non-boundary modules must NOT be detected ──

    #[test]
    fn non_boundary_module_is_not_detected() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/pipeline/state.rs"
        )));
    }

    #[test]
    fn non_boundary_domain_module_is_not_detected() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/reducer/logic.rs"
        )));
    }

    // ── File-level boundary module (.rs file matching a marker name) ──

    #[test]
    fn file_level_io_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new("src/io.rs")));
    }

    #[test]
    fn file_level_runtime_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/runtime.rs"
        )));
    }

    // ── Substring boundary markers must NOT match ──

    #[test]
    fn iostream_is_not_a_boundary_module() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/iostream/reader.rs"
        )));
    }

    #[test]
    fn runtimeconfig_is_not_a_boundary_module() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/runtimeconfig/settings.rs"
        )));
    }
}
