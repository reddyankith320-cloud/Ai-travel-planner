/**
 * Preview mode: replay recorded results instead of calling a server.
 *
 * The preview ships as one static page with no backend, so the planner cannot
 * run in it. This module intercepts the app's /api calls and answers them with
 * results the REAL backend computed ahead of time for a fixed set of example
 * trips (scripts/build-preview-data.py). Nothing is invented here.
 *
 * What it will NOT do, on purpose: answer a request it has no recording for.
 * Returning the nearest example under the user's own numbers would show a
 * total that does not match their budget, which is exactly the kind of
 * plausible-looking wrong answer TripWise exists to avoid. An unmatched request
 * gets a plain explanation instead.
 */

import type { TripRequest } from '../lib/types';
import data from './data.json';

export interface PreviewScenario {
  id: string;
  label: string;
  request: TripRequest;
  recommendations: unknown;
  itineraries: Record<string, unknown>;
}

interface PreviewData {
  generated_on: string;
  start_date: string;
  config: Record<string, unknown>;
  options: unknown;
  scenarios: PreviewScenario[];
}

const preview = data as unknown as PreviewData;

export const PREVIEW_SCENARIOS: PreviewScenario[] = preview.scenarios;
export const PREVIEW_GENERATED_ON = preview.generated_on;

/** Fields that decide the answer. Everything else is presentation. */
function keyFor(trip: Partial<TripRequest>): string {
  const coordinates = trip.origin?.coordinates;
  return JSON.stringify([
    coordinates ? [Number(coordinates.lat).toFixed(3), Number(coordinates.lon).toFixed(3)] : null,
    Number(trip.budget_inr),
    trip.travelers,
    trip.days,
    trip.start_date,
    trip.travel_mode,
    trip.accommodation,
    trip.pace,
    [...(trip.interests ?? [])].sort(),
    trip.max_one_way_hours ?? null,
    trip.include_stretch_options ?? true,
  ]);
}

const BY_KEY = new Map(PREVIEW_SCENARIOS.map((s) => [keyFor(s.request), s]));

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

const UNMATCHED =
  'This preview can only show its example trips, because there is no server behind it to ' +
  'work out a new one. Go back and pick one of the examples, or run TripWise itself for ' +
  'your own numbers.';

const NEEDS_SERVER = (what: string) =>
  `${what} needs the live TripWise server, which this preview does not have.`;

async function route(url: URL, init?: RequestInit): Promise<Response> {
  const path = url.pathname;
  const method = (init?.method ?? 'GET').toUpperCase();
  const body = typeof init?.body === 'string' ? JSON.parse(init.body) : null;

  if (path === '/api/meta/config') {
    return json({
      ...preview.config,
      // Typed-city search needs a geocoder, and there is none here.
      geocoding: [],
      demo_mode: true,
    });
  }
  if (path === '/api/meta/options') return json(preview.options);

  if (path.startsWith('/api/geo/')) {
    return json({ detail: NEEDS_SERVER('Looking up a place') }, 404);
  }

  if (path === '/api/recommendations' && method === 'POST') {
    const scenario = BY_KEY.get(keyFor(body));
    return scenario ? json(scenario.recommendations) : json({ detail: UNMATCHED }, 422);
  }

  if (path === '/api/itinerary' && method === 'POST') {
    const edited =
      (body?.excluded_place_names?.length ?? 0) > 0 ||
      Object.keys(body?.trip?.assumption_overrides ?? {}).length > 0;
    if (edited) {
      return json(
        { detail: NEEDS_SERVER('Re-planning after an edit (removing a stop or changing an assumption)') },
        409,
      );
    }
    const scenario = BY_KEY.get(keyFor(body?.trip));
    const itinerary = scenario?.itineraries[body?.destination_id];
    return itinerary ? json(itinerary) : json({ detail: UNMATCHED }, 422);
  }

  if (path === '/api/trips') {
    return method === 'GET'
      ? json([])
      : json({ detail: NEEDS_SERVER('Saving a trip') }, 503);
  }
  if (path.startsWith('/api/trips/')) {
    return json({ detail: NEEDS_SERVER('Saved trips') }, 404);
  }

  return json({ detail: 'Not available in the preview.' }, 404);
}

/** Route every /api call to the recordings. Leave all other requests alone. */
export function installPreviewShim(): void {
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const raw =
      typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(raw, window.location.href);
    if (url.pathname.startsWith('/api/')) return route(url, init);
    return realFetch(input, init);
  };
}
