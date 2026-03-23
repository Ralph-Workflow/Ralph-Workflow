#![feature(rustc_private)]
// ── Lint policy ──
// These rules enforce functional programming principles and boundary-based I/O
// separation.  The rules themselves (what they forbid, where they permit
// exceptions) MUST NOT be altered.  If an *implementation* has a bug — false
// positives, false negatives, or code that contradicts a principle — fix the
// implementation.  The spirit of each rule is authoritative, not the current code.
#![deny(warnings)]
#![deny(clippy::all)]

extern crate rustc_ast;
extern crate rustc_hir;
extern crate rustc_lint;
extern crate rustc_middle;
extern crate rustc_session;
extern crate rustc_span;

mod boundary;
mod domain;
mod runtime;

dylint_linting::dylint_library!();

#[expect(clippy::no_mangle_with_rust_abi, reason = "Required by dylint API")]
#[unsafe(no_mangle)]
pub fn register_lints(sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    // FP lints: mutation and imperative patterns
    boundary::forbid_mut_binding::register_lints(sess, lint_store);
    boundary::forbid_imperative_loops::register_lints(sess, lint_store);
    boundary::forbid_mutating_receiver_methods::register_lints(sess, lint_store);
    boundary::forbid_interior_mutability::register_lints(sess, lint_store);

    // Boundary lints: I/O effects
    boundary::forbid_terminal_output::register_lints(sess, lint_store);
    boundary::forbid_io_effects::register_lints(sess, lint_store);
    boundary::forbid_nested_boundary_modules::register_lints(sess, lint_store);
    boundary::forbid_domain_boundary_dependencies::register_lints(sess, lint_store);
    boundary::forbid_boundary_retry_loops::register_lints(sess, lint_store);
    boundary::forbid_boundary_policy_calls::register_lints(sess, lint_store);
    boundary::forbid_raw_effect_types_in_public_apis::register_lints(sess, lint_store);
    boundary::forbid_result_swallowing::register_lints(sess, lint_store);
    boundary::boundary_function_too_complex::register_lints(sess, lint_store);

    // Code quality lints (runtime - uses std::env)
    runtime::file_length::register_lints(sess, lint_store);
}

#[cfg(test)]
mod tests {
    #[test]
    fn ui() {
        dylint_testing::ui_test(env!("CARGO_PKG_NAME"), "ui");
    }
}
