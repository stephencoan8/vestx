"""
California personal income tax engine for equity planning.

Key CA rules modeled:
- Progressive PIT brackets (no preferential LTCG rate — gains taxed as ordinary)
- Mental Health Services Tax (MHST): 1% on taxable income over $1,000,000
- Filing statuses: single, mfj, mfs, hoh

CA AMT (Schedule P, 7%) is computed in app.utils.amt and layered on top of PIT
in the tax engine — not inside this PIT-only function.

Still not modeled: itemized vs standard, part-year residency, community property,
SDI (payroll withholding), full credit schedules beyond AMT credit.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from app.utils.state_tax.base import (
    StateTaxResult,
    progressive_tax_from_floors,
    marginal_from_floors,
)

CA_SUPPORTED_YEARS = (2024, 2025, 2026)

# Bracket floors → rate (inclusive of lower bound). Source: FTB-style schedules,
# inflation-adjusted approximations for planning (not a substitute for Form 540).
# Format: (taxable_income_floor, marginal_rate)

CA_BRACKETS: Dict[int, Dict[str, List[Tuple[float, float]]]] = {
    2024: {
        'single': [
            (0, 0.01), (10412, 0.02), (24684, 0.04), (38959, 0.06),
            (54081, 0.08), (68350, 0.093), (349137, 0.103),
            (418961, 0.113), (698271, 0.123),
        ],
        'mfj': [
            (0, 0.01), (20824, 0.02), (49368, 0.04), (77918, 0.06),
            (108162, 0.08), (136700, 0.093), (698274, 0.103),
            (837922, 0.113), (1396542, 0.123),
        ],
        'mfs': [
            (0, 0.01), (10412, 0.02), (24684, 0.04), (38959, 0.06),
            (54081, 0.08), (68350, 0.093), (349137, 0.103),
            (418961, 0.113), (698271, 0.123),
        ],
        'hoh': [
            (0, 0.01), (20839, 0.02), (49371, 0.04), (63644, 0.06),
            (78765, 0.08), (93037, 0.093), (474824, 0.103),
            (569790, 0.113), (949649, 0.123),
        ],
    },
    2025: {
        'single': [
            (0, 0.01), (10756, 0.02), (25499, 0.04), (40245, 0.06),
            (55866, 0.08), (70606, 0.093), (360659, 0.103),
            (432787, 0.113), (721314, 0.123),
        ],
        'mfj': [
            (0, 0.01), (21512, 0.02), (50998, 0.04), (80490, 0.06),
            (111732, 0.08), (141212, 0.093), (721318, 0.103),
            (865574, 0.113), (1442628, 0.123),
        ],
        'mfs': [
            (0, 0.01), (10756, 0.02), (25499, 0.04), (40245, 0.06),
            (55866, 0.08), (70606, 0.093), (360659, 0.103),
            (432787, 0.113), (721314, 0.123),
        ],
        'hoh': [
            (0, 0.01), (21527, 0.02), (51000, 0.04), (65670, 0.06),
            (81350, 0.08), (96120, 0.093), (490500, 0.103),
            (588650, 0.113), (981050, 0.123),
        ],
    },
    2026: {
        # Inflation-adjusted continuation of 2025 schedule (planning estimate)
        'single': [
            (0, 0.01), (11050, 0.02), (26200, 0.04), (41350, 0.06),
            (57400, 0.08), (72550, 0.093), (370600, 0.103),
            (444700, 0.113), (741200, 0.123),
        ],
        'mfj': [
            (0, 0.01), (22100, 0.02), (52400, 0.04), (82700, 0.06),
            (114800, 0.08), (145100, 0.093), (741200, 0.103),
            (889400, 0.113), (1482400, 0.123),
        ],
        'mfs': [
            (0, 0.01), (11050, 0.02), (26200, 0.04), (41350, 0.06),
            (57400, 0.08), (72550, 0.093), (370600, 0.103),
            (444700, 0.113), (741200, 0.123),
        ],
        'hoh': [
            (0, 0.01), (22120, 0.02), (52400, 0.04), (67500, 0.06),
            (83600, 0.08), (98750, 0.093), (504000, 0.103),
            (604800, 0.113), (1008000, 0.123),
        ],
    },
}

# Mental Health Services Tax: 1% on CA taxable income over $1,000,000
CA_MHST_THRESHOLD = 1_000_000.0
CA_MHST_RATE = 0.01


def _brackets_for(year: int, filing: str) -> List[Tuple[float, float]]:
    if year not in CA_BRACKETS:
        year = min(CA_BRACKETS.keys(), key=lambda y: abs(y - year))
    table = CA_BRACKETS[year]
    if filing not in table:
        filing = 'single'
    return table[filing]


def compute_california_tax(
    *,
    ordinary_income: float,
    capital_gains: float,
    filing_status: str,
    tax_year: int,
) -> StateTaxResult:
    """
    CA taxes capital gains as ordinary income — stack ordinary + gains.

    ordinary_income: non-CG ordinary (wages, RSU vest, ISO DD ordinary, etc.)
    capital_gains: net positive capital gains from sales
    """
    filing = filing_status if filing_status in ('single', 'mfj', 'mfs', 'hoh') else 'single'
    # CA: no LTCG preference
    taxable = max(0.0, ordinary_income) + max(0.0, capital_gains)
    brackets = _brackets_for(tax_year, filing)

    regular = progressive_tax_from_floors(taxable, brackets)
    mhst = max(0.0, taxable - CA_MHST_THRESHOLD) * CA_MHST_RATE
    total = regular + mhst
    marg = marginal_from_floors(taxable, brackets)
    if taxable > CA_MHST_THRESHOLD:
        marg += CA_MHST_RATE
    eff = (total / taxable) if taxable > 0 else 0.0

    notes = [
        'California taxes capital gains as ordinary income (no federal-style LTCG preference).',
        f'PIT brackets for tax year {tax_year if tax_year in CA_BRACKETS else "nearest supported"} + '
        f'Mental Health Services Tax {CA_MHST_RATE*100:.0f}% above ${CA_MHST_THRESHOLD:,.0f}.',
    ]
    if taxable > CA_MHST_THRESHOLD:
        notes.append(
            f'MHST applies: 1% × ${taxable - CA_MHST_THRESHOLD:,.0f} over $1M = ${mhst:,.2f}.'
        )

    return StateTaxResult(
        state_code='CA',
        engine='CA',
        tax_year=tax_year,
        taxable_income=taxable,
        regular_tax=regular,
        surtax=mhst,
        total_tax=total,
        marginal_rate=marg,
        effective_rate=eff,
        notes=notes,
        breakdown={
            'ordinary_income': max(0.0, ordinary_income),
            'capital_gains_as_ordinary': max(0.0, capital_gains),
            'ca_taxable_income': taxable,
            'ca_pit': regular,
            'ca_mhst': mhst,
            'mhst_threshold': CA_MHST_THRESHOLD,
            'mhst_rate': CA_MHST_RATE,
            'top_marginal_including_mhst': marg,
            'cg_preferential': False,
        },
    )
