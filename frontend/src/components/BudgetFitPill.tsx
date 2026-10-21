/** Budget-fit indicator. One colour per meaning, everywhere in the app. */

import type { BudgetFit } from '../lib/types';
import { money, percent } from '../lib/format';

const LABELS: Record<BudgetFit, string> = {
  comfortable: 'Comfortable fit',
  good: 'Good fit',
  tight: 'Tight fit',
  stretch: 'Over budget',
  over: 'Out of budget',
};

const SHORT: Record<BudgetFit, string> = {
  comfortable: 'Comfortable',
  good: 'Good fit',
  tight: 'Tight',
  stretch: 'Stretch',
  over: 'Over',
};

const EXPLANATIONS: Record<BudgetFit, string> = {
  comfortable: 'Well inside your budget, with room to spare.',
  good: 'Uses your budget well without straining it.',
  tight: 'Fits, but with very little headroom for extras.',
  stretch: 'Costs more than your budget. Shown as a stretch option.',
  over: 'Beyond your budget, so it is not recommended.',
};

interface Props {
  fit: BudgetFit;
  utilisation?: number;
  remaining?: number;
  overrun?: number;
  short?: boolean;
}

export function BudgetFitPill({ fit, utilisation, remaining, overrun, short = false }: Props) {
  const detail =
    overrun && overrun > 0
      ? `${money(overrun)} over budget.`
      : remaining !== undefined
        ? `${money(remaining)} left over.`
        : '';

  return (
    <span
      className={`badge fit-${fit}`}
      title={`${EXPLANATIONS[fit]}${
        utilisation !== undefined ? ` Uses ${percent(utilisation)} of your budget.` : ''
      } ${detail}`.trim()}
    >
      {short ? SHORT[fit] : LABELS[fit]}
      {utilisation !== undefined && !short ? ` · ${percent(utilisation)}` : ''}
    </span>
  );
}
