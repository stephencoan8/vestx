"""Shared state tax types and flat-rate fallback."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List


@dataclass
class StateTaxResult:
    state_code: str
    engine: str  # 'CA', 'flat', 'none'
    tax_year: int
    taxable_income: float
    regular_tax: float
    surtax: float  # e.g. CA mental health 1%
    total_tax: float
    marginal_rate: float
    effective_rate: float
    notes: List[str] = field(default_factory=list)
    breakdown: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def progressive_tax_from_floors(income: float, brackets: list) -> float:
    """
    brackets: ordered list of (floor, rate) like federal engine.
    """
    if income <= 0:
        return 0.0
    tax = 0.0
    for i, (floor, rate) in enumerate(brackets):
        next_floor = brackets[i + 1][0] if i + 1 < len(brackets) else None
        if next_floor is None:
            tax += max(0.0, income - floor) * rate
            break
        if income > floor:
            segment = min(income, next_floor) - floor
            if segment > 0:
                tax += segment * rate
    return tax


def marginal_from_floors(income: float, brackets: list) -> float:
    if income <= 0:
        return brackets[0][1] if brackets else 0.0
    rate = brackets[0][1]
    for floor, r in brackets:
        if income >= floor:
            rate = r
    return rate


def compute_flat_state_tax(
    *,
    state_code: str,
    ordinary_income: float,
    capital_gains: float,
    ordinary_rate: float,
    cg_rate: float,
    tax_year: int,
) -> StateTaxResult:
    ord_i = max(0.0, ordinary_income)
    cg = max(0.0, capital_gains)
    regular = ord_i * ordinary_rate + cg * cg_rate
    taxable = ord_i + cg
    eff = (regular / taxable) if taxable > 0 else 0.0
    # marginal approx: max of the two rates if both positive income
    marg = 0.0
    if cg > 0:
        marg = max(marg, cg_rate)
    if ord_i > 0:
        marg = max(marg, ordinary_rate)
    return StateTaxResult(
        state_code=state_code,
        engine='flat',
        tax_year=tax_year,
        taxable_income=taxable,
        regular_tax=regular,
        surtax=0.0,
        total_tax=regular,
        marginal_rate=marg,
        effective_rate=eff,
        notes=['Flat state rates from your tax profile.'],
        breakdown={
            'ordinary_base': ord_i,
            'cg_base': cg,
            'ordinary_rate': ordinary_rate,
            'cg_rate': cg_rate,
        },
    )
