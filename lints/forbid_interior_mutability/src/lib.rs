#![feature(rustc_private)]
// ── Lint policy ──
// This rule enforces a functional programming principle.  The rule itself
// (what it forbids, where it permits exceptions) MUST NOT be altered.
// If the *implementation* has a bug — false positives, false negatives,
// or code that contradicts the principle it enforces — fix the
// implementation.  The spirit of the rule is authoritative, not the
// current code.
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
    /// ### FP principle: `&T` must mean truly immutable
    ///
    /// In Haskell, a value *is* immutable — there is no mechanism to
    /// mutate it behind a shared reference.  Rust's borrow checker
    /// approximates this with `&T`, but interior-mutability wrappers
    /// (`Cell`, `RefCell`, `Mutex`, …) break the guarantee: code can
    /// mutate values behind `&T`, destroying referential transparency
    /// and making data flow invisible to callers.
    ///
    /// Keeping interior mutability out of domain code means `&T` in a
    /// function signature is a genuine promise of immutability, just
    /// as it would be in Haskell.
    ///
    /// ### Boundary exceptions
    ///
    /// Boundary code (I/O adapters, caches, runtime internals)
    /// sometimes genuinely needs interior mutability — Haskell uses
    /// `IORef` / `MVar` in its `IO` monad for the same reason.  Place
    /// such code in a module whose path contains one of the boundary
    /// markers (`io/`, `runtime/`, `ffi/`, `boundary/`).
    ///
    /// ### Example (bad — hidden mutation behind `&T`)
    ///
    /// ```rust,ignore
    /// struct Config {
    ///     cache: RefCell<HashMap<String, String>>,
    /// }
    /// ```
    ///
    /// ### Example (good — return a new value)
    ///
    /// ```rust,ignore
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

/// Check whether a sequence of path segments ends with a forbidden type's
/// segments.  Returns the display name when it matches.
fn segments_match_forbidden(segments: &[&str]) -> Option<&'static str> {
    FORBIDDEN_TYPES
        .iter()
        .find(|(forbidden_segments, _)| {
            segments.len() >= forbidden_segments.len() && {
                let tail = &segments[segments.len() - forbidden_segments.len()..];
                tail.iter()
                    .zip(forbidden_segments.iter())
                    .all(|(a, b)| *a == *b)
            }
        })
        .map(|(_, display_name)| *display_name)
}

fn matches_forbidden_type(ty: &Ty) -> Option<&'static str> {
    if let TyKind::Path(None, path) = &ty.kind {
        let segments: Vec<&str> = path
            .segments
            .iter()
            .map(|s| s.ident.name.as_str())
            .collect();
        segments_match_forbidden(&segments)
    } else {
        None
    }
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
                diag.help("use immutable data: build collections with `xs.map(|x| (x.key, x.val)).collect()` instead of `RefCell<HashMap>`. For state, use struct-update syntax (`..state`) or builder methods. See `docs/code-style/functional-transformations.md`.");
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
        path.segments
            .iter()
            .filter_map(|segment| segment.args.as_ref())
            .filter_map(|args| match args.as_ref() {
                rustc_ast::ast::GenericArgs::AngleBracketed(angle_args) => Some(angle_args),
                _ => None,
            })
            .flat_map(|angle_args| angle_args.args.iter())
            .filter_map(|arg| match arg {
                rustc_ast::ast::AngleBracketedArg::Arg(rustc_ast::ast::GenericArg::Type(
                    inner_ty,
                )) => Some(inner_ty),
                _ => None,
            })
            .for_each(|inner_ty| check_ty_recursive(cx, inner_ty));
    }
}

impl EarlyLintPass for ForbidInteriorMutability {
    fn check_item(&mut self, cx: &EarlyContext<'_>, item: &Item) {
        // Check struct fields
        if let ItemKind::Struct(_, _, variant_data) = &item.kind {
            variant_data
                .fields()
                .iter()
                .for_each(|field| check_ty_recursive(cx, &field.ty));
        }
    }

    fn check_ty(&mut self, cx: &EarlyContext<'_>, ty: &Ty) {
        check_ty_recursive(cx, ty);
    }
}

#[cfg(test)]
mod tests {
    use super::{path_contains_boundary_component, segments_match_forbidden};
    use std::path::Path;

    // ── path_contains_boundary_component ──

    #[test]
    fn boundary_io_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/io/cache.rs"
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

    #[test]
    fn non_boundary_module_is_not_detected() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/config/settings.rs"
        )));
    }

    #[test]
    fn non_boundary_domain_module_is_not_detected() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/pipeline/state.rs"
        )));
    }

    // ── segments_match_forbidden ──

    #[test]
    fn detects_bare_mutex() {
        assert_eq!(
            segments_match_forbidden(&["Mutex"]),
            Some("std::sync::Mutex")
        );
    }

    #[test]
    fn detects_fully_qualified_mutex() {
        assert_eq!(
            segments_match_forbidden(&["std", "sync", "Mutex"]),
            Some("std::sync::Mutex")
        );
    }

    #[test]
    fn detects_bare_cell() {
        assert_eq!(segments_match_forbidden(&["Cell"]), Some("std::cell::Cell"));
    }

    #[test]
    fn detects_bare_refcell() {
        assert_eq!(
            segments_match_forbidden(&["RefCell"]),
            Some("std::cell::RefCell")
        );
    }

    #[test]
    fn detects_bare_rwlock() {
        assert_eq!(
            segments_match_forbidden(&["RwLock"]),
            Some("std::sync::RwLock")
        );
    }

    #[test]
    fn detects_bare_oncelock() {
        assert_eq!(
            segments_match_forbidden(&["OnceLock"]),
            Some("std::sync::OnceLock")
        );
    }

    #[test]
    fn detects_bare_lazylock() {
        assert_eq!(
            segments_match_forbidden(&["LazyLock"]),
            Some("std::sync::LazyLock")
        );
    }

    #[test]
    fn detects_bare_oncecell() {
        assert_eq!(
            segments_match_forbidden(&["OnceCell"]),
            Some("once_cell::sync::OnceCell")
        );
    }

    #[test]
    fn detects_bare_unsafecell() {
        assert_eq!(
            segments_match_forbidden(&["UnsafeCell"]),
            Some("std::cell::UnsafeCell")
        );
    }

    #[test]
    fn non_forbidden_type_returns_none() {
        assert_eq!(segments_match_forbidden(&["Arc"]), None);
    }

    #[test]
    fn non_forbidden_custom_type_returns_none() {
        assert_eq!(segments_match_forbidden(&["MyCache"]), None);
    }

    #[test]
    fn empty_segments_returns_none() {
        let empty: &[&str] = &[];
        assert_eq!(segments_match_forbidden(empty), None);
    }

    #[test]
    fn partial_match_does_not_fire() {
        // "MutexGuard" should not match "Mutex"
        assert_eq!(segments_match_forbidden(&["MutexGuard"]), None);
    }

    #[test]
    fn substring_in_path_does_not_fire() {
        // A path containing "Mutex" as a module name but ending in a safe type
        assert_eq!(segments_match_forbidden(&["Mutex", "Guard"]), None);
    }

    // ── UI tests ──

    #[test]
    fn ui() {
        dylint_testing::ui_test(env!("CARGO_PKG_NAME"), "ui");
    }
}
