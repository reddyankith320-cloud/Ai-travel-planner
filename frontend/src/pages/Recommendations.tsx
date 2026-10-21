/**
 * Recommended destinations.
 *
 * Each card has to answer, without the user digging: can I afford it, how
 * long does it take, why was it chosen, and how much of this is a real price?
 */

import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import {
  distance,
  duration,
  longDate,
  modeIcon,
  modeLabel,
  money,
  percent,
  titleise,
} from '../lib/format';
import type {
  AppConfig,
  BudgetShortfall,
  DestinationRecommendation,
  InterestGap,
  RecommendationResponse,
  TripRequest,
} from '../lib/types';
import { BudgetFitPill } from '../components/BudgetFitPill';
import { ProvenanceBadge } from '../components/ProvenanceBadge';
import { DestinationCover } from '../components/DestinationCover';
import { RouteSketch, MapView, type MapPoint } from '../components/MapView';
import { EmptyState, ErrorState, LoadingPanel } from '../components/states';

/** Horizontal bars for the ranking factors, widest contribution first. */
function ScoreBars({ recommendation }: { recommendation: DestinationRecommendation }) {
  const components = [...recommendation.score.components].sort((a, b) => b.weighted - a.weighted);
  const maxWeight = Math.max(...components.map((c) => c.weight));

  return (
    <div className="stack-sm">
      {components.map((component) => (
        <div key={component.key}>
          <div className="row-between" style={{ gap: 'var(--space-2)' }}>
            <span className="xs" title={component.explanation}>
              {component.label}
            </span>
            <span className="xs subtle tnum">
              {Math.round(component.score * 100)}
              <span aria-hidden="true"> · w{component.weight.toFixed(2)}</span>
            </span>
          </div>
          <div className="meter meter-sm">
            <div
              className="meter-fill"
              style={{
                width: `${component.score * 100}%`,
                // Opacity encodes how much this factor is allowed to matter.
                opacity: 0.35 + 0.65 * (component.weight / maxWeight),
              }}
            />
          </div>
          <p className="xs subtle" style={{ marginTop: 2 }}>
            {component.explanation}
          </p>
        </div>
      ))}
      <p className="xs strong tnum" style={{ marginTop: 'var(--space-2)' }}>
        Overall match: {Math.round(recommendation.score.total * 100)} / 100
      </p>
    </div>
  );
}

function RecommendationCard({
  recommendation,
  originLabel,
  trip,
  onChoose,
}: {
  recommendation: DestinationRecommendation;
  originLabel: string;
  trip: TripRequest;
  onChoose: (recommendation: DestinationRecommendation) => void;
}) {
  const [showScore, setShowScore] = useState(false);
  const [showMap, setShowMap] = useState(false);
  const { destination, cost, outbound, season } = recommendation;

  const mapPoints: MapPoint[] = useMemo(
    () => [
      {
        coordinates: destination.coordinates,
        label: destination.name,
        detail: `${destination.region}, ${destination.state}`,
        kind: 'destination',
      },
      ...recommendation.highlights
        .filter((place) => place.coordinates)
        .map((place, index) => ({
          coordinates: place.coordinates!,
          label: place.name,
          detail: place.category ? titleise(place.category) : undefined,
          kind: 'stop' as const,
          order: index + 1,
        })),
    ],
    [destination, recommendation.highlights],
  );

  return (
    <article className="card card-interactive card-pad stack">
      <DestinationCover destination={destination} />

      {/* Header */}
      <header className="stack-sm">
        <div className="row-between" style={{ alignItems: 'flex-start' }}>
          <div style={{ minWidth: 0 }}>
            <p className="eyebrow">#{recommendation.rank} · best match</p>
            <h2>{destination.name}</h2>
          </div>
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            <p className="figure">{money(cost.total_inr)}</p>
            <p className="xs subtle">
              total · {money(cost.per_traveler_inr)} each
            </p>
          </div>
        </div>
        <div className="row" style={{ gap: 'var(--space-2)' }}>
          <BudgetFitPill
            fit={recommendation.budget_fit}
            utilisation={cost.budget_utilisation}
            remaining={cost.remaining_inr}
            overrun={cost.overrun_inr}
            short
          />
          <span className={`badge ${season.in_season ? 'badge-brand' : 'fit-tight'}`} title={season.note}>
            {season.label}
          </span>
          <ProvenanceBadge provenance={outbound.provenance} />
          {recommendation.is_stretch ? (
            <span className="badge fit-stretch">Over budget by {money(cost.overrun_inr)}</span>
          ) : null}
        </div>
        <p className="small muted">{destination.blurb}</p>
      </header>

      {/* Key figures */}
      <div className="grid grid-4" style={{ gap: 'var(--space-3)' }}>
        <div>
          <p className="figure-label">Getting there</p>
          <p className="small strong tnum">
            <span aria-hidden="true">{modeIcon(outbound.mode)}</span>{' '}
            {money(recommendation.intercity_cost_inr)}
          </p>
          <p className="xs subtle">{modeLabel(outbound.mode)}, return</p>
        </div>
        <div>
          <p className="figure-label">Stay</p>
          <p className="small strong tnum">{money(recommendation.accommodation_cost_inr)}</p>
          <p className="xs subtle">
            {cost.nights} night{cost.nights === 1 ? '' : 's'}
          </p>
        </div>
        <div>
          <p className="figure-label">Travel time</p>
          <p className="small strong tnum">{duration(outbound.duration_hours)}</p>
          <p className="xs subtle">each way · {distance(outbound.distance_km)}</p>
        </div>
        <div>
          <p className="figure-label">Per person/day</p>
          <p className="small strong tnum">{money(cost.per_traveler_per_day_inr)}</p>
          <p className="xs subtle">all in</p>
        </div>
      </div>

      <RouteSketch
        originLabel={originLabel.split(',')[0] ?? originLabel}
        destinationLabel={destination.name}
        distanceKm={outbound.distance_km}
        durationHours={outbound.duration_hours}
        modeIcon={modeIcon(outbound.mode)}
      />

      {/* Why */}
      <div className="stack-sm">
        <h3 className="small strong">Why this one</h3>
        <ul className="stack-sm small muted" style={{ paddingLeft: '1.1rem', margin: 0 }}>
          {recommendation.why_recommended.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      </div>

      {/* Highlights */}
      {recommendation.highlights.length ? (
        <div className="stack-sm">
          <h3 className="small strong">What you'd see</h3>
          <div className="chips">
            {recommendation.highlights.slice(0, 7).map((place) => (
              <span
                key={place.name}
                className="badge badge-neutral"
                title={[
                  titleise(place.category),
                  place.opening_hours ? `Hours (approx.): ${place.opening_hours}` : null,
                  place.entry_fee_inr !== null
                    ? place.entry_fee_inr > 0
                      ? `Entry about ${money(place.entry_fee_inr)} per person`
                      : 'Free entry'
                    : null,
                  place.typical_visit_minutes ? `About ${place.typical_visit_minutes} min` : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              >
                {place.name}
                {place.entry_fee_inr === 0 ? ' · free' : ''}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {/* Caveats */}
      {recommendation.caveats.length ? (
        <div className="notice notice-warn xs">
          <span className="notice-icon" aria-hidden="true">
            ⚠️
          </span>
          <div>
            <strong>Worth knowing</strong>
            <ul style={{ margin: 'var(--space-1) 0 0', paddingLeft: '1.1rem' }}>
              {recommendation.caveats.map((caveat) => (
                <li key={caveat}>{caveat}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}

      {/* Disclosure panels */}
      <div className="row" style={{ gap: 'var(--space-2)' }}>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => setShowScore((value) => !value)}
          aria-expanded={showScore}
        >
          {showScore ? 'Hide' : 'How we scored it'}
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => setShowMap((value) => !value)}
          aria-expanded={showMap}
        >
          {showMap ? 'Hide map' : 'Map preview'}
        </button>
      </div>

      {showScore ? (
        <div className="panel">
          <ScoreBars recommendation={recommendation} />
        </div>
      ) : null}

      {showMap ? <MapView points={mapPoints} height={220} ariaLabel={`Map of ${destination.name}`} /> : null}

      {/* Booking links */}
      {outbound.booking_links.length ? (
        <details>
          <summary className="xs muted" style={{ cursor: 'pointer' }}>
            Where to book the journey
          </summary>
          <ul className="stack-sm xs" style={{ paddingLeft: '1.1rem', marginTop: 'var(--space-2)' }}>
            {outbound.booking_links.map((link) => (
              <li key={link.url}>
                <a href={link.url} target="_blank" rel="noopener noreferrer">
                  {link.label}
                </a>
                {link.official ? (
                  <span className="badge badge-brand" style={{ marginLeft: 6 }}>
                    official
                  </span>
                ) : (
                  <span className="badge badge-neutral" style={{ marginLeft: 6 }}>
                    aggregator
                  </span>
                )}
                {link.note ? <span className="subtle"> — {link.note}</span> : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <div className="row">
        <button
          type="button"
          className="btn btn-primary btn-block"
          onClick={() => onChoose(recommendation)}
        >
          Plan {trip.days} day{trip.days === 1 ? '' : 's'} in {destination.name}{' '}
          <span aria-hidden="true">→</span>
        </button>
      </div>
    </article>
  );
}

/**
 * Shown when the affordable destinations do not cover what the user asked
 * for. Silently serving beaches to someone who asked for mountains reads as
 * the engine ignoring them -- this says what actually happened.
 */
function InterestGapPanel({
  gap,
  onAdjust,
}: {
  gap: InterestGap;
  onAdjust: (extra: number) => void;
}) {
  return (
    <div className="notice notice-warn" role="status">
      <span className="notice-icon" aria-hidden="true">
        🎯
      </span>
      <div className="stack-sm" style={{ width: '100%' }}>
        <strong>We could not match everything you asked for</strong>
        <p className="small">{gap.message}</p>

        {gap.matched.length ? (
          <p className="xs">
            Still covered: {gap.matched.map(titleise).join(', ')}.
          </p>
        ) : null}

        {gap.unmet.some((unmet) => unmet.cheapest_matching_name) ? (
          <ul className="xs stack-sm" style={{ paddingLeft: '1.1rem', margin: 0 }}>
            {gap.unmet
              .filter((unmet) => unmet.cheapest_matching_name)
              .map((unmet) => (
                <li key={unmet.interest}>
                  <strong>{titleise(unmet.interest)}</strong>: closest is{' '}
                  {unmet.cheapest_matching_name} at{' '}
                  {money(unmet.cheapest_matching_total_inr ?? 0)} — needs{' '}
                  {money(unmet.additional_budget_required_inr ?? 0)} more.
                </li>
              ))}
          </ul>
        ) : null}

        {gap.smallest_additional_budget_inr ? (
          <div className="row">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => onAdjust(gap.smallest_additional_budget_inr ?? 0)}
            >
              Search again with {money(gap.smallest_additional_budget_inr)} more
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ShortfallPanel({
  shortfall,
  onAdjust,
}: {
  shortfall: BudgetShortfall;
  onAdjust: (budget: number) => void;
}) {
  return (
    <div className="card card-pad stack">
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <span style={{ fontSize: '1.6rem' }} aria-hidden="true">
          🪙
        </span>
        <div className="stack-sm">
          <h2>Nothing fits that budget — here's the gap</h2>
          <p className="muted small">{shortfall.message}</p>
        </div>
      </div>

      <div className="grid grid-3">
        <div className="panel">
          <p className="figure-label">Cheapest option found</p>
          <p className="figure">{money(shortfall.cheapest_total_inr)}</p>
        </div>
        <div className="panel">
          <p className="figure-label">You'd need</p>
          <p className="figure" style={{ color: 'var(--fit-over)' }}>
            +{money(shortfall.additional_budget_required_inr)}
          </p>
        </div>
        <div className="panel">
          <p className="figure-label">Suggested budget</p>
          <p className="figure">{money(shortfall.suggested_budget_inr)}</p>
        </div>
      </div>

      <div className="stack-sm">
        <h3 className="small strong">What would bring a trip into range</h3>
        <ul className="stack-sm small muted" style={{ paddingLeft: '1.1rem', margin: 0 }}>
          {shortfall.levers.map((lever) => (
            <li key={lever}>{lever}</li>
          ))}
        </ul>
      </div>

      {shortfall.alternatives.length ? (
        <div className="stack-sm">
          <h3 className="small strong">Closest affordable alternatives</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th scope="col" className="figure-label" style={{ textAlign: 'left' }}>
                  Destination
                </th>
                <th scope="col" className="figure-label" style={{ textAlign: 'right' }}>
                  Estimated total
                </th>
                <th scope="col" className="figure-label" style={{ textAlign: 'right' }}>
                  Short by
                </th>
              </tr>
            </thead>
            <tbody>
              {shortfall.alternatives.map((alternative) => (
                <tr key={alternative.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <th scope="row" style={{ textAlign: 'left', fontWeight: 550, padding: '0.4rem 0' }}>
                    <span className="small">{alternative.name}</span>
                  </th>
                  <td className="tnum small" style={{ textAlign: 'right' }}>
                    {alternative.estimated_total_inr ? money(alternative.estimated_total_inr) : '—'}
                  </td>
                  <td className="tnum small" style={{ textAlign: 'right', color: 'var(--fit-over)' }}>
                    {alternative.shortfall_inr ? `+${money(alternative.shortfall_inr)}` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <div className="row">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => onAdjust(shortfall.suggested_budget_inr)}
        >
          Search again with {money(shortfall.suggested_budget_inr)}
        </button>
        <Link className="btn btn-secondary" to="/">
          Change the trip instead
        </Link>
      </div>
    </div>
  );
}

interface Props {
  trip: TripRequest | null;
  config: AppConfig | null;
  onChoose: (recommendation: DestinationRecommendation, trip: TripRequest) => void;
  onTripChange: (trip: TripRequest) => void;
}

export function Recommendations({ trip, config, onChoose, onTripChange }: Props) {
  const navigate = useNavigate();
  const [data, setData] = useState<RecommendationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!trip) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    api
      .recommendations(trip, 5, controller.signal)
      .then((payload) => {
        setData(payload);
        setLoading(false);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(
          caught instanceof ApiError
            ? caught.message
            : 'Something went wrong while searching for destinations.',
        );
        setLoading(false);
      });

    return () => controller.abort();
  }, [trip, attempt]);

  if (!trip) {
    return (
      <EmptyState
        icon="🧭"
        title="No trip to plan yet"
        message="Tell TripWise your budget and starting point, and it will work out where you can go."
      >
        <Link className="btn btn-primary" to="/">
          Start planning
        </Link>
      </EmptyState>
    );
  }

  if (loading) {
    return (
      <LoadingPanel
        title="Working out where you can afford to go…"
        detail={`Costing every destination against ${money(trip.budget_inr)} for ${
          trip.travelers
        } traveller(s) over ${trip.days} days.`}
      />
    );
  }

  if (error) {
    return (
      <ErrorState
        title="Could not load recommendations"
        message={error}
        onRetry={() => setAttempt((value) => value + 1)}
      >
        <Link className="btn btn-ghost" to="/">
          Change the trip
        </Link>
      </ErrorState>
    );
  }

  if (!data) return null;

  const all = [...data.recommendations, ...data.stretch_options];

  return (
    <div className="stack-xl">
      {/* Summary of what was searched */}
      <header className="stack-sm">
        <p className="eyebrow">Step 2 of 3 · Recommended destinations</p>
        <h1>
          {data.recommendations.length
            ? `${data.recommendations.length} place${
                data.recommendations.length === 1 ? '' : 's'
              } you can afford`
            : 'No destination fits that budget'}
        </h1>
        <p className="muted small">
          From <strong>{data.origin_resolved}</strong> · {money(trip.budget_inr)} ·{' '}
          {trip.travelers} traveller{trip.travelers === 1 ? '' : 's'} · {trip.days} days from{' '}
          {longDate(trip.start_date)} · {modeLabel(trip.travel_mode)}
        </p>
        <div className="row" style={{ gap: 'var(--space-2)' }}>
          <span className="badge badge-neutral">
            {data.meta.candidates_feasible} of {data.meta.candidates_considered} destinations
            were workable
          </span>
          <span className="badge badge-neutral">
            searched in {(data.meta.elapsed_ms / 1000).toFixed(1)}s
          </span>
          {data.meta.providers_used.length ? (
            <span className="badge prov-live badge-dot">
              live: {data.meta.providers_used.join(', ')}
            </span>
          ) : (
            <span className="badge prov-estimated">all figures estimated</span>
          )}
        </div>
        <div className="row">
          <Link className="btn btn-secondary btn-sm" to="/">
            Change budget or dates
          </Link>
        </div>
      </header>

      {/* Provider degradation, if any */}
      {data.meta.provider_failures.length ? (
        <div className="notice notice-warn small">
          <span className="notice-icon" aria-hidden="true">
            🔌
          </span>
          <div>
            <strong>Some data sources were unavailable, so TripWise fell back to estimates.</strong>
            <ul style={{ margin: 'var(--space-1) 0 0', paddingLeft: '1.1rem' }}>
              {data.meta.provider_failures.map((failure) => (
                <li key={failure} className="xs">
                  {failure}
                </li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}

      {/* Interests the budget could not cover */}
      {data.interest_gap ? (
        <InterestGapPanel
          gap={data.interest_gap}
          onAdjust={(extra) => {
            onTripChange({ ...trip, budget_inr: Math.ceil((trip.budget_inr + extra) / 500) * 500 });
            setAttempt((value) => value + 1);
          }}
        />
      ) : null}

      {/* Shortfall */}
      {data.shortfall ? (
        <ShortfallPanel
          shortfall={data.shortfall}
          onAdjust={(budget) => {
            onTripChange({ ...trip, budget_inr: budget });
            setAttempt((value) => value + 1);
          }}
        />
      ) : null}

      {/* The cards */}
      {all.length ? (
        <div className="grid grid-2">
          {all.map((recommendation) => (
            <RecommendationCard
              key={recommendation.destination.id}
              recommendation={recommendation}
              originLabel={data.origin_resolved}
              trip={trip}
              onChoose={(chosen) => {
                onChoose(chosen, trip);
                navigate(`/itinerary/${chosen.destination.id}`);
              }}
            />
          ))}
        </div>
      ) : null}

      {/* Ranking transparency */}
      <details className="card card-pad">
        <summary className="strong" style={{ cursor: 'pointer' }}>
          How TripWise ranks destinations
        </summary>
        <div className="stack" style={{ marginTop: 'var(--space-4)' }}>
          <p className="small muted">
            A weighted score, published so you can argue with it. Budget fit rewards using your
            budget <em>well</em> rather than spending the least — a trip that costs 40% of your
            budget usually means a worse trip for no reason.
          </p>
          <div className="stack-sm">
            {Object.entries(data.ranking_weights)
              .sort(([, a], [, b]) => b - a)
              .map(([key, weight]) => (
                <div key={key}>
                  <div className="row-between">
                    <span className="small">{titleise(key)}</span>
                    <span className="xs subtle tnum">{percent(weight)}</span>
                  </div>
                  <div className="meter meter-sm">
                    <div className="meter-fill" style={{ width: `${weight * 100}%` }} />
                  </div>
                </div>
              ))}
          </div>
        </div>
      </details>

      {/* Rejections */}
      {data.rejected.length ? (
        <details className="card card-pad">
          <summary className="strong" style={{ cursor: 'pointer' }}>
            Why {data.rejected.length} other destinations were ruled out
          </summary>
          <ul
            className="stack-sm small muted"
            style={{ paddingLeft: '1.1rem', marginTop: 'var(--space-4)' }}
          >
            {data.rejected.map((rejected) => (
              <li key={rejected.id}>
                <strong>{rejected.name}</strong> — {rejected.reason}
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <p className="xs subtle">{data.meta.data_disclaimer}</p>
      {config?.demo_mode ? null : null}
    </div>
  );
}
