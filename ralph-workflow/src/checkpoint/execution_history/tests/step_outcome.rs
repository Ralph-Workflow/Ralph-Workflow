#[test]
fn test_step_outcome_success_with_empty_files_uses_none() {
    // Empty files_modified should use None instead of empty Vec
    let outcome = StepOutcome::success(None, vec![]);

    match outcome {
        StepOutcome::Success { files_modified, .. } => {
            assert!(files_modified.is_none(), "Empty files should be None");
        }
        _ => panic!("Expected Success variant"),
    }
}

#[test]
fn test_step_outcome_success_with_files_uses_boxed_slice() {
    // Non-empty files_modified should use Box<[String]>
    let files = vec!["file1.txt".to_string(), "file2.txt".to_string()];
    let outcome = StepOutcome::success(None, files);

    match outcome {
        StepOutcome::Success { files_modified, .. } => {
            let files = files_modified.expect("Files should be present");
            assert_eq!(files.len(), 2);
            assert_eq!(files[0], "file1.txt");
            assert_eq!(files[1], "file2.txt");
        }
        _ => panic!("Expected Success variant"),
    }
}

#[test]
fn test_step_outcome_failure_with_no_signals_uses_none() {
    // Failure without signals should use None
    let outcome = StepOutcome::failure("error message".to_string(), true);

    match outcome {
        StepOutcome::Failure { signals, .. } => {
            assert!(signals.is_none(), "Empty signals should be None");
        }
        _ => panic!("Expected Failure variant"),
    }
}

#[test]
fn test_step_outcome_uses_box_str_for_strings() {
    // Verify that Box<str> is used for string fields
    let outcome = StepOutcome::failure("test error".to_string(), false);

    match outcome {
        StepOutcome::Failure { error, .. } => {
            assert_eq!(&*error, "test error");
            // Box<str> uses exactly the needed space
            assert_eq!(error.len(), "test error".len());
        }
        _ => panic!("Expected Failure variant"),
    }
}

#[test]
fn test_step_outcome_constructors_preserve_large_string_content() {
    // StepOutcome constructors accept owned String inputs and store them as Box<str>.
    // Allocation reuse is an optimization and is not guaranteed by Rust toolchains or
    // allocators, so this test asserts only semantic correctness.

    // Large strings avoid any small-string/allocator-size quirks.
    let make_string = |byte: u8| -> String {
        let bytes = vec![byte; 1024];
        String::from_utf8(bytes).expect("valid utf8")
    };

    // failure()
    let s = make_string(b'e');
    let s_expected = s.clone();
    let outcome = StepOutcome::failure(s, true);
    match outcome {
        StepOutcome::Failure { error, .. } => {
            assert_eq!(&*error, s_expected);
            assert_eq!(error.len(), s_expected.len());
        }
        _ => panic!("Expected Failure variant"),
    }

    // partial()
    let completed = make_string(b'c');
    let completed_expected = completed.clone();
    let remaining = make_string(b'r');
    let remaining_expected = remaining.clone();
    let outcome = StepOutcome::partial(completed, remaining);
    match outcome {
        StepOutcome::Partial {
            completed,
            remaining,
            ..
        } => {
            assert_eq!(&*completed, completed_expected);
            assert_eq!(completed.len(), completed_expected.len());
            assert_eq!(&*remaining, remaining_expected);
            assert_eq!(remaining.len(), remaining_expected.len());
        }
        _ => panic!("Expected Partial variant"),
    }

    // skipped()
    let reason = make_string(b's');
    let reason_expected = reason.clone();
    let outcome = StepOutcome::skipped(reason);
    match outcome {
        StepOutcome::Skipped { reason } => {
            assert_eq!(&*reason, reason_expected);
            assert_eq!(reason.len(), reason_expected.len());
        }
        _ => panic!("Expected Skipped variant"),
    }

    // success(Some(output), empty files)
    let output = make_string(b'o');
    let output_expected = output.clone();
    let outcome = StepOutcome::success(Some(output), vec![]);
    match outcome {
        StepOutcome::Success {
            output: Some(output),
            ..
        } => {
            assert_eq!(&*output, output_expected);
            assert_eq!(output.len(), output_expected.len());
        }
        _ => panic!("Expected Success variant with output"),
    }
}

#[test]
fn test_step_outcome_partial_uses_box_str() {
    let outcome = StepOutcome::partial("done".to_string(), "remaining".to_string());

    match outcome {
        StepOutcome::Partial {
            completed,
            remaining,
            ..
        } => {
            assert_eq!(&*completed, "done");
            assert_eq!(&*remaining, "remaining");
            // Verify Box<str> efficiency
            assert_eq!(completed.len(), "done".len());
            assert_eq!(remaining.len(), "remaining".len());
        }
        _ => panic!("Expected Partial variant"),
    }
}

#[test]
fn test_step_outcome_skipped_uses_box_str() {
    let outcome = StepOutcome::skipped("already done".to_string());

    match outcome {
        StepOutcome::Skipped { reason } => {
            assert_eq!(&*reason, "already done");
            assert_eq!(reason.len(), "already done".len());
        }
        _ => panic!("Expected Skipped variant"),
    }
}

#[test]
fn test_step_outcome_serialization_with_empty_collections() {
    // Test that empty collections serialize correctly
    let outcome = StepOutcome::success(None, vec![]);
    let json = serde_json::to_string(&outcome).unwrap();

    // Deserialize back
    let deserialized: StepOutcome = serde_json::from_str(&json).unwrap();
    assert_eq!(outcome, deserialized);

    // Verify None is preserved
    match deserialized {
        StepOutcome::Success { files_modified, .. } => {
            assert!(files_modified.is_none());
        }
        _ => panic!("Expected Success variant"),
    }
}

#[test]
fn test_step_outcome_backward_compatibility_with_empty_vec() {
    // Old checkpoints may have empty Vec serialized as []
    let old_json = r#"{"Success":{"output":null,"files_modified":[],"exit_code":0}}"#;
    let outcome: StepOutcome = serde_json::from_str(old_json).unwrap();

    // Canonical form: treat empty arrays as None to preserve the
    // None-for-empty optimization when resaving a legacy checkpoint.
    match outcome {
        StepOutcome::Success {
            ref files_modified, ..
        } => {
            assert!(
                files_modified.is_none(),
                "expected empty legacy array to deserialize as None"
            );
        }
        _ => panic!("Expected Success variant"),
    }

    // Round-trip should preserve the on-disk shape for compatibility.
    let json = serde_json::to_string(&outcome).unwrap();
    let value: serde_json::Value = serde_json::from_str(&json).unwrap();
    assert_eq!(
        value.get("Success").and_then(|v| v.get("files_modified")),
        Some(&serde_json::Value::Array(vec![])),
        "expected serialization to use [] (not null) for compatibility"
    );
}

#[test]
fn test_step_outcome_failure_signals_serialize_as_empty_array_when_none() {
    let outcome = StepOutcome::failure("boom".to_string(), true);
    let json = serde_json::to_string(&outcome).unwrap();
    let value: serde_json::Value = serde_json::from_str(&json).unwrap();
    assert_eq!(
        value.get("Failure").and_then(|v| v.get("signals")),
        Some(&serde_json::Value::Array(vec![])),
        "expected serialization to use [] (not null) for signals"
    );
}

#[test]
fn test_modified_files_detail_legacy_empty_arrays_deserialize_to_none() {
    let legacy = r#"{"added":[],"modified":[],"deleted":[]}"#;
    let detail: ModifiedFilesDetail = serde_json::from_str(legacy).unwrap();
    assert!(detail.added.is_none());
    assert!(detail.modified.is_none());
    assert!(detail.deleted.is_none());

    // Round-trip should omit empty fields.
    let json = serde_json::to_string(&detail).unwrap();
    assert_eq!(json, "{}", "expected empty fields to be omitted");
}

#[test]
fn test_step_outcome_memory_efficiency_vs_vec() {
    // Demonstrate memory efficiency of Box<str> and Option<Box<[T]>>
    // Vec<T> over-allocates capacity, Box<[T]> uses exact size

    let outcome = StepOutcome::success(
        Some("output".to_string()),
        vec!["file1.txt".to_string(), "file2.txt".to_string()],
    );

    match outcome {
        StepOutcome::Success {
            output,
            files_modified,
            ..
        } => {
            // Box<str> uses exact size
            let output_str = output.expect("Output should be present");
            assert_eq!(output_str.len(), "output".len());

            // Box<[String]> uses exact size (no excess capacity)
            let files = files_modified.expect("Files should be present");
            assert_eq!(files.len(), 2);
        }
        _ => panic!("Expected Success variant"),
    }
}
