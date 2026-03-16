// Risk and verification parsing (parse_risks_mitigations, parse_risk_pair, parse_verification_strategy, parse_single_verification)

/// Parse the ralph-risks-mitigations section
///
/// The `original_tag` parameter is used for fuzzy matching - when the opening tag was misspelled,
/// this allows the parser to accept either the canonical closing tag OR the original misspelled one.
fn parse_risks_mitigations(
    reader: &mut Reader<&[u8]>,
    original_tag: &[u8],
) -> Result<Vec<RiskPair>, XsdValidationError> {
    let canonical_tag = b"ralph-risks-mitigations";
    let mut risk_pairs = Vec::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) if e.name().as_ref() == b"risk-pair" => {
                let attrs = get_attributes(&e);
                let severity = attrs.get("severity").and_then(|s| Severity::from_str(s));
                let pair = parse_risk_pair(reader, severity)?;
                risk_pairs.push(pair);
            }
            Ok(Event::End(e)) => {
                // Accept either canonical tag OR original (misspelled) tag
                if e.name().as_ref() == canonical_tag || e.name().as_ref() == original_tag {
                    break;
                }
            }
            Ok(Event::Eof) => break,
            Ok(_) => {}
            Err(e) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "ralph-risks-mitigations".to_string(),
                    expected: "valid XML".to_string(),
                    found: format!("parse error: {e}"),
                    suggestion: "Check XML syntax".to_string(),
                    example: None,
                });
            }
        }
        buf.clear();
    }

    if risk_pairs.is_empty() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "ralph-risks-mitigations".to_string(),
            expected: "at least one <risk-pair> element".to_string(),
            found: "no risk-pairs".to_string(),
            suggestion:
                "Add <risk-pair severity=\"medium\"><risk>...</risk><mitigation>...</mitigation></risk-pair>"
                    .to_string(),
                    example: None,
        });
    }

    Ok(risk_pairs)
}

/// Parse a single risk-pair
fn parse_risk_pair(
    reader: &mut Reader<&[u8]>,
    severity: Option<Severity>,
) -> Result<RiskPair, XsdValidationError> {
    let mut risk = None;
    let mut mitigation = None;
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => match e.name().as_ref() {
                b"risk" => {
                    risk = Some(read_text_until_end(reader, b"risk")?);
                }
                b"mitigation" => {
                    mitigation = Some(read_text_until_end(reader, b"mitigation")?);
                }
                _ => {
                    let _ = skip_to_end(reader, e.name().as_ref());
                }
            },
            Ok(Event::End(e)) if e.name().as_ref() == b"risk-pair" => break,
            Ok(Event::Eof) | Err(_) => break,
            Ok(_) => {}
        }
        buf.clear();
    }

    let risk = risk.ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "risk-pair/risk".to_string(),
        expected: "<risk> element".to_string(),
        found: "no <risk> found".to_string(),
        suggestion: "Add <risk>Risk description</risk>".to_string(),
        example: None,
    })?;

    let mitigation = mitigation.ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "risk-pair/mitigation".to_string(),
        expected: "<mitigation> element".to_string(),
        found: "no <mitigation> found".to_string(),
        suggestion: "Add <mitigation>How to mitigate</mitigation>".to_string(),
        example: None,
    })?;

    Ok(RiskPair {
        severity,
        risk,
        mitigation,
    })
}

/// Parse the ralph-verification-strategy section
///
/// The `original_tag` parameter is used for fuzzy matching - when the opening tag was misspelled,
/// this allows the parser to accept either the canonical closing tag OR the original misspelled one.
fn parse_verification_strategy(
    reader: &mut Reader<&[u8]>,
    original_tag: &[u8],
) -> Result<Vec<Verification>, XsdValidationError> {
    let canonical_tag = b"ralph-verification-strategy";
    let mut verifications = Vec::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) if e.name().as_ref() == b"verification" => {
                verifications.push(parse_single_verification(reader)?);
            }
            Ok(Event::End(e)) => {
                // Accept either canonical tag OR original (misspelled) tag
                if e.name().as_ref() == canonical_tag || e.name().as_ref() == original_tag {
                    break;
                }
            }
            Ok(Event::Eof) => break,
            Ok(_) => {}
            Err(e) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "ralph-verification-strategy".to_string(),
                    expected: "valid XML".to_string(),
                    found: format!("parse error: {e}"),
                    suggestion: "Check XML syntax".to_string(),
                    example: None,
                });
            }
        }
        buf.clear();
    }

    if verifications.is_empty() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "ralph-verification-strategy".to_string(),
            expected: "at least one <verification> element".to_string(),
            found: "no verifications".to_string(),
            suggestion:
                "Add <verification><method>...</method><expected-outcome>...</expected-outcome></verification>"
                    .to_string(),
            example: None,
        });
    }

    Ok(verifications)
}

/// Parse a single verification element
fn parse_single_verification(
    reader: &mut Reader<&[u8]>,
) -> Result<Verification, XsdValidationError> {
    let mut method = None;
    let mut expected_outcome = None;
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => match e.name().as_ref() {
                b"method" => {
                    method = Some(read_text_until_end(reader, b"method")?);
                }
                b"expected-outcome" => {
                    expected_outcome = Some(read_text_until_end(reader, b"expected-outcome")?);
                }
                _ => {
                    let _ = skip_to_end(reader, e.name().as_ref());
                }
            },
            Ok(Event::End(e)) if e.name().as_ref() == b"verification" => break,
            Ok(Event::Eof) | Err(_) => break,
            Ok(_) => {}
        }
        buf.clear();
    }

    let method = method.ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "verification/method".to_string(),
        expected: "<method> element".to_string(),
        found: "no <method> found".to_string(),
        suggestion: "Add <method>How to verify</method>".to_string(),
        example: None,
    })?;

    let expected_outcome = expected_outcome.ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "verification/expected-outcome".to_string(),
        expected: "<expected-outcome> element".to_string(),
        found: "no <expected-outcome> found".to_string(),
        suggestion: "Add <expected-outcome>What success looks like</expected-outcome>".to_string(),
        example: None,
    })?;

    Ok(Verification {
        method,
        expected_outcome,
    })
}
