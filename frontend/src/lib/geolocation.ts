/**
 * Browser geolocation.
 *
 * The API returns coordinates and nothing else -- never a street address.
 * Resolving a readable place name is the backend's job, because that needs a
 * geocoding key, which stays on the server.
 */

export interface GeolocationOutcome {
  coordinates: { lat: number; lon: number } | null;
  accuracyMetres: number | null;
  error: string | null;
  denied: boolean;
}

const MESSAGES: Record<number, string> = {
  1: 'Location access was denied. Type your city instead.',
  2: 'Your location is unavailable right now. Type your city instead.',
  3: 'Finding your location took too long. Type your city instead.',
};

export function isGeolocationAvailable(): boolean {
  return typeof navigator !== 'undefined' && 'geolocation' in navigator;
}

export function requestLocation(timeoutMs = 12_000): Promise<GeolocationOutcome> {
  if (!isGeolocationAvailable()) {
    return Promise.resolve({
      coordinates: null,
      accuracyMetres: null,
      error: 'This browser cannot share your location. Type your city instead.',
      denied: false,
    });
  }

  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (position) =>
        resolve({
          coordinates: {
            lat: position.coords.latitude,
            lon: position.coords.longitude,
          },
          accuracyMetres: position.coords.accuracy ?? null,
          error: null,
          denied: false,
        }),
      (error) =>
        resolve({
          coordinates: null,
          accuracyMetres: null,
          error: MESSAGES[error.code] ?? 'Could not get your location. Type your city instead.',
          denied: error.code === 1,
        }),
      {
        // City-level accuracy is plenty for choosing a destination, and the
        // low-power option resolves far faster.
        enableHighAccuracy: false,
        timeout: timeoutMs,
        maximumAge: 300_000,
      },
    );
  });
}
