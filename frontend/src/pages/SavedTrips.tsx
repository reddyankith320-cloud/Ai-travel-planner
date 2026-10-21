/** Saved trips: list, reopen and delete. */

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import { longDate, modeLabel, money } from '../lib/format';
import type { TripSummary } from '../lib/types';
import { EmptyState, ErrorState, Spinner } from '../components/states';

export function SavedTrips() {
  const [trips, setTrips] = useState<TripSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setTrips(await api.listTrips());
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not load your saved trips.');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function remove(id: string) {
    setDeleting(id);
    try {
      await api.deleteTrip(id);
      setTrips((current) => (current ?? []).filter((trip) => trip.id !== id));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not delete that trip.');
    } finally {
      setDeleting(null);
    }
  }

  if (error && trips === null) {
    return <ErrorState title="Could not load saved trips" message={error} onRetry={() => void load()} />;
  }

  if (trips === null) {
    return (
      <div className="card card-pad">
        <Spinner label="Loading your saved trips…" />
      </div>
    );
  }

  if (!trips.length) {
    return (
      <EmptyState
        icon="🗂️"
        title="No saved trips yet"
        message="Plan a trip and hit Save, and it will show up here. Trips are tied to this browser — there is no account to create."
      >
        <Link className="btn btn-primary" to="/">
          Plan a trip
        </Link>
      </EmptyState>
    );
  }

  return (
    <div className="stack-lg">
      <header className="stack-sm">
        <p className="eyebrow">Saved</p>
        <h1>Your trips</h1>
        <p className="muted small">
          {trips.length} saved plan{trips.length === 1 ? '' : 's'}. These live in this browser,
          scoped by a local id — clearing site data will clear the list.
        </p>
      </header>

      {error ? (
        <p className="notice notice-error small">
          <span className="notice-icon" aria-hidden="true">
            ⚠️
          </span>
          <span>{error}</span>
        </p>
      ) : null}

      <div className="grid grid-2">
        {trips.map((trip) => {
          const overBudget = trip.total_estimate_inr > trip.budget_inr;
          return (
            <article key={trip.id} className="card card-interactive card-pad stack">
              <div className="row-between" style={{ alignItems: 'flex-start' }}>
                <div style={{ minWidth: 0 }}>
                  <p className="eyebrow">{trip.destination_name}</p>
                  <h2 style={{ fontSize: 'var(--text-lg)' }}>{trip.title}</h2>
                  <p className="xs subtle">
                    {longDate(trip.start_date)} · {trip.days} days · {trip.travelers} traveller
                    {trip.travelers === 1 ? '' : 's'} · {modeLabel(trip.travel_mode)}
                  </p>
                </div>
                <div style={{ textAlign: 'right', flexShrink: 0 }}>
                  <p className="figure" style={{ fontSize: 'var(--text-xl)' }}>
                    {money(trip.total_estimate_inr)}
                  </p>
                  <p className="xs subtle">{money(trip.per_traveler_inr)} each</p>
                </div>
              </div>

              <div className="row">
                <span className={`badge ${overBudget ? 'fit-stretch' : 'fit-good'}`}>
                  {overBudget ? 'Over' : 'Within'} the {money(trip.budget_inr)} budget
                </span>
                <span className="badge badge-neutral">
                  saved {new Date(trip.created_at).toLocaleDateString('en-IN')}
                </span>
              </div>

              {trip.notes ? <p className="small muted">{trip.notes}</p> : null}

              <div className="row">
                <Link className="btn btn-secondary btn-sm" to={`/itinerary/${trip.destination_id}`}>
                  Re-plan with current rates
                </Link>
                <button
                  type="button"
                  className="btn btn-danger btn-sm"
                  onClick={() => void remove(trip.id)}
                  disabled={deleting === trip.id}
                >
                  {deleting === trip.id ? <Spinner /> : 'Delete'}
                </button>
              </div>
              <p className="xs subtle">
                Re-planning recalculates against today's rates, so the total may differ from the
                one saved.
              </p>
            </article>
          );
        })}
      </div>
    </div>
  );
}
