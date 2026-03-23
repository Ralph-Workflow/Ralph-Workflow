//! Systems programming language review guidelines
//!
//! Contains guidelines for C, C++, and C# projects.

use super::base::ReviewGuidelines;

/// Add C/C++ guidelines to the review
pub fn add_c_cpp_guidelines(guidelines: ReviewGuidelines) -> ReviewGuidelines {
    ReviewGuidelines {
        quality_checks: guidelines
            .quality_checks
            .into_iter()
            .chain([
                "Check return values of system calls".to_string(),
                "Use RAII for resource management (C++)".to_string(),
                "Prefer smart pointers over raw pointers (C++)".to_string(),
                "Initialize all variables".to_string(),
            ])
            .collect(),
        security_checks: guidelines
            .security_checks
            .into_iter()
            .chain([
                "Check buffer bounds before operations".to_string(),
                "Use safe string functions (strncpy, snprintf)".to_string(),
                "Validate array indices".to_string(),
                "Check for integer overflow".to_string(),
                "Avoid use-after-free".to_string(),
            ])
            .collect(),
        performance_checks: guidelines
            .performance_checks
            .into_iter()
            .chain([
                "Minimize memory allocations in hot paths".to_string(),
                "Use const references for large objects".to_string(),
                "Consider cache locality".to_string(),
            ])
            .collect(),
        anti_patterns: guidelines
            .anti_patterns
            .into_iter()
            .chain([
                "Avoid raw new/delete (C++)".to_string(),
                "Don't use C-style casts (C++)".to_string(),
                "Avoid global mutable state".to_string(),
            ])
            .collect(),
        ..guidelines
    }
}

pub fn add_csharp_guidelines(guidelines: ReviewGuidelines) -> ReviewGuidelines {
    ReviewGuidelines {
        quality_checks: guidelines
            .quality_checks
            .into_iter()
            .chain([
                "Use async/await for I/O operations".to_string(),
                "Implement IDisposable correctly".to_string(),
                "Use nullable reference types".to_string(),
                "Follow C# naming conventions".to_string(),
            ])
            .collect(),
        security_checks: guidelines
            .security_checks
            .into_iter()
            .chain([
                "Use parameterized queries with Entity Framework".to_string(),
                "Validate model binding input".to_string(),
                "Use HTTPS and proper authentication".to_string(),
            ])
            .collect(),
        anti_patterns: guidelines
            .anti_patterns
            .into_iter()
            .chain([
                "Avoid async void (except event handlers)".to_string(),
                "Don't catch generic Exception".to_string(),
                "Avoid blocking on async code".to_string(),
            ])
            .collect(),
        ..guidelines
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_c_cpp_guidelines() {
        let guidelines = add_c_cpp_guidelines(ReviewGuidelines::default());

        // Should have C/C++ security checks
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("buffer") || c.contains("bounds")));
        assert!(guidelines
            .security_checks
            .iter()
            .any(|c| c.contains("overflow")));
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("RAII") || c.contains("smart pointer")));
    }

    #[test]
    fn test_csharp_guidelines() {
        let guidelines = add_csharp_guidelines(ReviewGuidelines::default());

        // Should have C# specific checks
        assert!(guidelines
            .quality_checks
            .iter()
            .any(|c| c.contains("async/await") || c.contains("IDisposable")));
        assert!(guidelines
            .anti_patterns
            .iter()
            .any(|c| c.contains("async void")));
    }
}
