"""
Employee FICA / payroll taxes — IRS Publication 15 (Circular E) style.

This is a *solved* calculation. One pure function owns Social Security,
Medicare, and Additional Medicare Tax for employees. Every VestX path
(full-year W-2, vest at ordinary, sale equity ordinary, user flat rates)
must call here so SS wage-base remaining and Add'l Medicare thresholds
cannot diverge.

Rules (employee share only; planning-grade):
  - Social Security (OASDI): 6.2% on wages up to the annual wage base.
    For a period: taxable = max(0, min(period_wages, wage_base - ytd_ss_wages)).
  - Medicare (HI): 1.45% on all wages (no wage base).
  - Additional Medicare Tax (IRC §3101(b)(2)): 0.9% employee-only on wages
    above the filing-status threshold, computed incrementally:
      add_taxable = max(0, ytd+period - thr) - max(0, ytd - thr)

Wage bases (OASDI contribution and benefit base, SSA):
  2022: 147_000, 2023: 160_200, 2024: 168_600,
  2025: 176_100, 2026: 184_500

Add'l Medicare thresholds (IRC / IRS Topic 560):
  Single / HoH: 200_000
  MFJ: 250_000
  MFS: 125_000

Not modeled: employer FICA match, SDI/SUI, multi-employer SS refund,
railroad retirement, tip allocation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

# Employee rates (unchanged for many years)
SS_EMPLOYEE_RATE = 0.062
MEDICARE_EMPLOYEE_RATE = 0.0145
ADDITIONAL_MEDICARE_RATE = 0.009

# OASDI wage base by calendar year
SS_WAGE_BASE: Dict[int, float] = {
    2022: 147_000.0,
    2023: 160_200.0,
    2024: 168_600.0,
    2025: 176_100.0,
    2026: 184_500.0,
}

# Additional Medicare Tax thresholds (wages + compensation)
ADD_MEDICARE_THRESHOLD: Dict[str, float] = {
    'single': 200_000.0,
    'hoh': 200_000.0,
    'mfj': 250_000.0,
    'mfs': 125_000.0,
}


def ss_wage_base_for_year(tax_year: int) -> float:
    year = int(tax_year)
    if year in SS_WAGE_BASE:
        return float(SS_WAGE_BASE[year])
    # Nearest known year (prefer later if equidistant — rare)
    years = sorted(SS_WAGE_BASE.keys())
    if year < years[0]:
        return float(SS_WAGE_BASE[years[0]])
    if year > years[-1]:
        return float(SS_WAGE_BASE[years[-1]])
    return float(min(years, key=lambda y: abs(y - year)))


def add_medicare_threshold(filing_status: str) -> float:
    filing = (filing_status or 'single').lower()
    if filing not in ADD_MEDICARE_THRESHOLD:
        filing = 'single'
    return float(ADD_MEDICARE_THRESHOLD[filing])


@dataclass(frozen=True)
class EmployeeFicaResult:
    """Result of employee FICA on one wage slice (paycheck, vest, full year)."""

    tax_year: int
    filing_status: str
    ytd_wages_before: float
    period_wages: float
    ss_wage_base: float
    ss_remaining_before: float
    ss_taxable_wages: float
    medicare_taxable_wages: float
    additional_medicare_taxable_wages: float
    social_security: float
    medicare: float
    additional_medicare: float
    total: float
    # Effective rates on *period_wages* (blended when SS only partially applies)
    ss_effective_rate: float
    medicare_rate: float
    additional_medicare_effective_rate: float
    ss_exhausted: bool
    notes: tuple

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['notes'] = list(self.notes)
        return d


def employee_fica(
    *,
    period_wages: float,
    ytd_wages_before: float = 0.0,
    tax_year: int,
    filing_status: str = 'single',
    ss_already_maxed: bool = False,
) -> EmployeeFicaResult:
    """
    Employee FICA on `period_wages` given Social Security / Medicare wages
    already paid year-to-date *before* this period.

    Parameters
    ----------
    period_wages
        Wages subject to FICA in this event (vest ordinary, paycheck, full year
        if ytd_wages_before=0).
    ytd_wages_before
        Cumulative FICA wages earlier in the same calendar year (not including
        period_wages). For a full-year W-2 on box-1 alone, pass 0 and put the
        full amount in period_wages.
    tax_year
        Calendar year for the OASDI wage base.
    filing_status
        single | mfj | mfs | hoh — drives Additional Medicare threshold.
    ss_already_maxed
        If True, force zero Social Security (user override when YTD already
        cleared the wage base at another employer, etc.). Medicare still applies.

    Notes
    -----
    When period spans the wage base, SS effective rate on the period is
    blended (tax / period_wages), not a sudden drop from 6.2% to 0% on the
    whole slice — only the over-base portion is untaxed for SS.
    """
    period = max(0.0, float(period_wages or 0.0))
    ytd = max(0.0, float(ytd_wages_before or 0.0))
    year = int(tax_year)
    filing = (filing_status or 'single').lower()
    if filing not in ADD_MEDICARE_THRESHOLD:
        filing = 'single'

    wage_base = ss_wage_base_for_year(year)
    thr = add_medicare_threshold(filing)
    notes = []

    if year not in SS_WAGE_BASE:
        notes.append(
            f'SS wage base for {year} not tabled — using ${wage_base:,.0f} (nearest).'
        )

    # --- Social Security: only remaining wage base ---
    if ss_already_maxed or ytd >= wage_base - 1e-9:
        ss_remaining = 0.0
        ss_taxable = 0.0
        if ss_already_maxed and ytd < wage_base:
            notes.append(
                'SS forced maxed (override) — Medicare still on period wages; '
                f'YTD ${ytd:,.0f} is still below ${wage_base:,.0f} base.'
            )
        else:
            notes.append(
                f'SS wage base exhausted (YTD ${ytd:,.0f} ≥ base ${wage_base:,.0f}).'
            )
    else:
        ss_remaining = max(0.0, wage_base - ytd)
        ss_taxable = min(period, ss_remaining)
        if period > ss_taxable + 1e-9:
            notes.append(
                f'SS partial: ${ss_taxable:,.2f} of ${period:,.2f} still under '
                f'${wage_base:,.0f} base (YTD was ${ytd:,.0f}).'
            )

    social_security = ss_taxable * SS_EMPLOYEE_RATE

    # --- Medicare: all wages ---
    medicare_taxable = period
    medicare = medicare_taxable * MEDICARE_EMPLOYEE_RATE

    # --- Additional Medicare 0.9%: only wages that cross the threshold this period ---
    # Incremental formula (IRS): tax on (ytd+period) minus tax on ytd alone.
    add_on_full = max(0.0, (ytd + period) - thr) * ADDITIONAL_MEDICARE_RATE
    add_on_ytd = max(0.0, ytd - thr) * ADDITIONAL_MEDICARE_RATE
    additional_medicare = max(0.0, add_on_full - add_on_ytd)
    add_taxable = (
        additional_medicare / ADDITIONAL_MEDICARE_RATE
        if ADDITIONAL_MEDICARE_RATE > 0
        else 0.0
    )
    if additional_medicare > 0:
        notes.append(
            f'Additional Medicare 0.9% on ${add_taxable:,.2f} over '
            f'${thr:,.0f} threshold ({filing}).'
        )

    total = social_security + medicare + additional_medicare
    ss_eff = (social_security / period) if period > 0 else 0.0
    med_rate = MEDICARE_EMPLOYEE_RATE if period > 0 else 0.0
    add_eff = (additional_medicare / period) if period > 0 else 0.0

    return EmployeeFicaResult(
        tax_year=year,
        filing_status=filing,
        ytd_wages_before=ytd,
        period_wages=period,
        ss_wage_base=wage_base,
        ss_remaining_before=ss_remaining if not (ss_already_maxed or ytd >= wage_base) else 0.0,
        ss_taxable_wages=ss_taxable,
        medicare_taxable_wages=medicare_taxable,
        additional_medicare_taxable_wages=add_taxable,
        social_security=social_security,
        medicare=medicare,
        additional_medicare=additional_medicare,
        total=total,
        ss_effective_rate=ss_eff,
        medicare_rate=med_rate,
        additional_medicare_effective_rate=add_eff,
        ss_exhausted=(ss_taxable <= 0 and period > 0),
        notes=tuple(notes),
    )


def employee_fica_full_year(
    *,
    annual_wages: float,
    tax_year: int,
    filing_status: str = 'single',
    ss_already_maxed: bool = False,
) -> EmployeeFicaResult:
    """Full calendar year employee FICA on annual FICA wages (box-3/5 style)."""
    return employee_fica(
        period_wages=annual_wages,
        ytd_wages_before=0.0,
        tax_year=tax_year,
        filing_status=filing_status,
        ss_already_maxed=ss_already_maxed,
    )


def profile_ytd_before_equity(
    profile: dict,
    equity_ordinary: float = 0.0,
    *,
    wages_include_equity: bool = False,
) -> float:
    """
    Infer YTD FICA wages *before* an equity ordinary slice.

    Parameters
    ----------
    wages_include_equity
        True when profile wages are full W-2 (already include this equity), e.g.
        tax-at-vest against annual box 1. False (default) for sale planning where
        profile wages are non-equity and equity ordinary stacks on top — do not peel.
    """
    other = float(profile.get('other_ordinary_income') or 0.0)
    ytd = float(profile.get('ytd_wages') or 0.0)
    stacked = max(other, ytd)
    equity = max(0.0, float(equity_ordinary or 0.0))
    if wages_include_equity and equity > 0 and stacked + 0.01 >= equity:
        return max(0.0, stacked - equity)
    return max(0.0, stacked)
