import { describe, expect, it } from 'vitest';
import {
  addDaysIso,
  categoryLabel,
  distance,
  duration,
  money,
  moneyCompact,
  percent,
  titleise,
} from './format';

describe('money', () => {
  it('formats in Indian rupees with Indian digit grouping', () => {
    // 1,23,456 -- lakh grouping, not 123,456.
    expect(money(123456)).toContain('1,23,456');
    expect(money(123456)).toContain('₹');
  });

  it('rounds to whole rupees', () => {
    expect(money(99.6)).toContain('100');
  });

  it('handles zero and non-finite input without crashing', () => {
    expect(money(0)).toContain('0');
    expect(money(Number.NaN)).toBe('—');
    expect(money(Number.POSITIVE_INFINITY)).toBe('—');
  });
});

describe('moneyCompact', () => {
  it('uses lakh and crore for large amounts', () => {
    expect(moneyCompact(250000)).toBe('₹2.5L');
    expect(moneyCompact(15000000)).toBe('₹1.50Cr');
  });

  it('uses thousands below a lakh', () => {
    expect(moneyCompact(45600)).toBe('₹45.6k');
  });

  it('leaves small amounts alone', () => {
    expect(moneyCompact(450)).toBe('₹450');
  });
});

describe('duration', () => {
  it('splits hours and minutes', () => {
    expect(duration(4.5)).toBe('4h 30m');
  });

  it('omits minutes on a whole hour', () => {
    expect(duration(3)).toBe('3h');
  });

  it('shows minutes only under an hour', () => {
    expect(duration(0.75)).toBe('45m');
  });

  it('rejects nonsense', () => {
    expect(duration(-1)).toBe('—');
    expect(duration(Number.NaN)).toBe('—');
  });
});

describe('distance', () => {
  it('uses metres below a kilometre', () => {
    expect(distance(0.4)).toBe('400 m');
  });

  it('keeps one decimal for short hops', () => {
    expect(distance(3.456)).toBe('3.5 km');
  });

  it('rounds longer distances', () => {
    expect(distance(672.1)).toBe('672 km');
  });
});

describe('percent', () => {
  it('renders a fraction as a percentage', () => {
    expect(percent(0.563)).toBe('56%');
    expect(percent(0.563, 1)).toBe('56.3%');
  });
});

describe('addDaysIso', () => {
  it('advances a date', () => {
    expect(addDaysIso('2026-11-20', 4)).toBe('2026-11-24');
  });

  it('crosses a month boundary', () => {
    expect(addDaysIso('2026-11-28', 5)).toBe('2026-12-03');
  });

  it('crosses a leap day', () => {
    expect(addDaysIso('2028-02-27', 3)).toBe('2028-03-01');
  });
});

describe('labels', () => {
  it('titleises snake_case', () => {
    expect(titleise('budget_hotel')).toBe('Budget Hotel');
  });

  it('gives cost categories readable names', () => {
    expect(categoryLabel('intercity_transport')).toBe('Getting there and back');
    expect(categoryLabel('contingency')).toBe('Contingency buffer');
  });

  it('falls back gracefully for an unknown category', () => {
    expect(categoryLabel('some_new_thing')).toBe('Some New Thing');
  });
});
