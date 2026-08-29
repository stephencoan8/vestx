"""
Employer withholding model (paycheck), distinct from tax liability.

Defaults (overridable with paystub YTD):
  - RSU / bonus supplemental: 22% federal (37% once YTD supplemental > $1M)
  - CA supplemental ~10.23%
  - Cash wages: estimated regular withholding ≈ income tax on wages-only
  - Employee FICA + CA SDI on wages and RSU vests
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.utils.tax_constants import (
    CA_SDI_RATE,
    CA_SUPP_RATE,
    FED_SUPP_HIGH_THRESHOLD,
    FED_SUPP_RATE,
    FED_SUPP_RATE_HIGH,
)
from app.utils.payroll_tax import employee_fica


def entered_amount(val) -> Optional[float]:
    """None / blank / 0 → not entered. Positive (or explicit negative) is used."""
    if val is None or val == '':
        return None
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    if abs(n) < 0.005:
        return None
    return n


def federal_supplemental_withholding(
    gross: float,
    *,
    ytd_supplemental_before: float = 0.0,
) -> float:
    """22% until $1M YTD supplemental wages, then 37% on the excess (Pub 15-A)."""
    g = max(0.0, float(gross or 0))
    ytd = max(0.0, float(ytd_supplemental_before or 0))
    if g <= 0:
        return 0.0
    room = max(0.0, FED_SUPP_HIGH_THRESHOLD - ytd)
    low = min(g, room)
    high = max(0.0, g - room)
    return low * FED_SUPP_RATE + high * FED_SUPP_RATE_HIGH


def ca_supplemental_withholding(gross: float) -> float:
    return max(0.0, float(gross or 0)) * CA_SUPP_RATE


def ca_sdi(gross: float) -> float:
    return max(0.0, float(gross or 0)) * CA_SDI_RATE


def vest_paycheck_withholding(
    gross: float,
    *,
    ytd_supplemental_before: float = 0.0,
    ytd_fica_wages_before: float = 0.0,
    tax_year: int,
    filing_status: str = 'single',
    state_code: str = 'CA',
    ss_already_maxed: bool = False,
    include_fica: bool = True,
) -> Dict[str, Any]:
    """Modeled employer take on an RSU vest (supplemental + payroll)."""
    g = max(0.0, float(gross or 0))
    fed = federal_supplemental_withholding(g, ytd_supplemental_before=ytd_supplemental_before)
    state = ca_supplemental_withholding(g) if (state_code or '').upper() == 'CA' else 0.0
    fica = 0.0
    sdi = 0.0
    if include_fica and g > 0:
        r = employee_fica(
            period_wages=g,
            ytd_wages_before=ytd_fica_wages_before,
            tax_year=tax_year,
            filing_status=filing_status,
            ss_already_maxed=ss_already_maxed,
        )
        fica = float(r.total)
    if (state_code or '').upper() == 'CA':
        sdi = ca_sdi(g)
    return {
        'gross': g,
        'federal': fed,
        'state': state,
        'fica': fica,
        'sdi': sdi,
        'total': fed + state + fica + sdi,
        'method': 'supplemental',
    }


def wages_only_income_tax(
    *,
    cash_wages: float,
    tax_year: int,
    filing_status: str,
    state_code: str = 'CA',
) -> Dict[str, float]:
    """Approximate regular W-4 / DE-4 on cash wages (no equity)."""
    from app.utils.wage_year_tax import compute_w2_year_tax

    w = max(0.0, float(cash_wages or 0))
    if w <= 0:
        return {'federal': 0.0, 'state': 0.0, 'fica': 0.0, 'sdi': 0.0, 'total': 0.0}
    r = compute_w2_year_tax(
        tax_year=tax_year,
        filing_status=filing_status,
        state_code=state_code,
        wages=w,
        include_fica=True,
        ss_wage_base_maxed=False,
        use_state_engine=True,
        fica_wages=w,
    )
    sdi = float(getattr(r, 'sdi', 0) or 0)
    return {
        'federal': float(r.federal_income_tax or 0),
        'state': float(r.state_tax or 0),
        'fica': float(r.total_fica or 0),
        'sdi': sdi,
        'total': float(r.federal_income_tax or 0) + float(r.state_tax or 0)
        + float(r.total_fica or 0) + sdi,
    }
