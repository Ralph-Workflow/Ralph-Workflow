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
    let risk_pairs =
        parse_risk_pairs_events(reader, original_tag, canonical_tag, Vec::new())?;

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

fn parse_risk_pairs_events(
    reader: &mut Reader<&[u8]>,
    original_tag: &[u8],
    canonical_tag: &[u8],
    acc: Vec<RiskPair>,
) -> Result<Vec<RiskPair>, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"risk-pair" => {
            let attrs = get_attributes(&e);
            let severity = attrs.get("severity").and_then(|s| Severity::from_str(s));
            let pair = parse_risk_pair_events(reader, severity, None, None)?;
            parse_risk_pairs_events(
                reader,
                original_tag,
                canonical_tag,
                acc.into_iter().chain(std::iter::once(pair)).collect(),
            )
        }
        Ok(Event::End(e))
            if e.name().as_ref() == canonical_tag || e.name().as_ref() == original_tag =>
        {
            Ok(acc)
        }
        Ok(Event::Eof) => Ok(acc),
        Ok(_) => parse_risk_pairs_events(reader, original_tag, canonical_tag, acc),
        Err(e) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: "ralph-risks-mitigations".to_string(),
            expected: "valid XML".to_string(),
            found: format!("parse error: {e}"),
            suggestion: "Check XML syntax".to_string(),
            example: None,
        }),
    }
}

/// Parse a single risk-pair using recursive accumulation.
fn parse_risk_pair_events(
    reader: &mut Reader<&[u8]>,
    severity: Option<Severity>,
    risk: Option<String>,
    mitigation: Option<String>,
) -> Result<RiskPair, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) => match e.name().as_ref() {
            b"risk" => {
                let text = read_text_until_end(reader, b"risk")?;
                parse_risk_pair_events(reader, severity, Some(text), mitigation)
            }
            b"mitigation" => {
                let text = read_text_until_end(reader, b"mitigation")?;
                parse_risk_pair_events(reader, severity, risk, Some(text))
            }
            _ => {
                let _ = skip_to_end(reader, e.name().as_ref());
                parse_risk_pair_events(reader, severity, risk, mitigation)
            }
        },
        Ok(Event::End(e)) if e.name().as_ref() == b"risk-pair" => {
            finish_risk_pair(severity, risk, mitigation)
        }
        Ok(Event::Eof) | Err(_) => finish_risk_pair(severity, risk, mitigation),
        Ok(_) => parse_risk_pair_events(reader, severity, risk, mitigation),
    }
}

fn finish_risk_pair(
    severity: Option<Severity>,
    risk: Option<String>,
    mitigation: Option<String>,
) -> Result<RiskPair, XsdValidationError> {
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
    let verifications =
        parse_verifications_events(reader, original_tag, canonical_tag, Vec::new())?;

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

fn parse_verifications_events(
    reader: &mut Reader<&[u8]>,
    original_tag: &[u8],
    canonical_tag: &[u8],
    acc: Vec<Verification>,
) -> Result<Vec<Verification>, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"verification" => {
            let v = parse_single_verification_events(reader, None, None)?;
            parse_verifications_events(
                reader,
                original_tag,
                canonical_tag,
                acc.into_iter().chain(std::iter::once(v)).collect(),
            )
        }
        Ok(Event::End(e))
            if e.name().as_ref() == canonical_tag || e.name().as_ref() == original_tag =>
        {
            Ok(acc)
        }
        Ok(Event::Eof) => Ok(acc),
        Ok(_) => parse_verifications_events(reader, original_tag, canonical_tag, acc),
        Err(e) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: "ralph-verification-strategy".to_string(),
            expected: "valid XML".to_string(),
            found: format!("parse error: {e}"),
            suggestion: "Check XML syntax".to_string(),
            example: None,
        }),
    }
}

/// Parse a single verification element using recursive accumulation.
fn parse_single_verification_events(
    reader: &mut Reader<&[u8]>,
    method: Option<String>,
    expected_outcome: Option<String>,
) -> Result<Verification, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) => match e.name().as_ref() {
            b"method" => {
                let text = read_text_until_end(reader, b"method")?;
                parse_single_verification_events(reader, Some(text), expected_outcome)
            }
            b"expected-outcome" => {
                let text = read_text_until_end(reader, b"expected-outcome")?;
                parse_single_verification_events(reader, method, Some(text))
            }
            _ => {
                let _ = skip_to_end(reader, e.name().as_ref());
                parse_single_verification_events(reader, method, expected_outcome)
            }
        },
        Ok(Event::End(e)) if e.name().as_ref() == b"verification" => {
            finish_verification(method, expected_outcome)
        }
        Ok(Event::Eof) | Err(_) => finish_verification(method, expected_outcome),
        Ok(_) => parse_single_verification_events(reader, method, expected_outcome),
    }
}

fn finish_verification(
    method: Option<String>,
    expected_outcome: Option<String>,
) -> Result<Verification, XsdValidationError> {
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
