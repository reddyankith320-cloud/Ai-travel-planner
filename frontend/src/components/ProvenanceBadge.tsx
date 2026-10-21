/**
 * The label that keeps TripWise honest.
 *
 * Every figure the user sees carries one of these, so "live quote",
 * "our estimate" and "from our dataset" can never be confused. The tooltip
 * carries the provider's own note verbatim.
 */

import type { Confidence, Provenance } from '../lib/types';

const LABELS: Record<Provenance['source'], string> = {
  live: 'Live',
  cached: 'Live (cached)',
  estimated: 'Estimate',
  curated: 'Reference data',
  sample: 'Sample',
};

const DESCRIPTIONS: Record<Provenance['source'], string> = {
  live: 'Fetched from a provider just now.',
  cached: 'A provider quote from earlier, still within its cache window.',
  estimated: "Computed by TripWise's cost model. Not a quote.",
  curated: "From TripWise's maintained dataset. Approximate.",
  sample: 'Illustrative only.',
};

const CONFIDENCE_NOTE: Record<Confidence, string> = {
  high: 'High confidence.',
  medium: 'Moderate confidence.',
  low: 'Low confidence — verify before relying on it.',
};

interface Props {
  provenance: Provenance;
  showConfidence?: boolean;
  compact?: boolean;
}

export function ProvenanceBadge({ provenance, showConfidence = false, compact = false }: Props) {
  const { source, confidence, provider, note } = provenance;

  const tooltip = [
    DESCRIPTIONS[source],
    provider ? `Source: ${provider}.` : null,
    showConfidence ? CONFIDENCE_NOTE[confidence] : null,
    note,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <span
      className={`badge prov-${source} ${source === 'live' ? 'badge-dot' : ''}`}
      title={tooltip}
    >
      {compact ? LABELS[source].split(' ')[0] : LABELS[source]}
      {showConfidence && confidence === 'low' && !compact ? ' · low' : ''}
    </span>
  );
}

/** A short, plain-language summary of how fresh a whole estimate is. */
export function FreshnessNote({ note, liveShare }: { note: string; liveShare: number }) {
  const isLive = liveShare > 0;
  return (
    <p className={`notice ${isLive ? 'notice-info' : 'notice-warn'} xs`}>
      <span className="notice-icon" aria-hidden="true">
        {isLive ? '🛰️' : '📐'}
      </span>
      <span>{note}</span>
    </p>
  );
}
