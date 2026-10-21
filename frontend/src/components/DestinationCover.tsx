/**
 * The visual for a destination card.
 *
 * When a licensed photograph is available it is shown, with its attribution.
 * When it is not -- which is the normal case in demo mode, and whenever the
 * image lookup is ambiguous -- we draw a cover instead of showing a stock
 * photo of somewhere else. A generated cover is honest; a wrong photograph is
 * not, and a grey box is just worse design.
 *
 * The cover is deterministic: the same destination always gets the same
 * artwork, derived from its id and its tags.
 */

import { useState } from 'react';
import type { DestinationSummary } from '../lib/types';

/** Stable hash so a destination's colours never change between sessions. */
function hash(value: string): number {
  let result = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    result ^= value.charCodeAt(i);
    result = Math.imul(result, 16777619);
  }
  return Math.abs(result);
}

/** The dominant tag decides the motif, so beaches never look like deserts. */
const MOTIF: Record<string, { glyph: string; hue: number }> = {
  beaches: { glyph: '🏖️', hue: 194 },
  backwaters: { glyph: '🛶', hue: 170 },
  mountains: { glyph: '🏔️', hue: 215 },
  hill_station: { glyph: '⛰️', hue: 150 },
  trekking: { glyph: '🥾', hue: 140 },
  heritage: { glyph: '🏛️', hue: 32 },
  art_culture: { glyph: '🎭', hue: 280 },
  spiritual: { glyph: '🛕', hue: 42 },
  wildlife: { glyph: '🐅', hue: 95 },
  nature: { glyph: '🌿', hue: 128 },
  waterfalls: { glyph: '💦', hue: 186 },
  lakes: { glyph: '🏞️', hue: 200 },
  desert: { glyph: '🏜️', hue: 28 },
  adventure: { glyph: '🪂', hue: 10 },
  food: { glyph: '🍛', hue: 20 },
  nightlife: { glyph: '🌃', hue: 265 },
  shopping: { glyph: '🛍️', hue: 320 },
  wellness: { glyph: '🧘', hue: 160 },
  offbeat: { glyph: '🧭', hue: 250 },
  photography: { glyph: '📷', hue: 230 },
};

function motifFor(tags: string[], id: string) {
  for (const tag of tags) {
    const motif = MOTIF[tag];
    if (motif) return motif;
  }
  // Nothing recognised: derive a hue from the id so it is still stable.
  return { glyph: '🧭', hue: hash(id) % 360 };
}

interface Props {
  destination: DestinationSummary;
  height?: number;
}

export function DestinationCover({ destination, height = 132 }: Props) {
  const [failed, setFailed] = useState(false);
  const { glyph, hue } = motifFor(destination.tags, destination.id);
  const shift = (hash(destination.id) % 40) - 20;

  const showPhoto = Boolean(destination.image_url) && !failed;

  return (
    <figure
      style={{
        margin: 0,
        height,
        borderRadius: 'var(--radius)',
        overflow: 'hidden',
        position: 'relative',
        background: `linear-gradient(135deg,
          hsl(${hue} 58% 42%) 0%,
          hsl(${(hue + shift + 360) % 360} 62% 33%) 100%)`,
        border: '1px solid var(--border)',
      }}
    >
      {showPhoto ? (
        <img
          src={destination.image_url!}
          alt={`${destination.name}, ${destination.state}`}
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
        />
      ) : (
        <div
          aria-hidden="true"
          style={{
            width: '100%',
            height: '100%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: Math.round(height * 0.34),
            // A soft radial highlight keeps the flat gradient from looking dead.
            backgroundImage:
              'radial-gradient(circle at 70% 25%, rgba(255,255,255,.22), transparent 55%)',
          }}
        >
          {glyph}
        </div>
      )}

      {/* Name plate, legible over both a photo and a gradient. */}
      <figcaption
        style={{
          position: 'absolute',
          insetInline: 0,
          bottom: 0,
          padding: '1.6rem 0.75rem 0.5rem',
          background: 'linear-gradient(to top, rgba(2,6,23,.82), transparent)',
          color: '#fff',
        }}
      >
        <span style={{ fontWeight: 650, fontSize: 'var(--text-md)', letterSpacing: '-0.01em' }}>
          {destination.name}
        </span>
        <span style={{ display: 'block', fontSize: 'var(--text-xs)', opacity: 0.85 }}>
          {destination.region}, {destination.state}
        </span>
      </figcaption>

      {/* Licence credit, required for Wikimedia images. */}
      {showPhoto && destination.image_attribution ? (
        <span
          title={destination.image_attribution}
          style={{
            position: 'absolute',
            top: 6,
            right: 6,
            fontSize: 10,
            padding: '2px 6px',
            borderRadius: 'var(--radius-full)',
            background: 'rgba(2,6,23,.6)',
            color: '#fff',
          }}
        >
          © Wikimedia
        </span>
      ) : (
        <span
          title="TripWise does not have a licensed photograph of this place, so this is generated artwork rather than a stock photo of somewhere else."
          style={{
            position: 'absolute',
            top: 6,
            right: 6,
            fontSize: 10,
            padding: '2px 6px',
            borderRadius: 'var(--radius-full)',
            background: 'rgba(2,6,23,.45)',
            color: '#fff',
          }}
        >
          illustration
        </span>
      )}
    </figure>
  );
}
