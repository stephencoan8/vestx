"""
State tax engines. California is fully modeled; other states use flat rates
from the tax profile until a dedicated engine exists.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.utils.state_tax.base import StateTaxResult, compute_flat_state_tax
from app.utils.state_tax.california import compute_california_tax, CA_SUPPORTED_YEARS


def compute_state_tax(
    *,
    state_code: Optional[str],
    filing_status: str,
    tax_year: int,
    ordinary_income: float,
    capital_gains: float,
    use_state_engine: bool = True,
    state_ordinary_rate: float = 0.0,
    state_cg_rate: float = 0.0,
) -> StateTaxResult:
    """
    Dispatch to a state engine or flat-rate fallback.

    ordinary_income: wages + equity ordinary (ISO DD, etc.)
    capital_gains: net ST + LT capital gains (CA taxes these as ordinary)
    """
    code = (state_code or '').strip().upper()
    total_for_flat = max(0.0, ordinary_income) * state_ordinary_rate + max(0.0, capital_gains) * state_cg_rate

    if not code:
        return StateTaxResult(
            state_code='',
            engine='none',
            tax_year=tax_year,
            taxable_income=max(0.0, ordinary_income + capital_gains),
            regular_tax=0.0,
            surtax=0.0,
            total_tax=0.0,
            marginal_rate=0.0,
            effective_rate=0.0,
            notes=['No state selected — state tax is $0.'],
            breakdown={},
        )

    if use_state_engine and code == 'CA':
        return compute_california_tax(
            ordinary_income=ordinary_income,
            capital_gains=capital_gains,
            filing_status=filing_status,
            tax_year=tax_year,
        )

    if use_state_engine and code not in ('CA',):
        # Flat fallback with note that only CA has full brackets so far
        result = compute_flat_state_tax(
            state_code=code,
            ordinary_income=ordinary_income,
            capital_gains=capital_gains,
            ordinary_rate=state_ordinary_rate,
            cg_rate=state_cg_rate,
            tax_year=tax_year,
        )
        result.notes.append(
            f'No full bracket engine for {code} yet — using your flat profile rates. '
            f'California (CA) is fully supported.'
        )
        return result

    # Explicit flat mode even for CA
    return compute_flat_state_tax(
        state_code=code,
        ordinary_income=ordinary_income,
        capital_gains=capital_gains,
        ordinary_rate=state_ordinary_rate,
        cg_rate=state_cg_rate,
        tax_year=tax_year,
    )


__all__ = [
    'compute_state_tax',
    'StateTaxResult',
    'compute_california_tax',
    'CA_SUPPORTED_YEARS',
]
