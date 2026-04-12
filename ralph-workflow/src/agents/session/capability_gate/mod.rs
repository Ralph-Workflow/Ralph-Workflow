//! Capability gate for RFC-009 Phase 2 policy enforcement.
//!
//! This module provides the core policy engine for Phase 2 enforcement:
//! - Pure function mapping from Effect variants to required Capabilities
//! - Session capability checking against required capabilities
//! - PolicyOutcome generation for audit trail
//!
//! # Design Principles
//!
//! - **Pure function**: No I/O, deterministic results based only on session and effect
//! - **Exhaustive coverage**: Every Effect variant has a capability mapping
//! - **Clear denials**: Denied outcomes include descriptive reasons
//! - **Audit-friendly**: Returns all information needed for audit trail records

mod effect_map;
mod policy;
#[cfg(test)]
mod tests;

pub use effect_map::{effect_kind, effect_name, required_capabilities, EffectKind};
pub use policy::{check_effect_capability, is_ralph_internal_effect};
