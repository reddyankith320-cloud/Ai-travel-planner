/** Loading, empty and error states. */

import type { ReactNode } from 'react';

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row" style={{ gap: 'var(--space-2)' }}>
      <span className="spinner" aria-hidden="true" />
      {label ? <span className="small muted">{label}</span> : null}
      <span className="visually-hidden">Loading</span>
    </span>
  );
}

export function CardSkeleton() {
  return (
    <div className="card card-pad stack" aria-hidden="true">
      <div className="skeleton" style={{ height: 14, width: '35%' }} />
      <div className="skeleton" style={{ height: 26, width: '62%' }} />
      <div className="skeleton" style={{ height: 10, width: '100%' }} />
      <div className="skeleton" style={{ height: 10, width: '88%' }} />
      <div className="skeleton" style={{ height: 76, width: '100%' }} />
    </div>
  );
}

export function LoadingPanel({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="stack-lg">
      <div className="card card-pad">
        <div className="row">
          <span className="spinner" style={{ fontSize: '1.2rem' }} aria-hidden="true" />
          <div>
            <p className="strong">{title}</p>
            {detail ? <p className="subtle">{detail}</p> : null}
          </div>
        </div>
      </div>
      <div className="grid grid-2">
        <CardSkeleton />
        <CardSkeleton />
      </div>
    </div>
  );
}

interface ErrorStateProps {
  title: string;
  message: string;
  onRetry?: () => void;
  children?: ReactNode;
}

export function ErrorState({ title, message, onRetry, children }: ErrorStateProps) {
  return (
    <div className="card card-pad stack" role="alert">
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <span style={{ fontSize: '1.4rem' }} aria-hidden="true">
          ⚠️
        </span>
        <div className="stack-sm">
          <h3>{title}</h3>
          {/* Server messages can be multi-line (validation lists). */}
          <p className="muted small" style={{ whiteSpace: 'pre-line' }}>
            {message}
          </p>
        </div>
      </div>
      {children}
      {onRetry ? (
        <div>
          <button type="button" className="btn btn-secondary" onClick={onRetry}>
            Try again
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  message,
  children,
}: {
  icon: string;
  title: string;
  message: string;
  children?: ReactNode;
}) {
  return (
    <div className="card card-pad stack" style={{ textAlign: 'center' }}>
      <div style={{ fontSize: '2.4rem' }} aria-hidden="true">
        {icon}
      </div>
      <h3>{title}</h3>
      <p className="muted small" style={{ maxWidth: '46ch', marginInline: 'auto' }}>
        {message}
      </p>
      {children}
    </div>
  );
}
