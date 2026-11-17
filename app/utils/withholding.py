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


def coerce_supplemental_rate(raw, default: float = FED_SUPP_RATE) -> float:
    """Percent (37) or fraction (0.37) → fraction. Blank/invalid → default (22%)."""
    if raw is None or raw == '':
        return float(default)
    try:
        rate = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if rate > 1.0:
        rate = rate / 100.0
    if rate < 0:
        return float(default)
    return rate


def resolve_federal_supplemental_rate(year_row=None, profile=None) -> float:
    """
    Tax-year row wins, then the main Tax Profile, then the IRS 22% flat.

    Null means "not elected" — do not treat it as 0%.
    """
    for src in (year_row, profile):
        if src is None:
            continue
        raw = src.get('federal_supplemental_rate') if isinstance(src, dict) else getattr(
            src, 'federal_supplemental_rate', None
        )
        if raw is None or raw == '':
            continue
        return coerce_supplemental_rate(raw)
    return float(FED_SUPP_RATE)


def _row_get(src, name):
    if src is None:
        return None
    if isinstance(src, dict):
        return src.get(name)
    return getattr(src, name, None)


def _as_date(raw):
    from datetime import date, datetime
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return date.fromisoformat(raw.strip()[:10])
        except ValueError:
            return None
    return None


def resolve_withholding_stub_date(year_row=None, profile=None):
    """
    Date the entered YTD withholding is through.

    Uses the same row that supplied the YTD figures (year row when that row
    has an amount, otherwise the main profile). An explicit date on that row
    wins. If that row's federal and state YTD still match the seeded 7/31
    stub, that date is used so vests after it are not dropped. Any other YTD
    with no date stays unknown — those vests are not guessed onto the stub,
    and another user's profile date is not borrowed.
    """
    from datetime import date
    from app.utils.tax_constants import (
        STUB_FEDERAL_WITHHOLDING_YTD,
        STUB_STATE_WITHHOLDING_YTD,
        STUB_WITHHOLDING_AS_OF,
    )

    def _entered(src, name):
        return entered_amount(_row_get(src, name))

    source = year_row if (
        _entered(year_row, 'federal_withholding_ytd') is not None
        or _entered(year_row, 'state_withholding_ytd') is not None
    ) else profile
    explicit = _as_date(_row_get(source, 'withholding_stub_date'))
    if explicit:
        return explicit
    fed = _entered(source, 'federal_withholding_ytd')
    state = _entered(source, 'state_withholding_ytd')
    if fed is None or state is None:
        return None
    if (
        abs(fed - STUB_FEDERAL_WITHHOLDING_YTD) < 0.02
        and abs(state - STUB_STATE_WITHHOLDING_YTD) < 0.02
    ):
        return date.fromisoformat(STUB_WITHHOLDING_AS_OF)
    return None


def federal_supplemental_withholding(
    gross: float,
    *,
    ytd_supplemental_before: float = 0.0,
    rate: Optional[float] = None,
) -> float:
    """
    Flat supplemental withholding on RSU/bonus wages.

    ``rate`` is the elected flat rate (Tax Profile; default 22%). Amounts that
    push YTD supplemental wages over $1M are withheld at the greater of that
    rate and 37% (Pub 15-A).
    """
    g = max(0.0, float(gross or 0))
    ytd = max(0.0, float(ytd_supplemental_before or 0))
    if g <= 0:
        return 0.0
    low_rate = float(FED_SUPP_RATE if rate is None else coerce_supplemental_rate(rate))
    high_rate = max(low_rate, float(FED_SUPP_RATE_HIGH))
    room = max(0.0, FED_SUPP_HIGH_THRESHOLD - ytd)
    low = min(g, room)
    high = max(0.0, g - room)
    return low * low_rate + high * high_rate


def split_withholding_by_rates(actual: float, rates: Dict[str, float]) -> Dict[str, float]:
    """
    Split dollars actually withheld by the rates that produced them.

    Federal and state are income-tax credits. Social Security, Medicare,
    Additional Medicare, and CA SDI/VPDI are payroll — they stay out of the
    income-tax credit.
    """
    actual_amt = max(0.0, float(actual or 0))
    income_keys = ('federal', 'state')
    payroll_keys = ('social_security', 'medicare', 'additional_medicare', 'sdi')
    weights: Dict[str, float] = {}
    for key in income_keys + payroll_keys:
        try:
            weights[key] = max(0.0, float((rates or {}).get(key) or 0))
        except (TypeError, ValueError):
            weights[key] = 0.0
    total_w = sum(weights.values())
    if actual_amt <= 0 or total_w <= 0:
        return {
            'federal': 0.0,
            'state': 0.0,
            'payroll': 0.0,
            'actual': actual_amt,
        }
    federal = actual_amt * weights['federal'] / total_w
    state = actual_amt * weights['state'] / total_w
    return {
        'federal': federal,
        'state': state,
        'payroll': actual_amt - federal - state,
        'actual': actual_amt,
    }


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
    federal_rate: Optional[float] = None,
) -> Dict[str, Any]:
    """Modeled employer take on an RSU vest (supplemental + payroll)."""
    g = max(0.0, float(gross or 0))
    fed = federal_supplemental_withholding(
        g,
        ytd_supplemental_before=ytd_supplemental_before,
        rate=federal_rate,
    )
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
        'federal_rate': (fed / g) if g > 0 else coerce_supplemental_rate(
            FED_SUPP_RATE if federal_rate is None else federal_rate
        ),
        'state_rate': (state / g) if g > 0 else (CA_SUPP_RATE if (state_code or '').upper() == 'CA' else 0.0),
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
