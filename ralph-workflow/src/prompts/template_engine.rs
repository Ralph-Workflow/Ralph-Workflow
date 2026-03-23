//! Template engine for rendering prompt templates.
//!
//! This module provides a template variable replacement system for prompt templates
//! with support for variables, partials, comments, conditionals, loops, and defaults.
//!
//! The imperative parsing and rendering code lives in the runtime/ boundary module
//! to satisfy the functional programming lints.

pub type Template = crate::prompts::runtime::Template;
pub type TemplateError = crate::prompts::template_registry::TemplateError;
