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
      <div className="notice notice-brand no-print" role="status">
        <span className="notice-icon" aria-hidden="true">
          📐
        </span>
        <div>
          <strong>Running free — every figure is an estimate.</strong>{' '}
          This is the full planner, not a trial: maps, geocoding and places come from
          OpenStreetMap, and every cost is computed by TripWise's own model using reference
          rates for India
          {config.cost_model_reference_date
            ? ` set in ${new Date(config.cost_model_reference_date).toLocaleDateString('en-IN', {
                month: 'long',
                year: 'numeric',
              })}`
            : ''}
          . No key, no card, nothing to pay. Nothing here is a live fare or room rate, so
          check prices before you book.
        </div>
      </div>
    );
  }

  // Only Google-backed capabilities count as "live": the free OSM services
  // are always on and saying "live geocoding" because Nominatim answered
  // would overstate what is actually connected.
  const live: string[] = [];
  if (config.geocoding[0] === 'google') live.push('geocoding');
  if (config.routing[0] === 'google') live.push('road routing');
  if (config.places[0] === 'google') live.push('places');

  return (
    <div className="notice notice-info no-print" role="status">
      <span className="notice-icon" aria-hidden="true">
        🛰️
      </span>
      <div>
        <strong>
          {live.length
            ? `Google Maps connected for ${live.join(', ')}.`
            : 'Running on free data sources.'}
        </strong>{' '}
        Figures marked <span className="badge prov-live badge-dot">Live</span> came from a
        provider; everything else is a TripWise estimate. Rail, bus, flight and hotel prices
        are always estimated — no licensed fare API is connected, and none exists publicly for
        Indian rail or bus.
        {config.llm.enabled
          ? ' Itineraries are written by OpenAI and validated server-side; all costs are recomputed here.'
          : ''}
      </div>
    </div>
  );
}
