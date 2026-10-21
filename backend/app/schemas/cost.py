"""Cost model output types.

Design rule: the total is *derived* from the line items, never stored
independently. Any breakdown the UI renders is a projection of the same list,
so the displayed numbers always reconcile.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, computed_field

from .common import Assumption, Provenance
from .enums import BudgetFit, Confidence, CostBasis, CostCategory


class CostLineItem(BaseModel):
    """One priced element of the trip."""

    category: CostCategory
    label: str
    basis: CostBasis
    units: float = Field(..., ge=0, description="Quantity in the unit implied by `basis`.")
    unit_label: str
    unit_price_inr: float = Field(..., ge=0)
    total_inr: float = Field(..., ge=0)
    taxable: bool = False
    tax_rate: float = Field(0.0, ge=0.0, le=0.5)
    provenance: Provenance
    detail: str | None = None

    @classmethod
    def build(
        cls,
        *,
        category: CostCategory,
        label: str,
        basis: CostBasis,
        units: float,
        unit_label: str,
        unit_price_inr: float,
        provenance: Provenance,
        taxable: bool = False,
        tax_rate: float = 0.0,
        detail: str | None = None,
    ) -> "CostLineItem":
        """Construct a line item with `total_inr` computed, not supplied."""
        return cls(
            category=category,
            label=label,
            basis=basis,
            units=round(units, 4),
            unit_label=unit_label,
            unit_price_inr=round(unit_price_inr, 2),
            total_inr=round(units * unit_price_inr, 2),
            taxable=taxable,
            tax_rate=tax_rate,
            provenance=provenance,
            detail=detail,
        )


class CategoryTotal(BaseModel):
    """Per-category rollup used by the budget dashboard."""

    category: CostCategory
    total_inr: float
    share_of_total: float = Field(..., ge=0.0, le=1.0)
    per_traveler_inr: float


class CostBreakdown(BaseModel):
    """A complete, auditable trip cost."""

    currency: str = "INR"
    travelers: int
    days: int
    nights: int

    line_items: list[CostLineItem]
    assumptions: list[Assumption] = Field(default_factory=list)

    subtotal_inr: float
    taxes_inr: float
    contingency_inr: float
    contingency_rate: float
    total_inr: float

    per_traveler_inr: float
    per_day_inr: float
    per_traveler_per_day_inr: float

    budget_inr: float
    remaining_inr: float
    overrun_inr: float
    budget_utilisation: float = Field(..., description="total / budget")
    budget_fit: BudgetFit

    confidence: Confidence
    live_data_share: float = Field(
        ..., ge=0.0, le=1.0, description="Share of cost backed by live provider quotes."
    )
    freshness_note: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def categories(self) -> list[CategoryTotal]:
        """Category rollup derived from `line_items` + tax/contingency.

        Because this is computed rather than stored, it cannot drift from the
        line items, and summing it always reproduces `total_inr`.
        """
        buckets: dict[CostCategory, float] = {}
        for item in self.line_items:
            buckets[item.category] = round(buckets.get(item.category, 0.0) + item.total_inr, 2)
        if self.taxes_inr:
            buckets[CostCategory.TAXES_AND_FEES] = round(
                buckets.get(CostCategory.TAXES_AND_FEES, 0.0) + self.taxes_inr, 2
            )
        if self.contingency_inr:
            buckets[CostCategory.CONTINGENCY] = round(
                buckets.get(CostCategory.CONTINGENCY, 0.0) + self.contingency_inr, 2
            )

        total = self.total_inr or 1.0
        order = list(CostCategory)
        return [
            CategoryTotal(
                category=cat,
                total_inr=buckets[cat],
                share_of_total=round(buckets[cat] / total, 4),
                per_traveler_inr=round(buckets[cat] / max(1, self.travelers), 2),
            )
            for cat in order
            if cat in buckets
        ]

    def reconciles(self, tolerance: float = 0.05) -> bool:
        """True when the category rollup sums to the headline total."""
        return abs(sum(c.total_inr for c in self.categories) - self.total_inr) <= tolerance
