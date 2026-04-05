// Backoff computation helpers.
// These mirror the semantics in `crate::agents::fallback::FallbackConfig::calculate_backoff`
// but live in reducer state so orchestration can derive BackoffWait effects purely.

const IEEE_754_EXP_BIAS: i32 = 1023;
const IEEE_754_EXP_MASK: u64 = 0x7FF;
const IEEE_754_MANTISSA_MASK: u64 = 0x000F_FFFF_FFFF_FFFF;
const IEEE_754_IMPLICIT_ONE: u64 = 1u64 << 52;

pub(super) fn f64_to_u64_via_bits(value: f64) -> u64 {
    if !value.is_finite() || value < 0.0 {
        return 0;
    }
    let bits = value.to_bits();
    let exp_biased = ((bits >> 52) & IEEE_754_EXP_MASK) as i32;
    let mantissa = bits & IEEE_754_MANTISSA_MASK;
    if exp_biased == 0 {
        return 0;
    }
    let exp = exp_biased - IEEE_754_EXP_BIAS;
    if exp < 0 {
        return 0;
    }
    let full_mantissa = mantissa | IEEE_754_IMPLICIT_ONE;
    let shift = 52i32 - exp;
    if shift <= 0 {
        u64::MAX
    } else if shift < 64 {
        full_mantissa >> shift
    } else {
        0
    }
}

pub(super) fn multiplier_hundredths(backoff_multiplier: f64) -> u64 {
    const EPSILON: f64 = 0.0001;
    let m = backoff_multiplier;
    if (m - 1.0).abs() < EPSILON {
        return 100;
    } else if (m - 1.5).abs() < EPSILON {
        return 150;
    } else if (m - 2.0).abs() < EPSILON {
        return 200;
    } else if (m - 2.5).abs() < EPSILON {
        return 250;
    } else if (m - 3.0).abs() < EPSILON {
        return 300;
    } else if (m - 4.0).abs() < EPSILON {
        return 400;
    } else if (m - 5.0).abs() < EPSILON {
        return 500;
    } else if (m - 10.0).abs() < EPSILON {
        return 1000;
    }

    let clamped = m.clamp(0.0, 1000.0);
    let multiplied = clamped * 100.0;
    let rounded = multiplied.round();
    f64_to_u64_via_bits(rounded)
}

pub(super) fn calculate_backoff_delay_ms(
    retry_delay_ms: u64,
    backoff_multiplier: f64,
    max_backoff_ms: u64,
    cycle: u32,
) -> u64 {
    let mult_hundredths = multiplier_hundredths(backoff_multiplier);
    // multiplier == 1.0 means no growth: result is always the base delay (capped at max).
    if mult_hundredths == 100 {
        return retry_delay_ms.min(max_backoff_ms);
    }
    // Early-exit to avoid O(n) loops for large cycle values like u32::MAX.
    // Exits as soon as the value hits the max cap OR reaches a stable fixed point
    // (value stops changing, which happens for shrinking multipliers once zero is reached).
    let max_hundredths = max_backoff_ms.saturating_mul(100);
    let delay_hundredths = (0..cycle)
        .try_fold(retry_delay_ms.saturating_mul(100), |acc, _| {
            if acc >= max_hundredths {
                return Err(acc);
            }
            let next = acc.saturating_mul(mult_hundredths).saturating_div(100);
            if next == acc {
                Err(acc) // stable fixed point: no further change possible
            } else {
                Ok(next)
            }
        })
        .unwrap_or_else(|v| v);
    delay_hundredths.div_euclid(100).min(max_backoff_ms)
}

#[cfg(test)]
mod backoff_overflow_safety_tests {
    use super::*;

    #[test]
    fn test_cycle_zero_returns_base_delay() {
        // cycle=0 means "no growth": return retry_delay_ms unchanged.
        assert_eq!(calculate_backoff_delay_ms(1000, 2.0, 60_000, 0), 1000);
    }

    #[test]
    fn test_cycle_one_doubles_base_delay() {
        // cycle=1 with multiplier=2.0: 1000 * 2 = 2000 (below max).
        assert_eq!(calculate_backoff_delay_ms(1000, 2.0, 60_000, 1), 2000);
    }

    #[test]
    fn test_cycle_two_quadruples_base_delay() {
        assert_eq!(calculate_backoff_delay_ms(1000, 2.0, 60_000, 2), 4000);
    }

    #[test]
    fn test_delay_is_clamped_to_max_backoff() {
        // cycle=10 with multiplier=2.0 would produce 1000 * 2^10 = 1_024_000,
        // which must be clamped to max_backoff_ms=60_000.
        assert_eq!(calculate_backoff_delay_ms(1000, 2.0, 60_000, 10), 60_000);
    }

    #[test]
    fn test_cycle_u32_max_with_multiplier_2_saturates_to_max_backoff() {
        // u32::MAX cycles: saturating arithmetic prevents overflow and the final
        // .min(max_backoff_ms) clamps the result.
        let result = calculate_backoff_delay_ms(1000, 2.0, 60_000, u32::MAX);
        assert_eq!(
            result, 60_000,
            "u32::MAX cycles must saturate at max_backoff_ms via saturating arithmetic"
        );
    }

    #[test]
    fn test_large_multiplier_with_many_cycles_saturates_to_max_backoff() {
        // cycle=100, multiplier=10.0: intermediate values overflow u64 but saturating_mul
        // ensures the result is always within [0, max_backoff_ms].
        let result = calculate_backoff_delay_ms(1000, 10.0, 60_000, 100);
        assert_eq!(
            result, 60_000,
            "large multiplier with many cycles must saturate at max_backoff_ms"
        );
    }

    #[test]
    fn test_multiplier_one_no_growth_returns_base_regardless_of_cycle() {
        // multiplier=1.0 means no exponential growth; delay stays at base (capped at max).
        assert_eq!(calculate_backoff_delay_ms(1000, 1.0, 60_000, 0), 1000);
        assert_eq!(calculate_backoff_delay_ms(1000, 1.0, 60_000, 5), 1000);
        assert_eq!(calculate_backoff_delay_ms(1000, 1.0, 60_000, 100), 1000);
    }

    #[test]
    fn test_max_backoff_zero_always_returns_zero() {
        // When max_backoff_ms=0, the .min(0) clamp ensures the result is always 0.
        assert_eq!(calculate_backoff_delay_ms(1000, 2.0, 0, 0), 0);
        assert_eq!(calculate_backoff_delay_ms(1000, 2.0, 0, 5), 0);
        assert_eq!(calculate_backoff_delay_ms(1000, 2.0, 0, u32::MAX), 0);
    }

    #[test]
    fn test_f64_to_u64_nan_returns_zero() {
        assert_eq!(f64_to_u64_via_bits(f64::NAN), 0);
    }

    #[test]
    fn test_f64_to_u64_positive_infinity_returns_zero() {
        // Positive infinity is non-finite; the !is_finite() guard at the top of
        // f64_to_u64_via_bits returns 0 before any bit manipulation occurs.
        assert_eq!(f64_to_u64_via_bits(f64::INFINITY), 0);
    }

    #[test]
    fn test_f64_to_u64_negative_infinity_returns_zero() {
        assert_eq!(f64_to_u64_via_bits(f64::NEG_INFINITY), 0);
    }

    #[test]
    fn test_f64_to_u64_negative_value_returns_zero() {
        assert_eq!(f64_to_u64_via_bits(-1.0), 0);
        assert_eq!(f64_to_u64_via_bits(-100.0), 0);
    }

    #[test]
    fn test_multiplier_hundredths_non_exact_value_returns_reasonable_approximation() {
        // multiplier=1.23 is not in the lookup table; falls through to f64-based path.
        // 1.23 * 100 = 123.0 → rounded = 123 → f64_to_u64 = 123.
        let result = multiplier_hundredths(1.23);
        assert_eq!(
            result, 123,
            "1.23 multiplier must convert to 123 hundredths"
        );
    }

    #[test]
    fn test_multiplier_hundredths_common_values_use_exact_lookup() {
        // Common values must use the fast lookup path, not the f64 path.
        assert_eq!(multiplier_hundredths(1.0), 100);
        assert_eq!(multiplier_hundredths(1.5), 150);
        assert_eq!(multiplier_hundredths(2.0), 200);
        assert_eq!(multiplier_hundredths(2.5), 250);
        assert_eq!(multiplier_hundredths(3.0), 300);
        assert_eq!(multiplier_hundredths(4.0), 400);
        assert_eq!(multiplier_hundredths(5.0), 500);
        assert_eq!(multiplier_hundredths(10.0), 1000);
    }

    #[test]
    fn test_backoff_result_always_within_valid_range_invariant() {
        // Invariant: for any inputs, 0 <= result <= max_backoff_ms.
        // Verified across a broad range of cycle values, multipliers, and extreme edges.
        let cases: &[(u64, f64, u64, u32)] = &[
            // (base_ms, multiplier, max_ms, cycle)
            (1000, 2.0, 60_000, 0),
            (1000, 2.0, 60_000, 1),
            (1000, 2.0, 60_000, 10),
            (1000, 2.0, 60_000, 100),
            (1000, 2.0, 60_000, u32::MAX),
            (0, 2.0, 60_000, 5),              // base=0: result must be 0
            (1000, 1.0, 60_000, u32::MAX),    // multiplier=1: base always ≤ max
            (1000, 10.0, 60_000, 20),         // large multiplier: must clamp
            (u64::MAX / 2, 5.0, u64::MAX, 3), // extreme base and max
            (1000, 2.0, 0, 5),                // max=0: result must be 0
            (1000, f64::NAN, 60_000, 1),      // NaN multiplier: must not panic, result ≤ max
            (1000, f64::INFINITY, 60_000, 1), // inf multiplier: must clamp to max
        ];

        for &(base_ms, multiplier, max_backoff_ms, cycle) in cases {
            let result = calculate_backoff_delay_ms(base_ms, multiplier, max_backoff_ms, cycle);
            assert!(
                result <= max_backoff_ms,
                "result {result} must not exceed max_backoff_ms {max_backoff_ms} \
                 (base={base_ms}, multiplier={multiplier}, cycle={cycle})"
            );
        }
    }
}
