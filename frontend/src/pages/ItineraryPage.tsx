/**
 * Day-by-day itinerary, budget dashboard, and plan editing.
 *
 * Editing anything -- removing a stop, changing an assumption -- re-requests
 * the plan from the server, because the server owns the arithmetic. That is
 * what keeps the itinerary and the budget dashboard in step.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import {
  dayOfWeek,
  distance,
  longDate,
  minutes as formatMinutes,
  money,
  shortDate,
  titleise,
} from '../lib/format';
import type {
  AppConfig,
  Itinerary,
  ItineraryActivity,
  ItineraryDay,
  TripRequest,
} from '../lib/types';
import { CostBreakdownPanel } from '../components/CostBreakdown';
import { MapView, type MapPoint } from '../components/MapView';
import { ProvenanceBadge } from '../components/ProvenanceBadge';
import { EmptyState, ErrorState, LoadingPanel, Spinner } from '../components/states';

const KIND_ICON: Record<string, string> = {
  sightseeing: '📍',
  activity: '🎟️',
  meal: '🍽️',
  rest: '☕',
  transit: '🚕',
  check_in: '🛎️',
  check_out: '🧳',
  free_time: '🌤️',
};

function ActivityRow({
  activity,
  travelers,
  onRemove,
  removing,
}: {
  activity: ItineraryActivity;
  travelers: number;
  onRemove?: (name: string) => void;
  removing: boolean;
}) {
  const removable = Boolean(activity.place && onRemove);

  return (
    <li
      className="panel"
      style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'flex-start' }}
    >
      <div style={{ flexShrink: 0, textAlign: 'center', minWidth: 54 }}>
        <p className="xs strong tnum">{activity.start_time}</p>
        <p className="xs subtle tnum">{activity.end_time}</p>
        <p style={{ fontSize: '1.1rem', marginTop: 2 }} aria-hidden="true">
          {KIND_ICON[activity.kind] ?? '•'}
        </p>
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="row-between" style={{ alignItems: 'flex-start' }}>
          <div style={{ minWidth: 0 }}>
            <p className="small strong">{activity.title}</p>
            <p className="xs subtle">
              {formatMinutes(activity.duration_minutes)}
              {activity.distance_from_previous_km !== null
                ? ` · ${distance(activity.distance_from_previous_km)} from the last stop`
                : ''}
              {activity.place?.category ? ` · ${titleise(activity.place.category)}` : ''}
            </p>
          </div>
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            {activity.block_cost_inr > 0 ? (
              <p className="small tnum strong">{money(activity.block_cost_inr)}</p>
            ) : (
              <p className="xs subtle">free</p>
            )}
            {activity.place ? <ProvenanceBadge provenance={activity.place.provenance} compact /> : null}
          </div>
        </div>

        <p className="xs muted" style={{ marginTop: 'var(--space-1)' }}>
          {activity.description}
        </p>

        {/* Cost split, so the day's total is traceable */}
        {activity.block_cost_inr > 0 ? (
          <p className="xs subtle tnum">
            {activity.entry_cost_inr > 0
              ? `Entry ${money(activity.entry_cost_inr)} for ${travelers} · `
              : ''}
            {activity.transport_cost_inr > 0
              ? `Transport ${money(activity.transport_cost_inr)} · `
              : ''}
            {activity.food_cost_inr > 0 ? `Food ${money(activity.food_cost_inr)}` : ''}
          </p>
        ) : null}

        {activity.place?.opening_hours ? (
          <p className="xs subtle">Hours (approx.): {activity.place.opening_hours}</p>
        ) : null}

        {activity.transport_note ? (
          <p className="xs subtle">🚕 {activity.transport_note}</p>
        ) : null}

        {activity.weather_alternative ? (
          <p className="xs" style={{ color: 'var(--prov-live)' }}>
            ☔ {activity.weather_alternative}
          </p>
        ) : null}

        {activity.tips.length ? (
          <ul className="xs subtle" style={{ margin: 'var(--space-1) 0 0', paddingLeft: '1.1rem' }}>
            {activity.tips.map((tip) => (
              <li key={tip}>{tip}</li>
            ))}
          </ul>
        ) : null}

        {activity.booking_required ? (
          <p className="xs" style={{ color: 'var(--accent)' }}>
            ⚠️ Book this ahead — it usually sells out or needs a permit.
          </p>
        ) : null}

        {removable ? (
          <button
            type="button"
            className="btn btn-ghost btn-sm no-print"
            style={{ marginTop: 'var(--space-2)' }}
            disabled={removing}
            onClick={() => onRemove?.(activity.title)}
          >
            {removing ? <Spinner /> : '✕'} Remove and re-plan
          </button>
        ) : null}
      </div>
    </li>
  );
}

function DayPanel({
  day,
  travelers,
  onRemove,
  removing,
}: {
  day: ItineraryDay;
  travelers: number;
  onRemove: (name: string) => void;
  removing: boolean;
}) {
  const points: MapPoint[] = day.activities
    .filter((activity) => activity.place?.coordinates)
    .map((activity, index) => ({
      coordinates: activity.place!.coordinates!,
      label: activity.place!.name,
      detail: `${activity.start_time}–${activity.end_time}`,
      kind: 'stop' as const,
      order: index + 1,
    }));

  return (
    <section className="card card-pad stack" style={{ breakInside: 'avoid' }}>
      <header className="stack-sm">
        <div className="row-between" style={{ alignItems: 'flex-start' }}>
          <div style={{ minWidth: 0 }}>
            <p className="eyebrow">
              Day {day.day_number} · {dayOfWeek(day.date)} {shortDate(day.date)}
            </p>
            <h2>{day.title}</h2>
          </div>
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            <p className="small strong tnum">{money(day.day_cost_inr)}</p>
            <p className="xs subtle">spend today</p>
          </div>
        </div>
        <p className="small muted">{day.summary}</p>
        <div className="row" style={{ gap: 'var(--space-2)' }}>
          <span className="badge badge-neutral">📍 {day.area_focus}</span>
          <span className="badge badge-neutral">{distance(day.total_travel_km)} on the move</span>
          <span className="badge badge-neutral">{day.walking_intensity} walking</span>
        </div>
        {day.notes.length ? (
          <p className="xs subtle">{day.notes.join(' ')}</p>
        ) : null}
      </header>

      {points.length > 1 ? (
        <MapView
          points={points}
          connect
          height={200}
          ariaLabel={`Map of day ${day.day_number}`}
        />
      ) : null}

      <ol className="stack-sm" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
        {day.activities.map((activity) => (
          <ActivityRow
            key={`${activity.order}-${activity.title}`}
            activity={activity}
            travelers={travelers}
            onRemove={onRemove}
            removing={removing}
          />
        ))}
      </ol>
    </section>
  );
}

interface Props {
  trip: TripRequest | null;
  config: AppConfig | null;
}

export function ItineraryPage({ trip, config }: Props) {
  const { destinationId } = useParams<{ destinationId: string }>();
  const [itinerary, setItinerary] = useState<Itinerary | null>(null);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [overrides, setOverrides] = useState<Record<string, number>>({});
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [tab, setTab] = useState<'plan' | 'budget'>('plan');

  const effectiveTrip = useMemo<TripRequest | null>(
    () => (trip ? { ...trip, assumption_overrides: overrides } : null),
    [trip, overrides],
  );

  const load = useCallback(
    async (isEdit: boolean) => {
      if (!destinationId || !effectiveTrip) return;
      if (isEdit) setEditing(true);
      else setLoading(true);
      setError(null);
      try {
        const payload = await api.itinerary(destinationId, effectiveTrip, excluded);
        setItinerary(payload);
      } catch (caught) {
        setError(
          caught instanceof ApiError
            ? caught.message
            : 'Could not build an itinerary for this destination.',
        );
      } finally {
        setLoading(false);
        setEditing(false);
      }
    },
    [destinationId, effectiveTrip, excluded],
  );

  useEffect(() => {
    void load(itinerary !== null);
    // `load` already closes over every real dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [destinationId, excluded, overrides]);

  const mapPoints = useMemo<MapPoint[]>(() => {
    if (!itinerary) return [];
    const seen = new Set<string>();
    const points: MapPoint[] = [];
    let order = 1;
    for (const day of itinerary.days) {
      for (const activity of day.activities) {
        const place = activity.place;
        if (place?.coordinates && !seen.has(place.name)) {
          seen.add(place.name);
          points.push({
            coordinates: place.coordinates,
            label: place.name,
            detail: `Day ${day.day_number} · ${activity.start_time}`,
            kind: 'stop',
            order: order++,
          });
        }
      }
    }
    return points;
  }, [itinerary]);

  async function handleSave() {
    if (!itinerary || !effectiveTrip) return;
    setSaveState('saving');
    setSaveMessage(null);
    try {
      await api.saveTrip({
        title: `${itinerary.destination.name} · ${itinerary.days.length} days`,
        destination_id: itinerary.destination.id,
        destination_name: itinerary.destination.name,
        trip: effectiveTrip,
        itinerary,
      });
      setSaveState('saved');
      setSaveMessage('Saved. You can find it under Saved trips.');
    } catch (caught) {
      setSaveState('error');
      setSaveMessage(
        caught instanceof ApiError ? caught.message : 'Could not save this trip.',
      );
    }
  }

  function handleExport() {
    if (!itinerary) return;
    const payload = {
      exported_at: new Date().toISOString(),
      exported_by: 'TripWise',
      note:
        'Costs are estimates from TripWise unless a line item is marked live. ' +
        'Confirm fares, room rates and admission charges before booking.',
      trip: effectiveTrip,
      itinerary,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `tripwise-${itinerary.destination.id}-${itinerary.start_date}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  if (!trip) {
    return (
      <EmptyState
        icon="🧭"
        title="No trip in progress"
        message="Start from the planner so TripWise knows your budget and starting point."
      >
        <Link className="btn btn-primary" to="/">
          Start planning
        </Link>
      </EmptyState>
    );
  }

  if (loading) {
    return <LoadingPanel title="Building your day-by-day plan…" detail="Grouping activities by area and costing each day." />;
  }

  if (error) {
    return (
      <ErrorState title="Could not build the itinerary" message={error} onRetry={() => void load(false)}>
        <Link className="btn btn-ghost" to="/recommendations">
          Back to recommendations
        </Link>
      </ErrorState>
    );
  }

  if (!itinerary) return null;

  const { destination, cost } = itinerary;

  return (
    <div className="stack-xl">
      {/* Header */}
      <header className="stack">
        <Link className="btn btn-ghost btn-sm no-print" to="/recommendations" style={{ alignSelf: 'flex-start' }}>
          <span aria-hidden="true">←</span> Back to recommendations
        </Link>
        <div className="row-between" style={{ alignItems: 'flex-start' }}>
          <div style={{ minWidth: 0 }}>
            <p className="eyebrow">
              Step 3 of 3 · {destination.region}, {destination.state}
            </p>
            <h1>{destination.name}</h1>
            <p className="muted small">
              {longDate(itinerary.start_date)} – {longDate(itinerary.end_date)} ·{' '}
              {itinerary.travelers} traveller{itinerary.travelers === 1 ? '' : 's'} ·{' '}
              {itinerary.activity_count} planned blocks
            </p>
          </div>
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            <p className="figure-lg">{money(cost.total_inr)}</p>
            <p className="xs subtle">{money(cost.per_traveler_inr)} per person</p>
          </div>
        </div>

        <div className="row" style={{ gap: 'var(--space-2)' }}>
          <span className="badge badge-neutral">
            {itinerary.generated_by.startsWith('llm')
              ? `AI-written plan (${itinerary.generated_by.replace('llm:', '')})`
              : 'Deterministic plan'}
          </span>
          <ProvenanceBadge provenance={itinerary.provenance} showConfidence />
        </div>

        <div className="row no-print">
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleSave}
            disabled={saveState === 'saving'}
          >
            {saveState === 'saving' ? <Spinner /> : <span aria-hidden="true">💾</span>}
            {saveState === 'saved' ? 'Saved' : 'Save this trip'}
          </button>
          <button type="button" className="btn btn-secondary" onClick={handleExport}>
            <span aria-hidden="true">⬇</span> Export JSON
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => window.print()}>
            <span aria-hidden="true">🖨</span> Print / PDF
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => void load(true)}
            disabled={editing}
          >
            {editing ? <Spinner /> : '↻'} Regenerate
          </button>
        </div>

        {saveMessage ? (
          <p className={`notice ${saveState === 'error' ? 'notice-error' : 'notice-brand'} small`}>
            <span className="notice-icon" aria-hidden="true">
              {saveState === 'error' ? '⚠️' : '✓'}
            </span>
            <span>
              {saveMessage}{' '}
              {saveState === 'saved' ? <Link to="/trips">View saved trips</Link> : null}
            </span>
          </p>
        ) : null}
      </header>

      {/* Edits in effect */}
      {excluded.length || Object.keys(overrides).length ? (
        <div className="notice notice-brand small no-print">
          <span className="notice-icon" aria-hidden="true">
            ✎
          </span>
          <div className="stack-sm" style={{ width: '100%' }}>
            <strong>Your edits are applied, and the budget has been recalculated.</strong>
            {excluded.length ? (
              <p className="xs">Removed: {excluded.join(', ')}</p>
            ) : null}
            {Object.keys(overrides).length ? (
              <p className="xs">
                Changed assumptions:{' '}
                {Object.entries(overrides)
                  .map(([key, value]) => `${titleise(key)} = ${value}`)
                  .join(', ')}
              </p>
            ) : null}
            <div className="row">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => {
                  setExcluded([]);
                  setOverrides({});
                }}
              >
                Undo all edits
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {/* Overview map */}
      {mapPoints.length ? (
        <MapView
          points={[
            {
              coordinates: destination.coordinates,
              label: `${destination.name} (your base)`,
              detail: itinerary.accommodation_area,
              kind: 'destination',
            },
            ...mapPoints,
          ]}
          height={300}
          ariaLabel={`Map of every stop in ${destination.name}`}
        />
      ) : null}

      {/* Tabs */}
      <div className="segmented no-print" role="tablist" aria-label="Itinerary view">
        <button
          type="button"
          role="tab"
          className="segmented-option"
          aria-pressed={tab === 'plan'}
          aria-selected={tab === 'plan'}
          onClick={() => setTab('plan')}
        >
          Day-by-day plan
        </button>
        <button
          type="button"
          role="tab"
          className="segmented-option"
          aria-pressed={tab === 'budget'}
          aria-selected={tab === 'budget'}
          onClick={() => setTab('budget')}
        >
          Budget dashboard
        </button>
      </div>

      {tab === 'plan' ? (
        <div className="stack-lg">
          {itinerary.days.map((day) => (
            <DayPanel
              key={day.day_number}
              day={day}
              travelers={itinerary.travelers}
              removing={editing}
              onRemove={(name) =>
                setExcluded((current) =>
                  current.includes(name) ? current : [...current, name],
                )
              }
            />
          ))}

          {/* Practical notes */}
          <section className="card card-pad stack">
            <h2>Before you go</h2>
            <div className="grid grid-2">
              <div className="stack-sm">
                <h3 className="small strong">Packing</h3>
                <ul className="small muted stack-sm" style={{ paddingLeft: '1.1rem', margin: 0 }}>
                  {itinerary.packing_notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              </div>
              <div className="stack-sm">
                <h3 className="small strong">Getting around</h3>
                <ul className="small muted stack-sm" style={{ paddingLeft: '1.1rem', margin: 0 }}>
                  {itinerary.local_tips.map((tip) => (
                    <li key={tip}>{tip}</li>
                  ))}
                </ul>
              </div>
            </div>
          </section>
        </div>
      ) : (
        <div className="card card-pad">
          <CostBreakdownPanel
            cost={cost}
            onAssumptionChange={(key, value) =>
              setOverrides((current) => ({ ...current, [key]: value }))
            }
          />
        </div>
      )}

      <p className="xs subtle">{itinerary.disclaimer}</p>
      {config?.demo_mode ? (
        <p className="xs subtle">
          Running in demo mode: no provider was contacted for any price on this page.
        </p>
      ) : null}
    </div>
  );
}
