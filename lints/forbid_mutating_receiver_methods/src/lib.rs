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

extern crate rustc_hir;
extern crate rustc_middle;
extern crate rustc_span;

use rustc_hir::Expr;
use rustc_hir::ExprKind;
use rustc_lint::{LateContext, LateLintPass, LintContext};
use rustc_middle::ty::{self, Ty};
use rustc_span::FileName;
use std::path::{Component, Path};

/// Boundary module path components where `&mut self` method calls are permitted.
const BOUNDARY_MODULES: &[&str] = &["io", "runtime", "ffi", "boundary"];

/// Types whose `&mut self` methods are always permitted (fully qualified paths).
///
/// These are types at the outermost boundary of the application that inherently
/// require mutation (e.g., I/O handles, process objects).
///
/// Standard collections (`HashMap`, `Vec`, etc.) are intentionally **not** in
/// this list.  In the FP style of this project, domain code should build new
/// collections via iterator combinators (`collect`, `chain`, `extend` returning
/// a new value) rather than mutate them in place.  Collection mutation is
/// permitted inside boundary modules, which are already exempt from this lint
/// by the boundary-module path check.
const ALLOWED_RECEIVER_TYPES: &[&str] = &[
    // Standard I/O
    "std::io::BufWriter",
    "std::io::BufReader",
    "std::io::Cursor",
    "std::fs::File",
    "std::net::TcpStream",
    "std::net::UdpSocket",
    // Process / OS
    "std::process::Command",
    "std::process::Child",
    "std::process::ChildStdin",
    "std::process::ChildStdout",
    "std::process::ChildStderr",
];

dylint_linting::impl_late_lint! {
    /// ### What it does
    ///
    /// Rejects calls to methods with `&mut self` receivers unless the
    /// receiver type appears in an allowlist of inherently-effectful I/O
    /// types, or the call site is inside a boundary module.
    ///
    /// ### FP principle: referential transparency and value semantics
    ///
    /// In Haskell, data structures are persistent — `Data.Map.insert`
    /// returns a *new* map rather than mutating the old one.  This
    /// preserves referential transparency: every expression can be
    /// replaced by its value without changing the program's meaning.
    ///
    /// Calling `&mut self` methods in Rust breaks that property.  The
    /// caller is mutating state in place, which makes data flow harder
    /// to trace and hides side effects from callers.  Prefer returning
    /// new values via builder patterns (`with_value`, `into_iter()
    /// .chain().collect()`, struct-update syntax) over mutating in
    /// place.
    ///
    /// Standard collections (`HashMap`, `Vec`, etc.) are intentionally
    /// **not** allowlisted globally.  Domain code should build
    /// collections via iterator pipelines (`collect`, `chain`,
    /// `extend` returning a new value).  Collection mutation is
    /// permitted inside boundary modules, which are already exempt.
    ///
    /// ### Boundary exceptions
    ///
    /// I/O handles and OS process objects inherently require mutation
    /// — they represent external resources, not values.  Their types
    /// are allowlisted.  Additional boundary code should live in
    /// modules marked with a boundary path component (`io/`,
    /// `runtime/`, `ffi/`, `boundary/`).
    ///
    /// ### Example (bad — in-place mutation)
    ///
    /// ```rust,ignore
    /// config.set_value("key", "value"); // &mut self
    /// ```
    ///
    /// ### Example (good — returns new value)
    ///
    /// ```rust,ignore
    /// let config = config.with_value("key", "value"); // returns new Config
    /// ```
    pub FORBID_MUTATING_RECEIVER_METHODS,
    Deny,
    "calls to `&mut self` methods are forbidden outside boundary modules and types",
    ForbidMutatingReceiverMethods
}

#[derive(Default)]
pub struct ForbidMutatingReceiverMethods;

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

fn is_in_boundary_module(cx: &LateContext<'_>, span: rustc_span::Span) -> bool {
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

/// Check whether a type string (after reference stripping) matches an
/// allowed receiver type.  Extracted so the matching logic is unit-testable
/// without requiring a compiler `Ty` value.
fn is_allowed_receiver_str(ty_str: &str) -> bool {
    let base = ty_str
        .strip_prefix("&mut ")
        .or_else(|| ty_str.strip_prefix("&"))
        .unwrap_or(ty_str);

    ALLOWED_RECEIVER_TYPES
        .iter()
        .any(|allowed| base.starts_with(allowed))
}

fn is_allowed_receiver_type(ty: Ty<'_>) -> bool {
    let ty_str = format!("{ty}");
    is_allowed_receiver_str(&ty_str)
}

impl<'tcx> LateLintPass<'tcx> for ForbidMutatingReceiverMethods {
    fn check_expr(&mut self, cx: &LateContext<'tcx>, expr: &'tcx Expr<'tcx>) {
        let ExprKind::MethodCall(method_name, receiver, _, _) = &expr.kind else {
            return;
        };

        if is_in_boundary_module(cx, expr.span) {
            return;
        }

        // Get the type of the receiver
        let typeck = cx.typeck_results();
        let receiver_ty = typeck.expr_ty(receiver);

        // Check if receiver type is in the allowlist
        if is_allowed_receiver_type(receiver_ty) {
            return;
        }

        // Check if the method actually takes &mut self by looking at the
        // resolved method's signature
        let Some(def_id) = typeck.type_dependent_def_id(expr.hir_id) else {
            return;
        };

        let fn_sig = cx.tcx.fn_sig(def_id).instantiate_identity();
        let inputs = fn_sig.skip_binder().inputs();
        if inputs.is_empty() {
            return;
        }

        let self_ty = inputs[0];
        let takes_mut_self = matches!(self_ty.kind(), ty::Ref(_, _, rustc_hir::Mutability::Mut));

        if !takes_mut_self {
            return;
        }

        cx.span_lint(FORBID_MUTATING_RECEIVER_METHODS, expr.span, |diag| {
            diag.primary_message(format!(
                "call to `&mut self` method `{}` is forbidden outside boundary modules",
                method_name.ident.name
            ));
            diag.help("for collections: rebuild with `.chain([item]).collect()` or use itertools (`.sorted_by_key()`, `.unique()`, `.rev()`). For structs: use builder pattern (`with_*` methods) or struct-update syntax (`..state`). See `docs/code-style/functional-transformations.md`.");
        });
    }
}

#[cfg(test)]
mod tests {
    use super::{is_allowed_receiver_str, path_contains_boundary_component};
    use std::path::Path;

    // ── path_contains_boundary_component ──

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

    #[test]
    fn non_boundary_module_is_not_detected() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/pipeline/reducer.rs"
        )));
    }

    // ── is_allowed_receiver_str: allowed I/O and process types ──

    #[test]
    fn allows_bufwriter() {
        assert!(is_allowed_receiver_str("std::io::BufWriter<std::fs::File>"));
    }

    #[test]
    fn allows_command() {
        assert!(is_allowed_receiver_str("std::process::Command"));
    }

    #[test]
    fn allows_file() {
        assert!(is_allowed_receiver_str("std::fs::File"));
    }

    #[test]
    fn allows_tcp_stream() {
        assert!(is_allowed_receiver_str("std::net::TcpStream"));
    }

    #[test]
    fn allows_child() {
        assert!(is_allowed_receiver_str("std::process::Child"));
    }

    #[test]
    fn allows_ref_stripped_bufwriter() {
        assert!(is_allowed_receiver_str(
            "&mut std::io::BufWriter<std::fs::File>"
        ));
    }

    // ── is_allowed_receiver_str: collections must NOT be allowed globally ──

    #[test]
    fn rejects_hashmap_in_domain_code() {
        assert!(
            !is_allowed_receiver_str("std::collections::HashMap<String, String>"),
            "HashMap mutation should not be globally allowed; use boundary modules"
        );
    }

    #[test]
    fn rejects_btreemap_in_domain_code() {
        assert!(
            !is_allowed_receiver_str("std::collections::BTreeMap<String, i32>"),
            "BTreeMap mutation should not be globally allowed; use boundary modules"
        );
    }

    #[test]
    fn rejects_hashset_in_domain_code() {
        assert!(
            !is_allowed_receiver_str("std::collections::HashSet<String>"),
            "HashSet mutation should not be globally allowed; use boundary modules"
        );
    }

    #[test]
    fn rejects_btreeset_in_domain_code() {
        assert!(
            !is_allowed_receiver_str("std::collections::BTreeSet<i32>"),
            "BTreeSet mutation should not be globally allowed; use boundary modules"
        );
    }

    #[test]
    fn rejects_vecdeque_in_domain_code() {
        assert!(
            !is_allowed_receiver_str("std::collections::VecDeque<u8>"),
            "VecDeque mutation should not be globally allowed; use boundary modules"
        );
    }

    // ── is_allowed_receiver_str: custom types must not be allowed ──

    #[test]
    fn rejects_custom_config_type() {
        assert!(!is_allowed_receiver_str("my_app::Config"));
    }

    #[test]
    fn rejects_bare_string() {
        assert!(!is_allowed_receiver_str("String"));
    }

    #[test]
    fn rejects_vec() {
        assert!(!is_allowed_receiver_str("Vec<i32>"));
    }

    // ── UI tests ──

    #[test]
    fn ui() {
        dylint_testing::ui_test(env!("CARGO_PKG_NAME"), "ui");
    }
}
