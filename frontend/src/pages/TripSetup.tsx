/**
 * Trip setup.
 *
 * Note what this form does *not* ask for: a destination. That is the whole
 * point of TripWise, so the field does not exist, and the copy explains why.
 */

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import {
  addDaysIso,
  money,
  modeIcon,
  modeLabel,
  titleise,
  todayIso,
} from '../lib/format';
import { isGeolocationAvailable, requestLocation } from '../lib/geolocation';
import { clearDraft, loadDraft, saveDraft } from '../lib/storage';
import type {
  AccommodationType,
  AppConfig,
  AppOptions,
  Coordinates,
  Pace,
  TravelMode,
  TripRequest,
} from '../lib/types';
import { ErrorState, Spinner } from '../components/states';

const PACE_HINT: Record<Pace, string> = {
  relaxed: 'Two main things a day, with real breaks.',
  balanced: 'Three main things a day. The usual choice.',
  packed: 'Four or more a day. Long days, more ground covered.',
};

export interface TripDraft {
  originLabel: string;
  originCoordinates: Coordinates | null;
  originDetection: 'geolocation' | 'manual';
  budget: string;
  travelers: number;
  days: number;
  startDate: string;
  travelMode: TravelMode;
  accommodation: AccommodationType;
  pace: Pace;
  interests: string[];
  maxOneWayHours: string;
  includeStretch: boolean;
}

function emptyDraft(): TripDraft {
  return {
    originLabel: '',
    originCoordinates: null,
    originDetection: 'manual',
    budget: '35000',
    travelers: 2,
    days: 4,
    startDate: addDaysIso(todayIso(), 30),
    travelMode: 'any',
    accommodation: 'budget_hotel',
    pace: 'balanced',
    interests: [],
    maxOneWayHours: '',
    includeStretch: true,
  };
}

export function draftToRequest(draft: TripDraft): TripRequest {
  const maxHours = Number(draft.maxOneWayHours);
  return {
    origin: {
      coordinates: draft.originCoordinates,
      label: draft.originLabel.trim() || null,
      detection: draft.originDetection,
    },
    budget_inr: Number(draft.budget),
    travelers: draft.travelers,
    days: draft.days,
    start_date: draft.startDate,
    travel_mode: draft.travelMode,
    accommodation: draft.accommodation,
    pace: draft.pace,
    interests: draft.interests,
    max_one_way_hours: Number.isFinite(maxHours) && maxHours > 0 ? maxHours : null,
    include_stretch_options: draft.includeStretch,
    assumption_overrides: {},
  };
}

/** Client-side checks that mirror the server's, so errors arrive instantly. */
export function validateDraft(draft: TripDraft): Record<string, string> {
  const errors: Record<string, string> = {};

  if (!draft.originCoordinates && !draft.originLabel.trim()) {
    errors.origin = 'Share your location or type a city to start from.';
  }

  const budget = Number(draft.budget);
  if (!Number.isFinite(budget) || budget <= 0) {
    errors.budget = 'Enter your total budget in rupees.';
  } else {
    // Mirrors the backend's floor: food, local transport and the cheapest bed.
    const floor = 600 * draft.travelers * draft.days;
    if (budget < floor) {
      errors.budget =
        `A ${draft.days}-day trip for ${draft.travelers} traveller(s) needs at least about ` +
        `${money(floor)} before any travel to get there. Raise the budget, shorten the trip, ` +
        `or travel with fewer people.`;
    }
  }

  if (!draft.startDate) {
    errors.startDate = 'Pick a departure date.';
  } else if (draft.startDate < todayIso()) {
    errors.startDate = 'That date has already passed.';
  }

  if (draft.days < 1 || draft.days > 21) errors.days = 'Trips can run from 1 to 21 days.';
  if (draft.travelers < 1 || draft.travelers > 20) {
    errors.travelers = 'Between 1 and 20 travellers.';
  }

  return errors;
}

interface Props {
  config: AppConfig | null;
  onSubmit: (request: TripRequest) => void;
}

export function TripSetup({ config, onSubmit }: Props) {
  const navigate = useNavigate();
  const [draft, setDraft] = useState<TripDraft>(() => loadDraft<TripDraft>() ?? emptyDraft());
  const [options, setOptions] = useState<AppOptions | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [locating, setLocating] = useState(false);
  const [locationNote, setLocationNote] = useState<string | null>(null);
  const [resolving, setResolving] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const errors = useMemo(() => validateDraft(draft), [draft]);
  const hasGeocoder = (config?.geocoding.length ?? 0) > 0;

  useEffect(() => {
    let cancelled = false;
    api
      .options()
      .then((payload) => {
        if (!cancelled) setOptions(payload);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setOptionsError(
            error instanceof ApiError ? error.message : 'Could not load the form choices.',
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    saveDraft(draft);
  }, [draft]);

  function update<K extends keyof TripDraft>(key: K, value: TripDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function useMyLocation() {
    setLocating(true);
    setLocationNote(null);
    const outcome = await requestLocation();
    setLocating(false);

    if (!outcome.coordinates) {
      setLocationNote(outcome.error);
      return;
    }

    setDraft((current) => ({
      ...current,
      originCoordinates: outcome.coordinates,
      originDetection: 'geolocation',
    }));

    // Geolocation gives coordinates only. Ask the server for a readable name,
    // because that needs a geocoding key which stays server-side.
    setResolving(true);
    try {
      const place = await api.reverseGeocode(
        outcome.coordinates.lat,
        outcome.coordinates.lon,
      );
      setDraft((current) => ({ ...current, originLabel: place.label }));
      setLocationNote(
        place.geocoder_configured
          ? `Found you near ${place.label}.`
          : `Using your coordinates (${place.label}). No geocoder is configured on this ` +
            `deployment, so we cannot show a place name — distances and costs are unaffected.`,
      );
    } catch {
      setLocationNote(
        `Using your coordinates (${outcome.coordinates.lat.toFixed(
          3,
        )}, ${outcome.coordinates.lon.toFixed(3)}). Could not resolve a place name.`,
      );
    } finally {
      setResolving(false);
    }
  }

  async function resolveTypedPlace() {
    const query = draft.originLabel.trim();
    if (!query || !hasGeocoder) return;
    setResolving(true);
    setLocationNote(null);
    try {
      const place = await api.searchPlace(query);
      setDraft((current) => ({
        ...current,
        originCoordinates: place.coordinates,
        originLabel: place.label,
        originDetection: 'manual',
      }));
      setLocationNote(`Starting from ${place.label}.`);
    } catch (error) {
      setLocationNote(
        error instanceof ApiError ? error.message : `Could not find "${query}".`,
      );
    } finally {
      setResolving(false);
    }
  }

  function toggleInterest(value: string) {
    setDraft((current) => ({
      ...current,
      interests: current.interests.includes(value)
        ? current.interests.filter((item) => item !== value)
        : current.interests.length >= 12
          ? current.interests
          : [...current.interests, value],
    }));
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitted(true);
    if (Object.keys(errors).length) {
      document.querySelector<HTMLElement>('[data-error="true"]')?.scrollIntoView({
        block: 'center',
      });
      return;
    }
    onSubmit(draftToRequest(draft));
    navigate('/recommendations');
  }

  const showError = (key: string) => (submitted ? errors[key] : undefined);
  const endDate = addDaysIso(draft.startDate, Math.max(0, draft.days - 1));

  return (
    <div className="stack-xl">
      {/* Hero */}
      <header className="stack">
        <p className="eyebrow">Budget-first planning</p>
        <h1>Tell us your budget. We'll find where you can go.</h1>
        <p className="muted" style={{ maxWidth: '62ch' }}>
          There's no destination field here, and that's deliberate. Give TripWise your starting
          point, what you can spend and when you're free, and it works out which places are
          genuinely affordable and reachable — then ranks them and plans the days.
        </p>
      </header>

      {optionsError ? (
        <ErrorState
          title="Could not load the form"
          message={optionsError}
          onRetry={() => window.location.reload()}
        />
      ) : null}

      <form className="stack-xl" onSubmit={handleSubmit} noValidate>
        {/* ---------------- Where from ---------------- */}
        <section className="card card-pad stack" data-error={Boolean(showError('origin'))}>
          <div>
            <h2>Where are you starting from?</h2>
            <p className="subtle">
              Everything is measured from here — distance, travel time and the cost of getting
              there and back.
            </p>
          </div>

          <div className="row">
            {isGeolocationAvailable() ? (
              <button
                type="button"
                className="btn btn-secondary"
                onClick={useMyLocation}
                disabled={locating || resolving}
              >
                {locating ? <Spinner /> : <span aria-hidden="true">📍</span>}
                {locating ? 'Finding you…' : 'Use my location'}
              </button>
            ) : null}
            <span className="subtle">or type a city</span>
          </div>

          <div className="field">
            <label className="field-label" htmlFor="origin">
              Starting city
            </label>
            <div className="row" style={{ flexWrap: 'nowrap', gap: 'var(--space-2)' }}>
              <input
                id="origin"
                className="input"
                type="text"
                autoComplete="address-level2"
                placeholder="e.g. Hyderabad"
                value={draft.originLabel}
                aria-invalid={Boolean(showError('origin'))}
                onChange={(event) => {
                  const value = event.target.value;
                  setDraft((current) => ({
                    ...current,
                    originLabel: value,
                    // Typing a new name invalidates coordinates from before.
                    originCoordinates: null,
                    originDetection: 'manual',
                  }));
                }}
                onBlur={resolveTypedPlace}
              />
              {hasGeocoder ? (
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={resolveTypedPlace}
                  disabled={resolving || !draft.originLabel.trim()}
                >
                  {resolving ? <Spinner /> : 'Find'}
                </button>
              ) : null}
            </div>
            {showError('origin') ? (
              <span className="field-error">{showError('origin')}</span>
            ) : null}
            {!hasGeocoder && !draft.originCoordinates ? (
              <span className="field-hint">
                No geocoding provider is configured on this deployment, so a typed city name
                cannot be turned into coordinates. Use <strong>Use my location</strong> instead,
                or add a geocoding key to the backend.
              </span>
            ) : null}
            {draft.originCoordinates ? (
              <span className="field-hint">
                ✓ Using {draft.originCoordinates.lat.toFixed(3)},{' '}
                {draft.originCoordinates.lon.toFixed(3)}
                {draft.originDetection === 'geolocation' ? ' from your device' : ''}
              </span>
            ) : null}
          </div>

          {locationNote ? (
            <p className="notice notice-info xs">
              <span className="notice-icon" aria-hidden="true">
                ℹ️
              </span>
              <span>{locationNote}</span>
            </p>
          ) : null}
        </section>

        {/* ---------------- Budget and group ---------------- */}
        <section className="card card-pad stack">
          <div>
            <h2>Budget, group and dates</h2>
            <p className="subtle">
              The budget is for everyone together, covering the whole trip including getting
              there and back.
            </p>
          </div>

          <div className="grid grid-2">
            <div className="field" data-error={Boolean(showError('budget'))}>
              <label className="field-label" htmlFor="budget">
                Total budget for the group
              </label>
              <div className="input-prefix">
                <span className="input-prefix-symbol" aria-hidden="true">
                  ₹
                </span>
                <input
                  id="budget"
                  className="input tnum"
                  type="number"
                  inputMode="numeric"
                  min={1000}
                  step={1000}
                  value={draft.budget}
                  aria-invalid={Boolean(showError('budget'))}
                  onChange={(event) => update('budget', event.target.value)}
                />
              </div>
              {showError('budget') ? (
                <span className="field-error">{showError('budget')}</span>
              ) : (
                <span className="field-hint">
                  {Number(draft.budget) > 0 && draft.travelers > 0 && draft.days > 0
                    ? `About ${money(
                        Number(draft.budget) / draft.travelers / draft.days,
                      )} per person per day.`
                    : 'Everything for everyone, start to finish.'}
                </span>
              )}
            </div>

            <div className="field" data-error={Boolean(showError('travelers'))}>
              <label className="field-label" htmlFor="travelers">
                Travellers
              </label>
              <input
                id="travelers"
                className="input tnum"
                type="number"
                inputMode="numeric"
                min={1}
                max={20}
                value={draft.travelers}
                onChange={(event) => update('travelers', Number(event.target.value) || 1)}
              />
              {showError('travelers') ? (
                <span className="field-error">{showError('travelers')}</span>
              ) : (
                <span className="field-hint">
                  Rooms and vehicles are shared, so the cost per head falls as the group grows.
                </span>
              )}
            </div>

            <div className="field" data-error={Boolean(showError('startDate'))}>
              <label className="field-label" htmlFor="start">
                Departure date
              </label>
              <input
                id="start"
                className="input"
                type="date"
                min={todayIso()}
                value={draft.startDate}
                aria-invalid={Boolean(showError('startDate'))}
                onChange={(event) => update('startDate', event.target.value)}
              />
              {showError('startDate') ? (
                <span className="field-error">{showError('startDate')}</span>
              ) : (
                <span className="field-hint">
                  Booking further ahead lowers the fare estimates.
                </span>
              )}
            </div>

            <div className="field" data-error={Boolean(showError('days'))}>
              <label className="field-label" htmlFor="days">
                Trip length: {draft.days} day{draft.days === 1 ? '' : 's'}
              </label>
              <input
                id="days"
                type="range"
                min={1}
                max={21}
                step={1}
                value={draft.days}
                style={{ width: '100%', accentColor: 'var(--brand)' }}
                onChange={(event) => update('days', Number(event.target.value))}
              />
              <span className="field-hint">
                {draft.days === 1
                  ? 'A single day out — no overnight stay.'
                  : `${draft.days - 1} night${draft.days - 1 === 1 ? '' : 's'} away, back on ${endDate}.`}
              </span>
            </div>
          </div>
        </section>

        {/* ---------------- How you travel ---------------- */}
        <section className="card card-pad stack">
          <div>
            <h2>How do you want to travel?</h2>
            <p className="subtle">
              Leave this on <em>Any</em> and TripWise prices every mode, then picks the cheapest
              one that actually fits your trip length.
            </p>
          </div>

          <div className="field">
            <span className="field-label">Travel mode</span>
            <div className="segmented" role="group" aria-label="Travel mode">
              {(options?.travel_modes ?? [{ value: 'any', label: 'Any' }]).map((mode) => (
                <button
                  key={mode.value}
                  type="button"
                  className="segmented-option"
                  aria-pressed={draft.travelMode === mode.value}
                  onClick={() => update('travelMode', mode.value as TravelMode)}
                >
                  <span aria-hidden="true">{modeIcon(mode.value)}</span>
                  <span>{modeLabel(mode.value)}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-2">
            <div className="field">
              <label className="field-label" htmlFor="accommodation">
                Where you'd like to stay
              </label>
              <select
                id="accommodation"
                className="select"
                value={draft.accommodation}
                onChange={(event) =>
                  update('accommodation', event.target.value as AccommodationType)
                }
              >
                {(options?.accommodation_types ?? []).map((type) => (
                  <option key={type.value} value={type.value}>
                    {type.label} — about {money(type.indicative_inr_per_night)} per {type.priced_per}
                    , per night
                  </option>
                ))}
              </select>
              <span className="field-hint">
                {options?.accommodation_types.find((t) => t.value === draft.accommodation)
                  ?.priced_per === 'bed'
                  ? 'Hostels are priced per bed, so everyone pays for their own.'
                  : 'Hotels are priced per room, shared between travellers.'}
              </span>
            </div>

            <div className="field">
              <span className="field-label">Pace</span>
              <div className="segmented" role="group" aria-label="Travel pace">
                {(options?.paces ?? []).map((pace) => (
                  <button
                    key={pace.value}
                    type="button"
                    className="segmented-option"
                    aria-pressed={draft.pace === pace.value}
                    onClick={() => update('pace', pace.value as Pace)}
                  >
                    <span>{pace.label}</span>
                    <span className="xs subtle">{pace.activities_per_day}/day</span>
                  </button>
                ))}
              </div>
              <span className="field-hint">{PACE_HINT[draft.pace]}</span>
            </div>
          </div>
        </section>

        {/* ---------------- Interests ---------------- */}
        <section className="card card-pad stack">
          <div>
            <h2>What are you into?</h2>
            <p className="subtle">
              Optional, but it changes the ranking a lot. Pick up to 12.
            </p>
          </div>
          {options ? (
            <div className="chips">
              {options.interests.map((interest) => (
                <button
                  key={interest.value}
                  type="button"
                  className="chip"
                  aria-pressed={draft.interests.includes(interest.value)}
                  onClick={() => toggleInterest(interest.value)}
                >
                  {interest.label}
                </button>
              ))}
            </div>
          ) : (
            <Spinner label="Loading interests…" />
          )}
          {draft.interests.length ? (
            <p className="xs subtle">
              Selected: {draft.interests.map(titleise).join(', ')}
            </p>
          ) : null}
        </section>

        {/* ---------------- Advanced ---------------- */}
        <details className="card card-pad">
          <summary className="strong" style={{ cursor: 'pointer' }}>
            Advanced constraints
          </summary>
          <div className="stack" style={{ marginTop: 'var(--space-4)' }}>
            <div className="field">
              <label className="field-label" htmlFor="maxhours">
                Maximum travel time, one way (hours)
              </label>
              <input
                id="maxhours"
                className="input tnum"
                type="number"
                min={1}
                max={72}
                placeholder="No limit"
                value={draft.maxOneWayHours}
                onChange={(event) => update('maxOneWayHours', event.target.value)}
                style={{ maxWidth: 160 }}
              />
              <span className="field-hint">
                TripWise already rejects anywhere whose return journey would eat more than a
                third of your trip. Set this to be stricter.
              </span>
            </div>

            <label className="row" style={{ cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={draft.includeStretch}
                onChange={(event) => update('includeStretch', event.target.checked)}
                style={{ accentColor: 'var(--brand)' }}
              />
              <span className="small">
                Show a couple of options just over budget, clearly marked
              </span>
            </label>
          </div>
        </details>

        {/* ---------------- Submit ---------------- */}
        <div className="stack">
          {submitted && Object.keys(errors).length ? (
            <div className="notice notice-error small" role="alert">
              <span className="notice-icon" aria-hidden="true">
                ⚠️
              </span>
              <div>
                <strong>Please fix these first:</strong>
                <ul style={{ margin: 'var(--space-2) 0 0', paddingLeft: '1.1rem' }}>
                  {Object.values(errors).map((message) => (
                    <li key={message}>{message}</li>
                  ))}
                </ul>
              </div>
            </div>
          ) : null}

          <div className="row">
            <button type="submit" className="btn btn-primary btn-lg">
              Find where I can go <span aria-hidden="true">→</span>
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                clearDraft();
                setDraft(emptyDraft());
                setSubmitted(false);
                setLocationNote(null);
              }}
            >
              Reset
            </button>
          </div>

          {config ? (
            <p className="xs subtle">
              Searching {config.destination_count} destinations across India · dataset{' '}
              {config.dataset_version}
            </p>
          ) : null}
        </div>
      </form>
    </div>
  );
}
