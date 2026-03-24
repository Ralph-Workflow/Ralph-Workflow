use super::SignatureFiles;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

fn push_unique(frameworks: Vec<String>, value: impl Into<String>) -> Vec<String> {
    let value = value.into();
    if frameworks.iter().any(|v| v == &value) {
        frameworks
    } else {
        frameworks
            .into_iter()
            .chain(std::iter::once(value))
            .collect()
    }
}

fn combine_unique(items: &[String]) -> Option<String> {
    match items.len() {
        0 => None,
        1 => Some(items[0].clone()),
        _ => Some(
            items
                .iter()
                .map(String::as_str)
                .collect::<Vec<_>>()
                .join(" + "),
        ),
    }
}

/// Detection results accumulator.
pub(super) struct DetectionResults {
    frameworks: Vec<String>,
    test_frameworks: Vec<String>,
    package_managers: Vec<String>,
}

impl DetectionResults {
    pub(super) const fn new() -> Self {
        Self {
            frameworks: Vec::new(),
            test_frameworks: Vec::new(),
            package_managers: Vec::new(),
        }
    }

    #[must_use]
    pub(super) fn with_framework(self, framework: impl Into<String>) -> Self {
        Self {
            frameworks: push_unique(self.frameworks, framework),
            test_frameworks: self.test_frameworks,
            package_managers: self.package_managers,
        }
    }

    #[must_use]
    pub(super) fn with_test_framework(self, framework: impl Into<String>) -> Self {
        Self {
            frameworks: self.frameworks,
            test_frameworks: push_unique(self.test_frameworks, framework),
            package_managers: self.package_managers,
        }
    }

    #[must_use]
    pub(super) fn with_package_manager(self, manager: impl Into<String>) -> Self {
        Self {
            frameworks: self.frameworks,
            test_frameworks: self.test_frameworks,
            package_managers: push_unique(self.package_managers, manager),
        }
    }

    pub(super) fn finish(self) -> (Vec<String>, Option<String>, Option<String>) {
        (
            self.frameworks,
            combine_unique(&self.test_frameworks),
            combine_unique(&self.package_managers),
        )
    }
}

pub(super) fn detect_rust(
    file_contents: &HashMap<PathBuf, String>,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let Some(cargo_files) = signatures.by_name_lower.get("cargo.toml") else {
        return results;
    };

    let results = results.with_package_manager("Cargo");

    cargo_files.iter().fold(results, |results, path| {
        let content = match file_contents.get(path) {
            Some(c) => c.to_lowercase(),
            None => return results,
        };

        let results = if content.contains("[dev-dependencies]") || content.contains("[[test]]") {
            results.with_test_framework("cargo test")
        } else {
            results
        };

        [
            ("actix", "Actix"),
            ("axum", "Axum"),
            ("rocket", "Rocket"),
            ("tokio", "Tokio"),
            ("warp", "Warp"),
            ("tauri", "Tauri"),
            ("leptos", "Leptos"),
            ("yew", "Yew"),
        ]
        .into_iter()
        .filter(|(name, _)| content.contains(name))
        .fold(results, |acc, (_, framework)| acc.with_framework(framework))
    })
}

pub(super) fn detect_python(
    file_contents: &HashMap<PathBuf, String>,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let paths = if let Some(p) = signatures.by_name_lower.get("pyproject.toml") {
        Some((p, "Poetry/pip"))
    } else if let Some(p) = signatures.by_name_lower.get("requirements.txt") {
        Some((p, "pip"))
    } else if signatures.by_name_lower.contains_key("setup.py") {
        return results.with_package_manager("setuptools");
    } else if signatures.by_name_lower.contains_key("pipfile") {
        return results.with_package_manager("Pipenv");
    } else {
        None
    };

    let Some((paths, pkg_mgr)) = paths else {
        return results;
    };

    let results = results.with_package_manager(pkg_mgr);

    paths.iter().fold(results, |results, path| {
        let content = match file_contents.get(path) {
            Some(c) => c.to_lowercase(),
            None => return results,
        };

        let results = if content.contains("pytest") {
            results.with_test_framework("pytest")
        } else {
            results
        };

        [
            ("django", "Django"),
            ("fastapi", "FastAPI"),
            ("flask", "Flask"),
        ]
        .into_iter()
        .filter(|(name, _)| content.contains(name))
        .fold(results, |acc, (_, framework)| acc.with_framework(framework))
    })
}

pub(super) fn detect_javascript(
    file_contents: &HashMap<PathBuf, String>,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let Some(paths) = signatures.by_name_lower.get("package.json") else {
        return results;
    };

    let pkg_mgr = if signatures.by_name_lower.contains_key("pnpm-lock.yaml") {
        "pnpm"
    } else if signatures.by_name_lower.contains_key("yarn.lock") {
        "Yarn"
    } else if signatures.by_name_lower.contains_key("bun.lockb")
        || signatures.by_name_lower.contains_key("bun.lock")
    {
        "Bun"
    } else {
        "npm"
    };

    let results = results.with_package_manager(pkg_mgr);

    paths.iter().fold(results, |results, path| {
        let content = match file_contents.get(path) {
            Some(c) => c.to_lowercase(),
            None => return results,
        };

        let results = [
            ("\"jest\"", "Jest"),
            ("\"vitest\"", "Vitest"),
            ("\"mocha\"", "Mocha"),
            ("\"cypress\"", "Cypress"),
            ("\"playwright\"", "Playwright"),
        ]
        .into_iter()
        .filter(|(pattern, _)| content.contains(pattern))
        .fold(results, |acc, (_, name)| acc.with_test_framework(name));

        [
            ("\"react\"", "React"),
            ("\"vue\"", "Vue"),
            ("\"angular\"", "Angular"),
            ("\"svelte\"", "Svelte"),
            ("\"next\"", "Next.js"),
            ("\"nuxt\"", "Nuxt"),
            ("\"express\"", "Express"),
            ("\"fastify\"", "Fastify"),
            ("\"nestjs\"", "NestJS"),
            ("\"gatsby\"", "Gatsby"),
        ]
        .into_iter()
        .filter(|(pattern, _)| content.contains(pattern))
        .fold(results, |acc, (_, name)| acc.with_framework(name))
    })
}

pub(super) fn detect_go(
    file_contents: &HashMap<PathBuf, String>,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let Some(paths) = signatures.by_name_lower.get("go.mod") else {
        return results;
    };

    let results = results
        .with_package_manager("Go Modules")
        .with_test_framework("go test");

    paths.iter().fold(results, |results, path| {
        let content = match file_contents.get(path) {
            Some(c) => c.to_lowercase(),
            None => return results,
        };

        [
            ("gin-gonic/gin", "Gin"),
            ("labstack/echo", "Echo"),
            ("gofiber/fiber", "Fiber"),
            ("gorilla/mux", "Gorilla"),
            ("go-chi/chi", "Chi"),
        ]
        .into_iter()
        .filter(|(pattern, _)| content.contains(pattern))
        .fold(results, |acc, (_, name)| acc.with_framework(name))
    })
}

pub(super) fn detect_ruby(
    file_contents: &HashMap<PathBuf, String>,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let Some(paths) = signatures.by_name_lower.get("gemfile") else {
        return results;
    };

    let results = results.with_package_manager("Bundler");

    paths.iter().fold(results, |results, path| {
        let content = match file_contents.get(path) {
            Some(c) => c.to_lowercase(),
            None => return results,
        };

        let results = if content.contains("rspec") {
            results.with_test_framework("RSpec")
        } else if content.contains("minitest") {
            results.with_test_framework("Minitest")
        } else {
            results
        };

        if content.contains("rails") {
            results.with_framework("Rails")
        } else if content.contains("sinatra") {
            results.with_framework("Sinatra")
        } else {
            results
        }
    })
}

pub(super) fn detect_java(
    file_contents: &HashMap<PathBuf, String>,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let results = if let Some(paths) = signatures.by_name_lower.get("pom.xml") {
        let results = results.with_package_manager("Maven");
        detect_java_frameworks(file_contents, paths, results)
    } else {
        results
    };

    let gradle_paths: Vec<_> = signatures
        .by_name_lower
        .get("build.gradle")
        .into_iter()
        .chain(signatures.by_name_lower.get("build.gradle.kts"))
        .flatten()
        .collect();

    if gradle_paths.is_empty() {
        return results;
    }

    let results = results.with_package_manager("Gradle");
    detect_java_frameworks(file_contents, &gradle_paths, results)
}

fn detect_java_frameworks(
    file_contents: &HashMap<PathBuf, String>,
    paths: &[impl AsRef<Path>],
    results: DetectionResults,
) -> DetectionResults {
    paths.iter().fold(results, |results, path| {
        let path_ref = path.as_ref();
        let content = match file_contents.get(path_ref) {
            Some(c) => c.to_lowercase(),
            None => return results,
        };

        let results = if content.contains("junit") {
            results.with_test_framework("JUnit")
        } else {
            results
        };

        if content.contains("spring") {
            results.with_framework("Spring")
        } else {
            results
        }
    })
}

pub(super) fn detect_php(
    file_contents: &HashMap<PathBuf, String>,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let Some(paths) = signatures.by_name_lower.get("composer.json") else {
        return results;
    };

    let results = results.with_package_manager("Composer");

    paths.iter().fold(results, |results, path| {
        let content = match file_contents.get(path) {
            Some(c) => c.to_lowercase(),
            None => return results,
        };

        let results = if content.contains("phpunit") {
            results.with_test_framework("PHPUnit")
        } else {
            results
        };

        [("laravel", "Laravel"), ("symfony", "Symfony")]
            .into_iter()
            .filter(|(pattern, _)| content.contains(pattern))
            .fold(results, |acc, (_, name)| acc.with_framework(name))
    })
}

pub(super) fn detect_dotnet(
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    if signatures
        .by_name_lower
        .keys()
        .any(|k| k.ends_with(".csproj") || k.ends_with(".fsproj"))
    {
        results.with_package_manager("NuGet")
    } else {
        results
    }
}

pub(super) fn detect_elixir(
    file_contents: &HashMap<PathBuf, String>,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let Some(paths) = signatures.by_name_lower.get("mix.exs") else {
        return results;
    };

    let results = results
        .with_package_manager("Mix")
        .with_test_framework("ExUnit");

    paths.iter().fold(results, |results, path| {
        let content = match file_contents.get(path) {
            Some(c) => c.to_lowercase(),
            None => return results,
        };

        if content.contains("phoenix") {
            results.with_framework("Phoenix")
        } else {
            results
        }
    })
}

pub(super) fn detect_dart(
    file_contents: &HashMap<PathBuf, String>,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let Some(paths) = signatures.by_name_lower.get("pubspec.yaml") else {
        return results;
    };

    let results = results.with_package_manager("Pub");

    paths.iter().fold(results, |results, path| {
        let content = match file_contents.get(path) {
            Some(c) => c.to_lowercase(),
            None => return results,
        };

        if content.contains("flutter:") || content.contains("flutter_test") {
            results
                .with_framework("Flutter")
                .with_test_framework("Flutter Test")
        } else {
            results
        }
    })
}
