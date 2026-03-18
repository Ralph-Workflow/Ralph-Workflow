//! Lint: `FORBID_MUTATING_RECEIVER_METHODS`
//!
//! Rejects calls to methods with `&mut self` receivers unless the receiver
//! type is an inherently-effectful I/O type or the call site is in a boundary
//! module.
//!
//! ## FP principle: referential transparency and value semantics
//!
//! In Haskell, data structures are persistent — `Data.Map.insert` returns a
//! *new* map rather than mutating the old one. This preserves referential
//! transparency.
//!
//! In domain code, collections should be rebuilt functionally:
//! - `map.insert(k, v)` → `map.into_iter().chain([(k, v)]).collect()`
//! - `vec.push(item)` → `vec.into_iter().chain([item]).collect()`
//!
//! See `docs/code-style/functional-transformations.md` lines 929-930.
//!
//! ## Boundary exceptions
//!
//! I/O handles and OS process objects inherently require mutation — they
//! represent external resources, not values. Their types are allowlisted.

use crate::domain::boundary::is_in_boundary_module;
use rustc_hir::{Expr, ExprKind, Mutability};
use rustc_lint::{LateContext, LateLintPass, LintContext};
use rustc_middle::ty::{self, Ty};
use rustc_session::{declare_lint, declare_lint_pass};

declare_lint! {
    /// ### What it does
    ///
    /// Rejects calls to `&mut self` methods outside boundary modules and
    /// allowlisted I/O types.
    ///
    /// ### Example (bad — in-place mutation)
    ///
    /// ```rust,ignore
    /// vec.push(item);  // &mut self - violates FP
    /// map.insert(k, v);  // &mut self - violates FP
    /// ```
    ///
    /// ### Example (good — returns new value)
    ///
    /// ```rust,ignore
    /// let vec = vec.into_iter().chain([item]).collect();
    /// let map = map.into_iter().chain([(k, v)]).collect();
    /// ```
    pub FORBID_MUTATING_RECEIVER_METHODS,
    Warn,
    "calls to `&mut self` methods are forbidden outside boundary modules and types"
}

const ALLOWED_RECEIVER_TYPES: &[&str] = &[
    // ━━━ I/O handles ━━━
    // These represent external resources (files, sockets, streams) that are
    // inherently effectful. Mutation here is unavoidable and isolated at boundaries.
    "std::io::BufWriter",
    "std::io::BufReader",
    "std::io::Cursor",
    "std::io::Read",
    "std::io::Write",
    "std::fs::File",
    // ━━━ Network handles ━━━
    // Represent external OS socket resources, inherently effectful
    "std::net::TcpStream",
    "std::net::UdpSocket",
    // ━━━ Process handles ━━━
    // Represent OS process objects, inherently effectful external resources
    "std::process::Command",
    "std::process::Child",
    "std::process::ChildStdin",
    "std::process::ChildStdout",
    "std::process::ChildStderr",
    // ━━━ Iterator ━━━
    // Iterator is a state machine where `.next()` advances state (consuming).
    // This is the standard Rust way to traverse sequences.
    "std::iter::Iterator",
    // ━━━ External types ━━━
    // DetectionResults is designed to be mutated during language detection
    "language_detector::signatures::detectors::DetectionResults",
    "ralph_workflow::language_detector::signatures::detectors::DetectionResults",
];

declare_lint_pass!(ForbidMutatingReceiverMethods => [FORBID_MUTATING_RECEIVER_METHODS]);

pub fn register_lints(_sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    lint_store.register_lints(&[FORBID_MUTATING_RECEIVER_METHODS]);
    lint_store.register_late_pass(|_| Box::new(ForbidMutatingReceiverMethods));
}

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
    is_allowed_receiver_str(&format!("{ty}"))
}

/// Methods that are allowed to take `&mut self` regardless of context.
///
/// IMPORTANT: Only add methods here that are:
/// 1. Iterator adapter methods (next, position, find, etc.)
/// 2. Domain-specific methods that CANNOT conflict with std collection names
/// 3. I/O operations (read, write, flush)
///
/// DO NOT add generic collection method names like "push", "insert", "extend",
/// "clear" — these would allow Vec::push, HashMap::insert, etc. to bypass the
/// lint entirely!
///
/// If a custom type needs a method with a collection-like name, add that type
/// to ALLOWED_RECEIVER_TYPES instead of adding the method name here.
const ALLOWED_METHODS: &[&str] = &[
    // ━━━ Iterator methods ━━━
    "next",     // Iterator::next() - standard traversal
    "any",      // Iterator::any() - short-circuit search
    "all",      // Iterator::all() - short-circuit validation
    "find",     // Iterator::find() - standard search
    "find_map", // Iterator::find_map() - search with transform
    "filter",   // Iterator::filter() - conditional selection
    "map",      // Iterator::map() - transformation
    "flat_map", // Iterator::flat_map() - flattening transform
    "fold",     // Iterator::fold() - accumulation
    "reduce",   // Iterator::reduce() - accumulation without init
    "try_fold", // Iterator::try_fold() - fallible accumulation
    "position", // Iterator::position() - index search
    // ━━━ I/O operations ━━━
    "read",            // Read trait
    "read_json",       // Custom I/O operation
    "read_to_string",  // Read trait extension
    "read_tree",       // Custom I/O operation
    "read_event_into", // Custom I/O operation
    "write_all",       // Write trait
    "write_str",       // Write trait extension
    "write",           // Write trait
    "flush",           // Write trait
    "execute",         // Process execution
    "try_wait",        // Process::try_wait()
    // ━━━ State management in event loop/pipeline ━━━
    // These are in core orchestration code (reducer, pipeline, checkpoint)
    // and manage internal state during event processing
    "update_state",                      // Reducer state transitions
    "add_step_bounded",                  // Bounded queue operations
    "take",                              // Option::take(), bounded operations
    "replace_execution_history_bounded", // Bounded history management
    "set_current_message_id",            // Event tracking
    "on_message_stop",                   // Event lifecycle
    // ━━━ Configuration and setup ━━━
    // These mutate configuration objects during app initialization
    "apply_ccs_aliases",     // Config mutation during setup
    "apply_agent_overrides", // Config mutation during setup
    "set_ccs_aliases",       // Config mutation during setup
    "apply_unified_config",  // Config mutation during setup
    "set_opencode_catalog",  // Config mutation during setup
    "set_readonly",          // Config mutation during setup
    "config_mut",            // Config accessor
    "set_mode",              // Mode configuration
    // ━━━ Workspace and boundary operations ━━━
    "normalize_agent_chain_for_invocation", // Agent chain processing
    "capture_file_with_workspace",          // Workspace operation
    "capture_file_impl",                    // Workspace operation
    "trim_text",                            // Text normalization
    "recurse_untracked_dirs",               // Directory traversal
    "include_untracked",                    // Git state capture
    "capture_git_state",                    // Git state capture
    "add_path",                             // Path management
    "remove_path",                          // Path management
    // ━━━ Logging and monitoring ━━━
    "log_effect", // Effect logging
    "disarm",     // Guard disarm
    "start",      // Timer/tracer start
    "register",   // Registration
    "finish",     // Completion
    // ━━━ Formatting and debugging ━━━
    "body_mut",     // HTTP body accessor
    "mark_owned",   // Ownership marking
    "field",        // Debug formatter
    "debug_struct", // Debug formatter
    "watch",        // Debugging/monitoring
    "custom_flags", // Flag configuration
    "create_new",   // Creation operation
    // ━━━ String pool ━━━
    "intern_str",    // String interning (performance optimization)
    "intern_string", // String interning (performance optimization)
];

fn mutating_receiver_note() -> &'static str {
    "in-place `&mut self` updates hide state changes behind shared control flow; prefer value semantics so domain transformations return new values and remain easier to reason about, test, and keep referentially transparent. If mutation is genuinely required for I/O handles or process objects, keep it at the boundary. Style guides: `docs/code-style/functional-transformations.md` and `docs/code-style/boundaries.md`."
}

impl<'tcx> LateLintPass<'tcx> for ForbidMutatingReceiverMethods {
    fn check_expr(&mut self, cx: &LateContext<'tcx>, expr: &'tcx Expr<'tcx>) {
        let ExprKind::MethodCall(method_name, receiver, _, _) = &expr.kind else {
            return;
        };

        let method_str = method_name.ident.name.as_str();
        if ALLOWED_METHODS.iter().any(|&m| m == method_str) {
            return;
        }

        if is_in_boundary_module(cx, expr.span) {
            return;
        }

        let typeck = cx.typeck_results();
        let receiver_ty = typeck.expr_ty(receiver);

        if is_allowed_receiver_type(receiver_ty) {
            return;
        }

        let Some(def_id) = typeck.type_dependent_def_id(expr.hir_id) else {
            return;
        };

        let fn_sig = cx.tcx.fn_sig(def_id).instantiate_identity();
        let inputs = fn_sig.skip_binder().inputs();

        if inputs.is_empty() {
            return;
        }

        let self_ty = inputs[0];
        let takes_mut_self = matches!(self_ty.kind(), ty::Ref(_, _, Mutability::Mut));

        if !takes_mut_self {
            return;
        }

        cx.span_lint(FORBID_MUTATING_RECEIVER_METHODS, expr.span, |diag| {
            diag.primary_message(format!(
                "call to `&mut self` method `{}` is forbidden outside boundary modules",
                method_name.ident.name
            ));
            diag.help(
                "for collections: rebuild with `.chain([item]).collect()` or use itertools \
                 (`.sorted_by_key()`, `.unique()`, `.rev()`). For structs: use builder pattern \
                 (`with_*` methods) or struct-update syntax (`..state`). \
                 See `docs/code-style/functional-transformations.md`.",
            );
            diag.note(mutating_receiver_note());
        });
    }
}

#[cfg(test)]
mod tests {
    use super::{is_allowed_receiver_str, mutating_receiver_note};

    #[test]
    fn allows_bufwriter() {
        assert!(is_allowed_receiver_str("std::io::BufWriter<std::fs::File>"));
    }

    #[test]
    fn allows_command() {
        assert!(is_allowed_receiver_str("std::process::Command"));
    }

    #[test]
    fn allows_iterator() {
        assert!(is_allowed_receiver_str("std::iter::Iterator"));
    }

    #[test]
    fn rejects_hashmap() {
        assert!(!is_allowed_receiver_str(
            "std::collections::HashMap<String, String>"
        ));
    }

    #[test]
    fn rejects_vec() {
        assert!(!is_allowed_receiver_str("std::vec::Vec<i32>"));
    }

    #[test]
    fn rejects_string() {
        assert!(!is_allowed_receiver_str("std::string::String"));
    }

    #[test]
    fn rejects_custom_type() {
        assert!(!is_allowed_receiver_str("my_app::Config"));
    }

    #[test]
    fn note_explains_why_in_place_mutation_is_forbidden() {
        let note = mutating_receiver_note();

        assert!(note.contains("value semantics"));
        assert!(note.contains("referential transparency"));
    }
}
