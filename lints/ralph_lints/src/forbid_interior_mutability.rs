//! Lint: `FORBID_INTERIOR_MUTABILITY`
//!
//! Rejects usage of interior-mutability types (`Cell`, `RefCell`, `UnsafeCell`,
//! `Mutex`, `RwLock`, `OnceLock`, `LazyLock`, `OnceCell`) in type annotations
//! outside boundary modules.
//!
//! ## FP principle: `&T` must mean truly immutable
//!
//! In Haskell, a value *is* immutable — there is no mechanism to mutate it
//! behind a shared reference. Rust's interior-mutability wrappers break this
//! guarantee, making data flow invisible to callers.
//!
//! ## Boundary exceptions
//!
//! Boundary code (I/O adapters, caches, runtime internals) sometimes genuinely
//! needs interior mutability — Haskell uses `IORef`/`MVar` in its `IO` monad
//! for the same reason.

use crate::boundary::is_in_boundary_module;
use rustc_ast::ast::{AngleBracketedArg, GenericArg, GenericArgs, Item, ItemKind, Ty, TyKind};
use rustc_lint::{EarlyContext, EarlyLintPass, LintContext};
use rustc_session::{declare_lint, declare_lint_pass};

declare_lint! {
    /// ### What it does
    ///
    /// Rejects usage of interior-mutability types outside boundary modules.
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
    "interior-mutability types are forbidden outside boundary modules"
}

/// Interior-mutability types that are forbidden in application code.
///
/// Each entry is `(path_segments, display_name)`.
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

declare_lint_pass!(ForbidInteriorMutability => [FORBID_INTERIOR_MUTABILITY]);

pub fn register_lints(_sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    lint_store.register_lints(&[FORBID_INTERIOR_MUTABILITY]);
    lint_store.register_early_pass(|| Box::new(ForbidInteriorMutability));
}

/// Check whether path segments end with a forbidden type's segments.
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

/// Check if a type matches a forbidden interior-mutability type.
fn matches_forbidden_type(ty: &Ty) -> Option<&'static str> {
    let TyKind::Path(None, path) = &ty.kind else {
        return None;
    };

    let segments: Vec<&str> = path
        .segments
        .iter()
        .map(|s| s.ident.name.as_str())
        .collect();

    segments_match_forbidden(&segments)
}

/// Recursively check a type and its generic arguments for forbidden types.
fn check_ty_recursive(cx: &EarlyContext<'_>, ty: &Ty) {
    // Check the outermost type
    if let Some(display_name) = matches_forbidden_type(ty) {
        if !is_in_boundary_module(cx, ty.span) {
            cx.span_lint(FORBID_INTERIOR_MUTABILITY, ty.span, |diag| {
                diag.primary_message(format!(
                    "interior-mutability type `{display_name}` is forbidden outside boundary modules"
                ));
                diag.help(
                    "use immutable data: build collections with \
                     `xs.map(|x| (x.key, x.val)).collect()` instead of `RefCell<HashMap>`. \
                     For state, use struct-update syntax (`..state`) or builder methods. \
                     See `docs/code-style/functional-transformations.md`.",
                );
                diag.note(
                    "if interior mutability is genuinely required for I/O or caching at the \
                     outermost boundary, move this code into a boundary module \
                     (io/, runtime/, ffi/, boundary/)",
                );
            });
        }
    }

    // Recursively check generic arguments (e.g., Arc<Mutex<T>>)
    if let TyKind::Path(_, path) = &ty.kind {
        path.segments
            .iter()
            .filter_map(|segment| segment.args.as_ref())
            .filter_map(|args| match args.as_ref() {
                GenericArgs::AngleBracketed(angle_args) => Some(angle_args),
                _ => None,
            })
            .flat_map(|angle_args| angle_args.args.iter())
            .filter_map(|arg| match arg {
                AngleBracketedArg::Arg(GenericArg::Type(inner_ty)) => Some(inner_ty),
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
    use super::segments_match_forbidden;

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
    fn detects_bare_refcell() {
        assert_eq!(
            segments_match_forbidden(&["RefCell"]),
            Some("std::cell::RefCell")
        );
    }

    #[test]
    fn non_forbidden_type_returns_none() {
        assert_eq!(segments_match_forbidden(&["Arc"]), None);
    }

    #[test]
    fn partial_match_does_not_fire() {
        assert_eq!(segments_match_forbidden(&["MutexGuard"]), None);
    }
}
