import { describe, expect, it } from 'vitest';
import { draftToRequest, validateDraft, type TripDraft } from './TripSetup';
import { addDaysIso, todayIso } from '../lib/format';

function draft(overrides: Partial<TripDraft> = {}): TripDraft {
  return {
    originLabel: 'Hyderabad',
    originCoordinates: { lat: 17.385, lon: 78.486 },
    originDetection: 'manual',
    budget: '35000',
    travelers: 2,
    days: 4,
    startDate: addDaysIso(todayIso(), 30),
    travelMode: 'any',
    accommodation: 'budget_hotel',
    pace: 'balanced',
    interests: ['beaches'],
    maxOneWayHours: '',
    includeStretch: true,
    ...overrides,
  };
}

describe('validateDraft', () => {
  it('accepts a sensible trip', () => {
    expect(validateDraft(draft())).toEqual({});
  });

  it('requires an origin', () => {
    const errors = validateDraft(draft({ originLabel: '', originCoordinates: null }));
    expect(errors.origin).toBeDefined();
  });

  it('accepts coordinates with no label', () => {
    const errors = validateDraft(draft({ originLabel: '' }));
    expect(errors.origin).toBeUndefined();
  });

  it('rejects a zero budget', () => {
    expect(validateDraft(draft({ budget: '0' })).budget).toBeDefined();
  });

  it('rejects a budget below the on-the-ground floor', () => {
    // 2 travellers x 4 days needs at least ~4,800 before any travel.
    const errors = validateDraft(draft({ budget: '3000' }));
    expect(errors.budget).toContain('at least');
  });

  it('scales the floor with group size and duration', () => {
    // 20,000 is fine for 2 people over 4 days, but not for 8 over 10.
    expect(validateDraft(draft({ budget: '20000' })).budget).toBeUndefined();
    expect(
      validateDraft(draft({ budget: '20000', travelers: 8, days: 10 })).budget,
    ).toBeDefined();
  });

  it('rejects a past departure date', () => {
    expect(validateDraft(draft({ startDate: '2020-01-01' })).startDate).toBeDefined();
  });

  it('rejects an out-of-range trip length', () => {
    expect(validateDraft(draft({ days: 0 })).days).toBeDefined();
    expect(validateDraft(draft({ days: 40 })).days).toBeDefined();
  });

  it('rejects an out-of-range group size', () => {
    expect(validateDraft(draft({ travelers: 0 })).travelers).toBeDefined();
    expect(validateDraft(draft({ travelers: 99 })).travelers).toBeDefined();
  });
});

describe('draftToRequest', () => {
  it('has no destination field -- discovery is the product', () => {
    const request = draftToRequest(draft());
    expect(request).not.toHaveProperty('destination');
    expect(request).not.toHaveProperty('destination_id');
  });

  it('converts the budget to a number', () => {
    expect(draftToRequest(draft({ budget: '42000' })).budget_inr).toBe(42000);
  });

  it('omits an empty travel-time limit', () => {
    expect(draftToRequest(draft()).max_one_way_hours).toBeNull();
  });

  it('passes a travel-time limit through when set', () => {
    expect(draftToRequest(draft({ maxOneWayHours: '8' })).max_one_way_hours).toBe(8);
  });

  it('ignores a non-numeric travel-time limit', () => {
    expect(draftToRequest(draft({ maxOneWayHours: 'soon' })).max_one_way_hours).toBeNull();
  });

  it('carries the coordinates and detection method', () => {
    const request = draftToRequest(draft({ originDetection: 'geolocation' }));
    expect(request.origin.coordinates).toEqual({ lat: 17.385, lon: 78.486 });
    expect(request.origin.detection).toBe('geolocation');
  });

  it('sends a null label rather than an empty string', () => {
    expect(draftToRequest(draft({ originLabel: '   ' })).origin.label).toBeNull();
  });
});
