/**
 * The budget dashboard.
 *
 * Everything here is derived from the same `categories` array the backend
 * computes from its line items, so the bars, the per-head figures and the
 * headline total cannot disagree. The footer asserts that explicitly.
 */

import { useState } from 'react';
import type { CostBreakdown as Breakdown, CostCategory } from '../lib/types';
import { categoryLabel, money, moneyPrecise, percent } from '../lib/format';
import { ProvenanceBadge, FreshnessNote } from './ProvenanceBadge';
import { BudgetFitPill } from './BudgetFitPill';

const CATEGORY_COLOUR: Record<CostCategory, string> = {
  intercity_transport: 'var(--cat-transport)',
  accommodation: 'var(--cat-accommodation)',
  food: 'var(--cat-food)',
  local_transport: 'var(--cat-local)',
  activities: 'var(--cat-activities)',
  taxes_and_fees: 'var(--cat-taxes)',
  contingency: 'var(--cat-contingency)',
};

function Figure({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <p className="figure-label">{label}</p>
      <p className="figure" title={hint}>
        {value}
      </p>
      {hint ? <p className="xs subtle">{hint}</p> : null}
    </div>
  );
}

/** Stacked proportional bar of the whole budget. */
function CategoryBar({ cost }: { cost: Breakdown }) {
  return (
    <div
      className="row"
      style={{
        gap: 2,
        height: 14,
        borderRadius: 'var(--radius-full)',
        overflow: 'hidden',
        flexWrap: 'nowrap',
        border: '1px solid var(--border)',
      }}
      role="img"
      aria-label={cost.categories
        .map((c) => `${categoryLabel(c.category)} ${percent(c.share_of_total)}`)
        .join(', ')}
    >
      {cost.categories.map((category) => (
        <span
          key={category.category}
          title={`${categoryLabel(category.category)}: ${money(category.total_inr)} (${percent(
            category.share_of_total,
          )})`}
          style={{
            width: `${Math.max(1, category.share_of_total * 100)}%`,
            background: CATEGORY_COLOUR[category.category],
            height: '100%',
            flexShrink: 0,
          }}
        />
      ))}
    </div>
  );
}

export function CostBreakdownPanel({
  cost,
  showLineItems = true,
  showAssumptions = true,
  onAssumptionChange,
}: {
  cost: Breakdown;
  showLineItems?: boolean;
  showAssumptions?: boolean;
  onAssumptionChange?: (key: string, value: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const categorySum = cost.categories.reduce((total, c) => total + c.total_inr, 0);
  const reconciles = Math.abs(categorySum - cost.total_inr) < 0.05;

  return (
    <section className="stack-lg" aria-label="Budget breakdown">
      {/* Headline figures */}
      <div className="grid grid-4">
        <Figure
          label="Total for the group"
          value={money(cost.total_inr)}
          hint={`${cost.travelers} traveller(s), ${cost.days} day(s)`}
        />
        <Figure label="Per traveller" value={money(cost.per_traveler_inr)} />
        <Figure label="Per day" value={money(cost.per_day_inr)} />
        <Figure
          label={cost.overrun_inr > 0 ? 'Over budget by' : 'Budget remaining'}
          value={money(cost.overrun_inr > 0 ? cost.overrun_inr : cost.remaining_inr)}
          hint={`of ${money(cost.budget_inr)}`}
        />
      </div>

      <div className="stack-sm">
        <div className="row-between">
          <span className="small strong">
            Using {percent(cost.budget_utilisation)} of your budget
          </span>
          <BudgetFitPill
            fit={cost.budget_fit}
            utilisation={cost.budget_utilisation}
            remaining={cost.remaining_inr}
            overrun={cost.overrun_inr}
          />
        </div>
        <div className="meter">
          <div
            className="meter-fill"
            style={{
              width: `${Math.min(100, cost.budget_utilisation * 100)}%`,
              background:
                cost.budget_utilisation > 1 ? 'var(--fit-over)' : 'var(--fit-comfortable)',
            }}
          />
        </div>
        <p className="xs subtle tnum">
          Per person per day: {money(cost.per_traveler_per_day_inr)}
          {' · '}
          {cost.nights} night{cost.nights === 1 ? '' : 's'} of accommodation
        </p>
      </div>

      {/* Category breakdown */}
      <div className="stack">
        <h3>Where the money goes</h3>
        <CategoryBar cost={cost} />
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <caption className="visually-hidden">
            Trip cost by category, with per-traveller amounts
          </caption>
          <thead>
            <tr>
              <th scope="col" style={{ textAlign: 'left' }} className="figure-label">
                Category
              </th>
              <th scope="col" style={{ textAlign: 'right' }} className="figure-label">
                Total
              </th>
              <th scope="col" style={{ textAlign: 'right' }} className="figure-label">
                Share
              </th>
              <th scope="col" style={{ textAlign: 'right' }} className="figure-label">
                Per head
              </th>
            </tr>
          </thead>
          <tbody>
            {cost.categories.map((category) => (
              <tr key={category.category} style={{ borderTop: '1px solid var(--border)' }}>
                <th scope="row" style={{ textAlign: 'left', fontWeight: 550, padding: '0.45rem 0' }}>
                  <span className="row" style={{ gap: 'var(--space-2)', flexWrap: 'nowrap' }}>
                    <span
                      aria-hidden="true"
                      style={{
                        width: 9,
                        height: 9,
                        borderRadius: 2,
                        background: CATEGORY_COLOUR[category.category],
                        flexShrink: 0,
                      }}
                    />
                    <span className="small">{categoryLabel(category.category)}</span>
                  </span>
                </th>
                <td className="tnum small" style={{ textAlign: 'right' }}>
                  {money(category.total_inr)}
                </td>
                <td className="tnum small muted" style={{ textAlign: 'right' }}>
                  {percent(category.share_of_total)}
                </td>
                <td className="tnum small muted" style={{ textAlign: 'right' }}>
                  {money(category.per_traveler_inr)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr style={{ borderTop: '2px solid var(--border-strong)' }}>
              <th scope="row" style={{ textAlign: 'left', padding: '0.5rem 0' }}>
                Total
              </th>
              <td className="tnum strong" style={{ textAlign: 'right' }}>
                {money(categorySum)}
              </td>
              <td colSpan={2} className="xs subtle" style={{ textAlign: 'right' }}>
                {reconciles ? '✓ matches the total above' : '⚠ does not match — please report this'}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      {/* Subtotal / tax / contingency */}
      <div className="panel stack-sm">
        <div className="row-between small">
          <span>Subtotal</span>
          <span className="tnum">{moneyPrecise(cost.subtotal_inr)}</span>
        </div>
        <div className="row-between small">
          <span>Taxes and service charges</span>
          <span className="tnum">{moneyPrecise(cost.taxes_inr)}</span>
        </div>
        <div className="row-between small">
          <span>Contingency ({percent(cost.contingency_rate)})</span>
          <span className="tnum">{moneyPrecise(cost.contingency_inr)}</span>
        </div>
        <div className="row-between strong" style={{ borderTop: '1px solid var(--border)', paddingTop: 'var(--space-2)' }}>
          <span>Total</span>
          <span className="tnum">{moneyPrecise(cost.total_inr)}</span>
        </div>
      </div>

      <FreshnessNote note={cost.freshness_note} liveShare={cost.live_data_share} />

      {/* Assumptions */}
      {showAssumptions && cost.assumptions.length ? (
        <div className="stack">
          <h3>Assumptions behind these numbers</h3>
          <p className="small muted">
            Every figure above is built from these. Change one and the whole estimate is
            recalculated.
          </p>
          <div className="grid grid-2">
            {cost.assumptions.map((assumption) => (
              <div key={assumption.key} className="panel">
                <div className="row-between" style={{ alignItems: 'flex-start' }}>
                  <div style={{ minWidth: 0 }}>
                    <p className="small strong">{assumption.label}</p>
                    {assumption.provenance.note ? (
                      <p className="xs subtle">{assumption.provenance.note}</p>
                    ) : null}
                  </div>
                  <ProvenanceBadge provenance={assumption.provenance} compact />
                </div>
                <div className="row" style={{ marginTop: 'var(--space-2)' }}>
                  {assumption.editable && onAssumptionChange ? (
                    <>
                      <input
                        className="input tnum"
                        type="number"
                        min={0}
                        step={assumption.unit === '%' ? 0.5 : 10}
                        defaultValue={assumption.value}
                        aria-label={`${assumption.label} in ${assumption.unit}`}
                        style={{ maxWidth: 130 }}
                        onBlur={(event) => {
                          const next = Number(event.target.value);
                          if (Number.isFinite(next) && next >= 0 && next !== assumption.value) {
                            onAssumptionChange(assumption.key, next);
                          }
                        }}
                      />
                      <span className="xs subtle">{assumption.unit}</span>
                    </>
                  ) : (
                    <span className="tnum strong">
                      {assumption.unit === 'INR'
                        ? money(assumption.value)
                        : `${assumption.value} ${assumption.unit}`}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {/* Line items */}
      {showLineItems ? (
        <div className="stack">
          <div className="row-between">
            <h3>Every line item</h3>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
            >
              {expanded ? 'Hide' : `Show all ${cost.line_items.length}`}
            </button>
          </div>
          {expanded ? (
            <div className="stack-sm">
              {cost.line_items.map((item, index) => (
                <div key={`${item.label}-${index}`} className="panel">
                  <div className="row-between" style={{ alignItems: 'flex-start' }}>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <p className="small strong">{item.label}</p>
                      <p className="xs muted tnum">
                        {item.units.toLocaleString('en-IN')} × {money(item.unit_price_inr)} per{' '}
                        {item.unit_label}
                        {item.taxable ? ` · +${percent(item.tax_rate)} tax` : ''}
                      </p>
                      {item.detail ? <p className="xs subtle">{item.detail}</p> : null}
                      {item.provenance.note ? (
                        <p className="xs subtle" style={{ marginTop: 2 }}>
                          {item.provenance.note}
                        </p>
                      ) : null}
                    </div>
                    <div style={{ textAlign: 'right', flexShrink: 0 }}>
                      <p className="tnum strong">{money(item.total_inr)}</p>
                      <ProvenanceBadge provenance={item.provenance} compact />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
