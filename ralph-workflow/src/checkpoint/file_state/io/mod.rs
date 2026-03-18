// Boundary module for checkpoint file state I/O operations.
// This module contains filesystem access and process execution that requires mutation.

pub mod capture;
pub mod validation;

pub use capture::FileSystemState;
pub use validation::validate_file_state;
