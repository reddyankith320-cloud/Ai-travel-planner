/**
 * The only module that talks to the network.
 *
 * Everything goes to TripWise's own backend. The browser never holds a
 * provider credential, so there is nothing here to leak: no API keys, no
 * Authorization headers for third parties, no direct calls to Google or
 * anyone else.
 */

import type {
  AppConfig,
  AppOptions,
  Itinerary,
  RecommendationResponse,
  ResolvedPlace,
  TripDetail,
  TripRequest,
  TripSummary,
} from './types';
import { getOwnerToken } from './storage';

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');

/** A request that failed, with a message worth showing a human. */
export class ApiError extends Error {
  readonly status: number;
  readonly retryable: boolean;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    // 429 and 5xx are worth another try; a 4xx means fix the request.
    this.retryable = status === 429 || status >= 500;
  }
}

/** Extract the clearest message the backend gave us. */
function describeError(payload: unknown, status: number): string {
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === 'string') return detail;
    // FastAPI validation errors arrive as an array of field problems.
    if (Array.isArray(detail)) {
      const messages = detail
        .map((entry) => {
          if (entry && typeof entry === 'object') {
            const location = Array.isArray((entry as { loc?: unknown[] }).loc)
              ? (entry as { loc: unknown[] }).loc.filter((p) => p !== 'body').join(' → ')
              : '';
            const message = String((entry as { msg?: unknown }).msg ?? 'is invalid');
            return location ? `${location}: ${message}` : message;
          }
          return String(entry);
        })
        .filter(Boolean);
      if (messages.length) return messages.join('\n');
    }
  }
  if (status === 0) {
    return 'Could not reach the TripWise server. Check that the backend is running.';
  }
  return `Request failed (HTTP ${status}).`;
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'DELETE';
  body?: unknown;
  withOwner?: boolean;
  signal?: AbortSignal;
  timeoutMs?: number;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, withOwner = false, signal, timeoutMs = 60_000 } = options;

  const headers: Record<string, string> = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (withOwner) headers['X-TripWise-Owner'] = getOwnerToken();

  // Recommendation requests can legitimately take a few seconds when live
  // providers are configured, so the timeout is generous but always present.
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  if (signal) {
    signal.addEventListener('abort', () => controller.abort(), { once: true });
  }

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (error) {
    window.clearTimeout(timer);
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('The request took too long and was cancelled.', 408);
    }
    throw new ApiError(describeError(null, 0), 0);
  }
  window.clearTimeout(timer);

  if (response.status === 204) return undefined as T;

  let payload: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    throw new ApiError(describeError(payload, response.status), response.status);
  }
  return payload as T;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export const api = {
  config: () => request<AppConfig>('/api/meta/config', { timeoutMs: 15_000 }),

  options: () => request<AppOptions>('/api/meta/options', { timeoutMs: 15_000 }),

  reverseGeocode: (lat: number, lon: number) =>
    request<ResolvedPlace>(
      `/api/geo/reverse?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`,
      { timeoutMs: 15_000 },
    ),

  searchPlace: (query: string) =>
    request<ResolvedPlace>(`/api/geo/search?q=${encodeURIComponent(query)}`, {
      timeoutMs: 15_000,
    }),

  recommendations: (trip: TripRequest, limit = 5, signal?: AbortSignal) =>
    request<RecommendationResponse>('/api/recommendations', {
      method: 'POST',
      body: { ...trip, limit },
      signal,
    }),

  itinerary: (
    destinationId: string,
    trip: TripRequest,
    excludedPlaceNames: string[] = [],
    regenerate = false,
    signal?: AbortSignal,
  ) =>
    request<Itinerary>('/api/itinerary', {
      method: 'POST',
      body: {
        destination_id: destinationId,
        trip,
        excluded_place_names: excludedPlaceNames,
        regenerate,
      },
      signal,
    }),

  listTrips: () => request<TripSummary[]>('/api/trips', { withOwner: true }),

  getTrip: (id: string) =>
    request<TripDetail>(`/api/trips/${encodeURIComponent(id)}`, { withOwner: true }),

  saveTrip: (payload: {
    title: string;
    destination_id: string;
    destination_name: string;
    trip: TripRequest;
    itinerary: Itinerary;
    notes?: string | null;
  }) =>
    request<TripSummary>('/api/trips', {
      method: 'POST',
      body: payload,
      withOwner: true,
    }),

  deleteTrip: (id: string) =>
    request<void>(`/api/trips/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      withOwner: true,
    }),
};
