/**
 * Tells the user, up front, that nothing on screen is a live quote.
 *
 * This is deliberately prominent rather than a footnote. A planner that
 * looks complete while every number is modelled would be misleading, and the
 * fix is to say so plainly, once, where it cannot be missed.
 */

import type { AppConfig } from '../lib/types';

export function DemoModeBanner({ config }: { config: AppConfig | null }) {
  if (!config) return null;

  if (config.demo_mode) {
    return (
      <div className="notice notice-warn no-print" role="status">
        <span className="notice-icon" aria-hidden="true">
          📐
        </span>
        <div>
          <strong>Demo mode — all figures are estimates.</strong>{' '}
          No travel provider is connected, so every cost here comes from TripWise's own
          cost model, using reference rates for India
          {config.cost_model_reference_date
            ? ` set in ${new Date(config.cost_model_reference_date).toLocaleDateString('en-IN', {
                month: 'long',
                year: 'numeric',
              })}`
            : ''}
          . The planner is fully functional, but nothing shown is a live fare or room rate.
          Connect providers in the backend's <code>.env</code> to pull live prices.
        </div>
      </div>
    );
  }

  const live: string[] = [];
  if (config.routing.length) live.push('road routing');
  if (config.geocoding.length) live.push('geocoding');
  if (config.places.length) live.push('places');
  if (config.flights.length) live.push('flight fares');
  if (config.hotels.length) live.push('hotel rates');
  if (config.weather.length) live.push('climate data');

  return (
    <div className="notice notice-info no-print" role="status">
      <span className="notice-icon" aria-hidden="true">
        🛰️
      </span>
      <div>
        <strong>Live data connected for {live.join(', ') || 'some services'}.</strong>{' '}
        Figures marked <span className="badge prov-live badge-dot">Live</span> come from a
        provider; everything else is still a TripWise estimate. Indian rail and intercity bus
        fares have no public API, so those are always estimated.
        {config.llm.enabled ? ' Itineraries are written by an AI model and validated server-side.' : ''}
      </div>
    </div>
  );
}
