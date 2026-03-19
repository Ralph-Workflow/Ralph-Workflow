// Tests for suppressing spurious "error_during_execution" result events.

/// Test for suppressing duplicate error Result events after success Result event.
///
/// This test verifies the fix for the GLM/ccs-glm bug where the agent emits both:
/// 1. A "success" Result event when completing its work
/// 2. An "`error_during_execution`" Result event when exiting with code 1
///
/// The fix suppresses the spurious error event to avoid confusing duplicate output.
#[cfg(test)]
#[test]
fn test_suppress_duplicate_error_result_after_success() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer);

    // Simulate the GLM/ccs-glm scenario:
    // 1. Success Result event (agent completed successfully)
    // 2. error_during_execution Result event (GLM exited with code 1)
    let input_lines = [
        // Success result
        r#"{"type":"result","subtype":"success","duration_ms":600000,"num_turns":22,"total_cost_usd":0.9883}"#.to_string(),
        // Spurious error result (should be suppressed)
        r#"{"type":"result","subtype":"error_during_execution","duration_ms":0,"error":null}"#.to_string(),
    ];

    let input = input_lines.join("\n");
    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // Should contain "Completed" from the success event
    assert!(
        output.contains("Completed"),
        "Should contain 'Completed' from success event. Output: {output:?}"
    );

    // Should NOT contain "error_during_execution" (it's suppressed)
    assert!(
        !output.contains("error_during_execution"),
        "Should NOT contain 'error_during_execution' - it should be suppressed. Output: {output:?}"
    );

    // Should only have ONE result line, not two
    let result_count = output.matches("[Claude]").count();
    assert_eq!(
        result_count, 1,
        "Should have exactly 1 result line (success only), not 2. Found {result_count}. Output: {output:?}"
    );
}

/// Test for suppressing error Result events that arrive BEFORE success Result event.
///
/// This test verifies the fix works when events arrive in reverse order:
/// 1. `error_during_execution` Result event (arrives first)
/// 2. success Result event (arrives second)
///
/// The enhanced suppression logic identifies spurious GLM error events by their
/// characteristics (`duration_ms` < 100, error field is null/empty) and suppresses
/// them regardless of event order.
#[cfg(test)]
#[test]
fn test_suppress_error_result_that_arrives_before_success() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer);

    // Simulate the REVERSE order scenario:
    // 1. error_during_execution Result event (arrives first)
    // 2. success Result event (arrives second)
    let input_lines = [
        // Spurious error result (arrives FIRST - should be suppressed by new logic)
        r#"{"type":"result","subtype":"error_during_execution","duration_ms":0,"error":null}"#.to_string(),
        // Success result (arrives SECOND)
        r#"{"type":"result","subtype":"success","duration_ms":600000,"num_turns":22,"total_cost_usd":0.9883}"#.to_string(),
    ];

    let input = input_lines.join("\n");
    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // Should contain "Completed" from the success event
    assert!(
        output.contains("Completed"),
        "Should contain 'Completed' from success event. Output: {output:?}"
    );

    // Should NOT contain "error_during_execution" (it's suppressed)
    assert!(
        !output.contains("error_during_execution"),
        "Should NOT contain 'error_during_execution' - it should be suppressed. Output: {output:?}"
    );

    // Should only have ONE result line, not two
    let result_count = output.matches("[Claude]").count();
    assert_eq!(
        result_count, 1,
        "Should have exactly 1 result line (success only), not 2. Found {result_count}. Output: {output:?}"
    );
}

/// Test for NOT suppressing error Result events that have actual error messages.
///
/// This test verifies that error events with an 'errors' array containing actual
/// error messages are NOT suppressed, because these represent real error conditions
/// that the user should see.
///
/// This is the opposite of the spurious GLM error suppression - when there are
/// actual error messages, we should display them.
#[cfg(test)]
#[test]
fn test_do_not_suppress_error_with_actual_errors_array() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer);

    // Simulate the scenario where an error event has actual error messages:
    // 1. Success Result event (agent completed successfully)
    // 2. Error Result event with 'errors' array containing actual error messages (should NOT be suppressed)
    let input_lines = [
        // Success result
        r#"{"type":"result","subtype":"success","duration_ms":600000,"num_turns":22,"total_cost_usd":0.9883}"#.to_string(),
        // Error result with actual errors in 'errors' array (should NOT be suppressed)
        r#"{"type":"result","subtype":"error_during_execution","duration_ms":0,"error":null,"errors":["only prompt commands are supported in streaming mode","Error: Lock acquisition failed"]}"#.to_string(),
    ];

    let input = input_lines.join("\n");
    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // Should contain "Completed" from the success event
    assert!(
        output.contains("Completed"),
        "Should contain 'Completed' from success event. Output: {output:?}"
    );

    // Should ALSO contain "error_during_execution" because the error has actual messages
    assert!(
        output.contains("error_during_execution"),
        "Should contain 'error_during_execution' - error events with actual messages should NOT be suppressed. Output: {output:?}"
    );

    // Should have TWO result lines (success + error)
    let result_count = output.matches("[Claude]").count();
    assert_eq!(
        result_count, 2,
        "Should have 2 result lines (success + error with actual messages). Found {result_count}. Output: {output:?}"
    );
}

/// Test for suppressing error Result events with empty 'errors' array.
///
/// This test verifies that error events with an 'errors' array that's empty
/// or contains only empty strings ARE suppressed, because they don't represent
/// a real error condition.
#[cfg(test)]
#[test]
fn test_suppress_error_with_empty_errors_array() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer);

    // Simulate the scenario where an error event has an empty 'errors' array:
    // 1. Success Result event (agent completed successfully)
    // 2. Error Result event with empty 'errors' array (should be suppressed)
    let input_lines = [
        // Success result
        r#"{"type":"result","subtype":"success","duration_ms":600000,"num_turns":22,"total_cost_usd":0.9883}"#.to_string(),
        // Error result with empty 'errors' array (should be suppressed)
        r#"{"type":"result","subtype":"error_during_execution","duration_ms":0,"error":null,"errors":[]}"#.to_string(),
    ];

    let input = input_lines.join("\n");
    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // Should contain "Completed" from the success event
    assert!(
        output.contains("Completed"),
        "Should contain 'Completed' from success event. Output: {output:?}"
    );

    // Should NOT contain "error_during_execution" (it's suppressed)
    assert!(
        !output.contains("error_during_execution"),
        "Should NOT contain 'error_during_execution' - error events with empty 'errors' array should be suppressed. Output: {output:?}"
    );

    // Should only have ONE result line, not two
    let result_count = output.matches("[Claude]").count();
    assert_eq!(
        result_count, 1,
        "Should have exactly 1 result line (success only), not 2. Found {result_count}. Output: {output:?}"
    );
}
