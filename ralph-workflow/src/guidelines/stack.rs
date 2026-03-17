//! Stack-based guideline aggregation.

use crate::language_detector::ProjectStack;

use super::base::ReviewGuidelines;
use super::{functional, go, java, javascript, php, python, ruby, rust, systems};

impl ReviewGuidelines {
    /// Generate guidelines for a specific project stack.
    ///
    /// This method supports multi-language projects by:
    /// 1. Adding guidelines for the primary language
    /// 2. Adding guidelines for all secondary languages
    /// 3. Framework-specific guidelines are added by each language module
    pub(crate) fn for_stack(stack: &ProjectStack) -> Self {
        let base = Self::default();

        let with_primary = add_language_guidelines(base, &stack.primary_language, stack);

        stack
            .secondary_languages
            .iter()
            .fold(with_primary, |acc, lang| {
                add_language_guidelines(acc, lang, stack)
            })
    }
}

/// Add guidelines for a specific language.
fn add_language_guidelines(
    guidelines: ReviewGuidelines,
    language: &str,
    stack: &ProjectStack,
) -> ReviewGuidelines {
    match language {
        "Rust" => rust::add_guidelines(guidelines, stack),
        "Python" => python::add_guidelines(guidelines, stack),
        "JavaScript" => javascript::add_javascript_guidelines(guidelines, stack),
        "TypeScript" => javascript::add_typescript_guidelines(guidelines, stack),
        "Go" => go::add_guidelines(guidelines, stack),
        "Java" => java::add_java_guidelines(guidelines, stack),
        "Kotlin" => java::add_kotlin_guidelines(guidelines, stack),
        "Ruby" => ruby::add_guidelines(guidelines, stack),
        "PHP" => php::add_guidelines(guidelines, stack),
        "C" | "C++" => systems::add_c_cpp_guidelines(guidelines),
        "C#" => systems::add_csharp_guidelines(guidelines),
        "Elixir" => functional::add_elixir_guidelines(guidelines),
        "Scala" => functional::add_scala_guidelines(guidelines),
        "Swift" => functional::add_swift_guidelines(guidelines),
        _ => guidelines,
    }
}
