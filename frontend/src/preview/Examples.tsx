/**
 * The example trips offered in the static preview.
 *
 * Shown instead of asking the user to type numbers, because only these exact
 * trips have recorded results behind them.
 */

import { PREVIEW_GENERATED_ON, PREVIEW_SCENARIOS } from './shim';
import { money, modeLabel } from '../lib/format';
import type { TripRequest } from '../lib/types';

export default function PreviewExamples({ onPick }: { onPick: (request: TripRequest) => void }) {
  return (
    <section className="card card-pad stack" aria-label="Example trips">
      <div>
        <p className="eyebrow">Preview</p>
        <h2>Pick an example trip</h2>
        <p className="subtle">
          This is a static preview with no server behind it, so it cannot plan a trip of your
          own. These {PREVIEW_SCENARIOS.length} were worked out by TripWise's real engine
          ({PREVIEW_GENERATED_ON}) and are replayed here. Everything after this click — the
          ranking, the costs, the day-by-day plan, the budget dashboard — is that engine's
          actual output.
        </p>
      </div>

      <div className="stack-sm">
        {PREVIEW_SCENARIOS.map((scenario) => {
          const trip = scenario.request;
          return (
            <button
              key={scenario.id}
              type="button"
              className="card card-interactive card-pad"
              style={{ textAlign: 'left', cursor: 'pointer', width: '100%' }}
              onClick={() => onPick(trip)}
            >
              <span className="strong" style={{ display: 'block' }}>
                {scenario.label}
              </span>
              <span className="small muted tnum" style={{ display: 'block' }}>
                {money(trip.budget_inr)} · {trip.travelers} traveller
                {trip.travelers === 1 ? '' : 's'} · {trip.days} days ·{' '}
                {modeLabel(trip.travel_mode)}
                {trip.interests.length ? ` · ${trip.interests.join(', ').replace(/_/g, ' ')}` : ''}
              </span>
            </button>
          );
        })}
      </div>

      <p className="xs subtle">
        Removing a stop, editing an assumption, saving a trip and typing a city all need the live
        server, so they are switched off here.
      </p>
    </section>
  );
}
