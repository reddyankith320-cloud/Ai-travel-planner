/** App shell: header, routing and the trip state shared across pages. */

import { useEffect, useState } from 'react';
import { Link, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { api } from './lib/api';
import type { AppConfig, DestinationRecommendation, TripRequest } from './lib/types';
import { DemoModeBanner } from './components/DemoModeBanner';
import { TripSetup } from './pages/TripSetup';
import { Recommendations } from './pages/Recommendations';
import { ItineraryPage } from './pages/ItineraryPage';
import { SavedTrips } from './pages/SavedTrips';
import { EmptyState } from './components/states';

function Header() {
  return (
    <header
      className="no-print"
      style={{
        borderBottom: '1px solid var(--border)',
        background: 'var(--surface)',
        position: 'sticky',
        top: 0,
        zIndex: 40,
      }}
    >
      <div
        className="page row-between"
        style={{ paddingBlock: 'var(--space-3)', flexWrap: 'nowrap' }}
      >
        <Link
          to="/"
          className="row"
          style={{ gap: 'var(--space-2)', textDecoration: 'none', color: 'var(--text)', flexWrap: 'nowrap' }}
        >
          <span style={{ fontSize: '1.3rem' }} aria-hidden="true">
            🧭
          </span>
          <span>
            <strong style={{ letterSpacing: '-0.02em', fontSize: 'var(--text-md)' }}>TripWise</strong>
            <span className="xs subtle" style={{ display: 'block', lineHeight: 1.1 }}>
              Budget-first planning
            </span>
          </span>
        </Link>

        <nav className="row" style={{ gap: 'var(--space-1)', flexWrap: 'nowrap' }}>
          {[
            { to: '/', label: 'Plan' },
            { to: '/trips', label: 'Saved' },
          ].map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) => `btn btn-sm ${isActive ? 'btn-secondary' : 'btn-ghost'}`}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>
  );
}

function Footer({ config }: { config: AppConfig | null }) {
  return (
    <footer
      className="no-print"
      style={{
        borderTop: '1px solid var(--border)',
        marginTop: 'var(--space-16)',
        paddingBlock: 'var(--space-8)',
      }}
    >
      <div className="page stack-sm">
        <p className="xs subtle">
          TripWise does not sell tickets and holds no inventory. Prices shown are estimates from
          our own cost model unless a figure is explicitly marked as live. There is no public API
          for Indian rail or intercity bus fares, so those are always estimated and we link to
          the official booking channel instead.
        </p>
        <p className="xs subtle">
          Maps and place data &copy;{' '}
          <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">
            OpenStreetMap
          </a>{' '}
          contributors.
          {config ? ` TripWise v${config.version} · dataset ${config.dataset_version}` : ''}
        </p>
      </div>
    </footer>
  );
}

/** Step indicator, so the three-stage flow is legible. */
function Progress() {
  const { pathname } = useLocation();
  const step = pathname.startsWith('/itinerary') ? 3 : pathname.startsWith('/recommendations') ? 2 : 1;
  if (pathname.startsWith('/trips')) return null;

  const steps = ['Your trip', 'Where you can go', 'Your plan'];

  return (
    <ol
      className="row no-print"
      style={{ listStyle: 'none', padding: 0, margin: 0, gap: 'var(--space-2)' }}
      aria-label="Planning progress"
    >
      {steps.map((label, index) => {
        const position = index + 1;
        const state = position < step ? 'done' : position === step ? 'current' : 'upcoming';
        return (
          <li key={label} className="row" style={{ gap: 'var(--space-2)', flexWrap: 'nowrap' }}>
            <span
              aria-current={state === 'current' ? 'step' : undefined}
              className={`badge ${state === 'upcoming' ? 'badge-neutral' : 'badge-brand'}`}
            >
              {state === 'done' ? '✓' : position}. {label}
            </span>
            {position < steps.length ? (
              <span className="xs subtle" aria-hidden="true">
                →
              </span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [trip, setTrip] = useState<TripRequest | null>(null);
  const [, setChosen] = useState<DestinationRecommendation | null>(null);

  useEffect(() => {
    // Capability discovery. If this fails the app still works; the banner is
    // simply omitted rather than blocking the planner.
    api.config().then(setConfig).catch(() => setConfig(null));
  }, []);

  return (
    <>
      <Header />
      <main className="page stack-lg" style={{ paddingBlock: 'var(--space-6)' }}>
        <Progress />
        <DemoModeBanner config={config} />
        <Routes>
          <Route path="/" element={<TripSetup config={config} onSubmit={setTrip} />} />
          <Route
            path="/recommendations"
            element={
              <Recommendations
                trip={trip}
                config={config}
                onChoose={(recommendation, usedTrip) => {
                  setChosen(recommendation);
                  setTrip(usedTrip);
                }}
                onTripChange={setTrip}
              />
            }
          />
          <Route
            path="/itinerary/:destinationId"
            element={<ItineraryPage trip={trip} config={config} />}
          />
          <Route path="/trips" element={<SavedTrips />} />
          <Route
            path="*"
            element={
              <EmptyState icon="🗺️" title="Page not found" message="That route does not exist.">
                <Link className="btn btn-primary" to="/">
                  Back to planning
                </Link>
              </EmptyState>
            }
          />
        </Routes>
      </main>
      <Footer config={config} />
    </>
  );
}
