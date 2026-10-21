/**
 * Display formatting. Money, durations and dates in one place so the whole
 * app renders them identically.
 */

const INR = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
});

const INR_PRECISE = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Indian-format currency, rounded to the rupee. */
export function money(amount: number): string {
  if (!Number.isFinite(amount)) return '—';
  return INR.format(Math.round(amount));
}

/** Currency to the paisa, for line-item detail where exactness matters. */
export function moneyPrecise(amount: number): string {
  if (!Number.isFinite(amount)) return '—';
  return INR_PRECISE.format(amount);
}

/** Compact money for tight spaces: ₹1.2L, ₹45.6k. */
export function moneyCompact(amount: number): string {
  if (!Number.isFinite(amount)) return '—';
  const abs = Math.abs(amount);
  if (abs >= 10_000_000) return `₹${(amount / 10_000_000).toFixed(2)}Cr`;
  if (abs >= 100_000) return `₹${(amount / 100_000).toFixed(1)}L`;
  if (abs >= 1_000) return `₹${(amount / 1_000).toFixed(1)}k`;
  return `₹${Math.round(amount)}`;
}

export function percent(fraction: number, digits = 0): string {
  if (!Number.isFinite(fraction)) return '—';
  return `${(fraction * 100).toFixed(digits)}%`;
}

/** Hours as "4h 30m", or "45m" under an hour. */
export function duration(hours: number): string {
  if (!Number.isFinite(hours) || hours < 0) return '—';
  const totalMinutes = Math.round(hours * 60);
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

export function minutes(value: number): string {
  if (!Number.isFinite(value)) return '—';
  return duration(value / 60);
}

export function distance(km: number): string {
  if (!Number.isFinite(km)) return '—';
  if (km < 1) return `${Math.round(km * 1000)} m`;
  if (km < 10) return `${km.toFixed(1)} km`;
  return `${Math.round(km).toLocaleString('en-IN')} km`;
}

export function shortDate(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

export function longDate(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString('en-IN', {
    weekday: 'short',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });
}

export function dayOfWeek(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString('en-IN', { weekday: 'long' });
}

/** Today in YYYY-MM-DD, in the viewer's own timezone. */
export function todayIso(): string {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

export function addDaysIso(iso: string, days: number): string {
  const date = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(date.getTime())) return iso;
  date.setDate(date.getDate() + days);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}

/** "budget_hotel" → "Budget Hotel" */
export function titleise(value: string): string {
  return value
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

export function categoryLabel(category: string): string {
  const labels: Record<string, string> = {
    intercity_transport: 'Getting there and back',
    accommodation: 'Accommodation',
    food: 'Food and drinks',
    local_transport: 'Local transport',
    activities: 'Activities and entry fees',
    taxes_and_fees: 'Taxes and fees',
    contingency: 'Contingency buffer',
  };
  return labels[category] ?? titleise(category);
}

export function modeLabel(mode: string): string {
  const labels: Record<string, string> = {
    bus: 'Bus',
    train: 'Train',
    car: 'Car',
    flight: 'Flight',
    any: 'Any',
  };
  return labels[mode] ?? titleise(mode);
}

export function modeIcon(mode: string): string {
  const icons: Record<string, string> = {
    bus: '🚌',
    train: '🚆',
    car: '🚗',
    flight: '✈️',
    any: '🧭',
  };
  return icons[mode] ?? '🧭';
}
