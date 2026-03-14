import { describe, it, expect } from 'vitest';
import { formatDuration } from './format-duration.pipe';

describe('formatDuration', () => {
  it('should return "0s" for 0 seconds', () => {
    expect(formatDuration(0)).toBe('0s');
  });

  it('should format seconds under 60 as seconds only', () => {
    expect(formatDuration(45)).toBe('45s');
  });

  it('should format 754 seconds as 12m 34s', () => {
    expect(formatDuration(754)).toBe('12m 34s');
  });

  it('should format 5100 seconds as 1h 25m', () => {
    expect(formatDuration(5100)).toBe('1h 25m');
  });

  it('should return empty string for null', () => {
    expect(formatDuration(null)).toBe('');
  });

  it('should return empty string for undefined', () => {
    expect(formatDuration(undefined)).toBe('');
  });

  it('should handle very large values', () => {
    expect(formatDuration(7200)).toBe('2h 0m');
  });

  it('should handle decimal values', () => {
    expect(formatDuration(90.5)).toBe('1m 30s');
  });

  it('should be usable as a pure function', () => {
    expect(formatDuration(123)).toBe('2m 3s');
  });
});
