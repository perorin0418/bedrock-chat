import { describe, expect, it } from 'vitest';
import { formatPrice } from '../PriceUtils';

describe('formatPrice', () => {
  it('formats a sub-cent price with 4 decimal places', () => {
    expect(formatPrice(0.0032)).toBe('$0.0032');
  });

  it('formats zero', () => {
    expect(formatPrice(0)).toBe('$0.0000');
  });

  it('rounds to 4 decimal places', () => {
    expect(formatPrice(1.23456)).toBe('$1.2346');
  });
});
