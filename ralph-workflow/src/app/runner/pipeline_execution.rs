// Pipeline execution and phase context creation.
//
// This module contains:
// - run_pipeline: Main pipeline execution via reducer event loop
// - RunWithHandlersParams: Parameters for test entry points
// - Phase context creation helpers
// - Pipeline preparation and finalization

// Include sub-modules
include!("pipeline_execution/discovery.rs");
include!("pipeline_execution/entry_points/main.rs");
include!("pipeline_execution/entry_points/io_testing.rs");
include!("pipeline_execution/pipeline/execution.rs");
include!("pipeline_execution/pipeline/io_testing.rs");
include!("pipeline_execution/helpers.rs");
