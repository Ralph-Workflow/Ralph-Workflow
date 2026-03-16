#![feature(rustc_private)]
// DO NOT CHANGE LINTING POLICY UNLESS THE USER SPECIFICALLY ASKS TO, YOU MUST REFACTOR EVEN IF IT TAKES YOU LONG TIME
#![deny(warnings)]
#![deny(clippy::all)]

extern crate rustc_ast;
extern crate rustc_span;

use rustc_ast::ast::{Item, ItemKind, Ty, TyKind};
use rustc_lint::{EarlyContext, EarlyLintPass, LintContext};
use rustc_span::FileName;
use std::path::{Component, Path};

/// Interior-mutability types that are forbidden in application code.
///
/// Each entry is a list of path segments to match against type paths.
/// For example, `["Cell"]` matches `Cell<T>`, `std::cell::Cell<T>`, etc.
const FORBIDDEN_TYPES: &[(&[&str], &str)] = &[
    (&["Cell"], "std::cell::Cell"),
    (&["RefCell"], "std::cell::RefCell"),
    (&["UnsafeCell"], "std::cell::UnsafeCell"),
    (&["Mutex"], "std::sync::Mutex"),
    (&["RwLock"], "std::sync::RwLock"),
    (&["OnceLock"], "std::sync::OnceLock"),
    (&["LazyLock"], "std::sync::LazyLock"),
    (&["OnceCell"], "once_cell::sync::OnceCell"),
];

/// Boundary module path components where interior mutability is permitted.
const BOUNDARY_MODULES: &[&str] = &["io", "runtime", "ffi", "boundary"];

dylint_linting::impl_early_lint! {
    /// ### What it does
    ///
    /// Rejects usage of interior-mutability types (`Cell`, `RefCell`,
    /// `UnsafeCell`, `Mutex`, `RwLock`, `OnceLock`, `LazyLock`, `OnceCell`)
    /// in type annotations outside boundary modules.
    ///
    /// ### Why is this bad?
    ///
    /// Interior mutability lets code mutate values behind shared references,
    /// breaking the expectation that `&T` is truly immutable. This makes
    /// reasoning about data flow much harder and hides mutation from callers.
    ///
    /// ### Boundary exceptions
    ///
    /// Some boundary code (I/O adapters, caches, runtime internals) genuinely
    /// needs interior mutability. Place such code in a module whose path
    /// contains one of the boundary markers.
    ///
    /// ### Example (bad)
    ///
    /// ```rust,ignore
    /// struct Config {
    ///     cache: RefCell<HashMap<String, String>>,
    /// }
    /// ```
    ///
    /// ### Example (good)
    ///
    /// ```rust,ignore
    /// // Return a new Config with the cache populated
    /// fn with_cached(config: &Config, entries: HashMap<String, String>) -> Config {
    ///     Config { cache: entries, ..config.clone() }
    /// }
    /// ```
    pub FORBID_INTERIOR_MUTABILITY,
    Deny,
    "interior-mutability types are forbidden outside boundary modules",
    ForbidInteriorMutability
}

#[derive(Default)]
pub struct ForbidInteriorMutability;

fn path_contains_boundary_component(path: &Path) -> bool {
    path.components().any(|component| match component {
        Component::Normal(name) => {
            let name_str = name.to_str().unwrap_or("");
            let stem = name_str.strip_suffix(".rs").unwrap_or(name_str);
            BOUNDARY_MODULES.iter().any(|b| *b == stem)
        }
        _ => false,
    })
}

fn is_in_boundary_module(cx: &EarlyContext<'_>, span: rustc_span::Span) -> bool {
    let source_map = cx.sess().source_map();
    let filename = source_map.span_to_filename(span);
    match &filename {
        FileName::Real(real_name) => {
            if let Some(path) = real_name.local_path() {
                path_contains_boundary_component(path)
            } else {
                false
            }
        }
        _ => false,
    }
}

fn matches_forbidden_type(ty: &Ty) -> Option<&'static str> {
    if let TyKind::Path(None, path) = &ty.kind {
        let segments: Vec<&str> = path
            .segments
            .iter()
            .map(|s| s.ident.name.as_str())
            .collect();
        for (forbidden_segments, display_name) in FORBIDDEN_TYPES {
            // Match if the type path ends with the forbidden segments
            if segments.len() >= forbidden_segments.len() {
                let tail = &segments[segments.len() - forbidden_segments.len()..];
                if tail
                    .iter()
                    .zip(forbidden_segments.iter())
                    .all(|(a, b)| *a == *b)
                {
                    return Some(display_name);
                }
            }
        }
    }
    None
}

/// Recursively walk a type AST node to find forbidden interior-mutability
/// types, including when they appear as generic arguments (e.g.,
/// `Arc<Mutex<T>>`).
fn check_ty_recursive(cx: &EarlyContext<'_>, ty: &Ty) {
    // Check the outermost type itself
    if let Some(display_name) = matches_forbidden_type(ty) {
        if !is_in_boundary_module(cx, ty.span) {
            cx.span_lint(FORBID_INTERIOR_MUTABILITY, ty.span, |diag| {
                diag.primary_message(format!(
                    "interior-mutability type `{display_name}` is forbidden outside boundary modules"
                ));
                diag.help(
                    "prefer immutable data structures and return new values instead of \
                     mutating behind shared references",
                );
                diag.note(
                    "if interior mutability is genuinely required for I/O or caching at the \
                     outermost boundary, move this code into a boundary module \
                     (io/, runtime/, ffi/, boundary/)",
                );
            });
        }
    }

    // Walk into generic arguments to catch patterns like Arc<Mutex<T>>
    if let TyKind::Path(_, path) = &ty.kind {
        for segment in &path.segments {
            if let Some(args) = &segment.args {
                if let rustc_ast::ast::GenericArgs::AngleBracketed(angle_args) = args.as_ref() {
                    for arg in &angle_args.args {
                        if let rustc_ast::ast::AngleBracketedArg::Arg(
                            rustc_ast::ast::GenericArg::Type(inner_ty),
                        ) = arg
                        {
                            check_ty_recursive(cx, inner_ty);
                        }
                    }
                }
            }
        }
    }
}

impl EarlyLintPass for ForbidInteriorMutability {
    fn check_item(&mut self, cx: &EarlyContext<'_>, item: &Item) {
        // Check struct fields
        if let ItemKind::Struct(_, _, variant_data) = &item.kind {
            for field in variant_data.fields() {
                check_ty_recursive(cx, &field.ty);
            }
        }
    }

    fn check_ty(&mut self, cx: &EarlyContext<'_>, ty: &Ty) {
        check_ty_recursive(cx, ty);
    }
}

#[cfg(test)]
mod tests {
    use super::path_contains_boundary_component;
    use std::path::Path;

    #[test]
    fn boundary_io_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/io/cache.rs"
        )));
    }

    #[test]
    fn non_boundary_module_is_not_detected() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/config/settings.rs"
        )));
    }

    #[test]
    fn ui() {
        dylint_testing::ui_test(env!("CARGO_PKG_NAME"), "ui");
    }
}
