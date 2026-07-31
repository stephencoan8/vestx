"""
Full-year W-2 / ordinary income tax calculator for past years.

Unlike sale planning (incremental vs profile), this returns the **full-year**
federal + CA + FICA stack on wages you enter — standard employee W-2 path
(no ISO AMT). Optional other ST/LT gains supported if you add them later.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from app.utils.tax_engine import (
    ORDINARY_BRACKETS,
    SS_WAGE_BASE,
    SS_RATE,
    MEDICARE_RATE,
    ADDITIONAL_MEDICARE_RATE,
    ADD_MEDICARE_THRESHOLD,
    NIIT_THRESHOLD,
    progressive_tax,
    marginal_rate,
    preferential_ltcg_tax,
    _year_table,
    stacking_ordinary_income,
)


@dataclass
class YearTaxResult:
    tax_year: int
    filing_status: str
    state_code: str
    wages: float
    other_ordinary: float
    total_ordinary: float
    stcg: float
    ltcg: float
    federal_ordinary_tax: float
    federal_ltcg_tax: float
    niit: float
    federal_income_tax: float  # ordinary + ltcg + niit (no FICA)
    state_tax: float
    state_regular_tax: float
    state_surtax: float
    social_security: float
    medicare: float
    additional_medicare: float
    total_fica: float
    total_tax: float  # federal income + state + FICA
    effective_rate: float  # total_tax / (ordinary + gains)
    ordinary_marginal: float
    ltcg_marginal: float
    state_marginal: float
    ss_wage_base: float
    notes: List[str] = field(default_factory=list)
    vest_prefills: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def compute_w2_year_tax(
    *,
    tax_year: int,
    filing_status: str = 'single',
    state_code: str = 'CA',
    wages: float = 0.0,
    other_ordinary: float = 0.0,
    stcg: float = 0.0,
    ltcg: float = 0.0,
    include_fica: bool = True,
    ss_wage_base_maxed: bool = False,
    use_state_engine: bool = True,
    vest_prefills: Optional[dict] = None,
) -> YearTaxResult:
    """
    Full-year employee tax on W-2 wages (+ optional other ordinary / CG).

    Federal: progressive ordinary brackets for `tax_year` (nearest table if missing).
    CA: progressive PIT + MHST when state_code is CA.
    FICA: SS to annual wage base, Medicare 1.45%, Additional Medicare 0.9% over threshold.
    """
    from app.utils.state_tax import compute_state_tax

    filing = filing_status if filing_status in ('single', 'mfj', 'mfs', 'hoh') else 'single'
    year = int(tax_year)
    wages = max(0.0, float(wages or 0))
    other_ordinary = max(0.0, float(other_ordinary or 0))
    stcg = max(0.0, float(stcg or 0))
    ltcg = max(0.0, float(ltcg or 0))
    total_ordinary = wages + other_ordinary + stcg  # STCG as ordinary for federal

    notes: List[str] = []
    brackets = _year_table(ORDINARY_BRACKETS, year)[filing]
    if year not in ORDINARY_BRACKETS:
        notes.append(
            f'Federal brackets for {year} not tabled — using nearest year '
            f'{min(ORDINARY_BRACKETS.keys(), key=lambda y: abs(y - year))}.'
        )

    federal_ordinary_tax = progressive_tax(total_ordinary, brackets)
    ord_marginal = marginal_rate(total_ordinary, brackets)
    federal_ltcg_tax, ltcg_marg = preferential_ltcg_tax(ltcg, total_ordinary, filing, year)

    niit = 0.0
    if ltcg + stcg > 0:
        magi = total_ordinary + ltcg
        thr = NIIT_THRESHOLD.get(filing, 200000)
        inv = stcg + ltcg
        niit = min(inv, max(0.0, magi - thr)) * 0.038

    federal_income = federal_ordinary_tax + federal_ltcg_tax + niit

    state_result = compute_state_tax(
        state_code=state_code,
        filing_status=filing,
        tax_year=year,
        ordinary_income=wages + other_ordinary,
        capital_gains=stcg + ltcg,
        use_state_engine=use_state_engine,
        state_ordinary_rate=0.0,
        state_cg_rate=0.0,
    )
    state_tax = float(state_result.total_tax)
    notes.extend(list(state_result.notes or [])[:3])

    # FICA on wages only (not investment income)
    ss_base = float(SS_WAGE_BASE.get(year) or SS_WAGE_BASE.get(2025, 176100))
    if year not in SS_WAGE_BASE:
        notes.append(f'SS wage base for {year} not tabled — using ${ss_base:,.0f}.')

    if include_fica and wages > 0:
        if ss_wage_base_maxed:
            social_security = 0.0
            notes.append('SS treated as already maxed — Medicare only on wages.')
        else:
            social_security = min(wages, ss_base) * SS_RATE
        medicare = wages * MEDICARE_RATE
        add_thr = float(ADD_MEDICARE_THRESHOLD.get(filing, 200000))
        additional_medicare = max(0.0, wages - add_thr) * ADDITIONAL_MEDICARE_RATE
    else:
        social_security = medicare = additional_medicare = 0.0

    total_fica = social_security + medicare + additional_medicare
    total_tax = federal_income + state_tax + total_fica
    tax_base = wages + other_ordinary + stcg + ltcg
    eff = (total_tax / tax_base) if tax_base > 0 else 0.0

    notes.append(
        'Planning estimate — not a filed return. No standard deduction/itemizing, credits, or pre-tax 401(k) netting.'
    )
    notes.append('RSU vest ordinary should be included in W-2 wages (box 1), not double-counted as “other.”')

    return YearTaxResult(
        tax_year=year,
        filing_status=filing,
        state_code=(state_code or '').upper() or '—',
        wages=wages,
        other_ordinary=other_ordinary,
        total_ordinary=total_ordinary,
        stcg=stcg,
        ltcg=ltcg,
        federal_ordinary_tax=federal_ordinary_tax,
        federal_ltcg_tax=federal_ltcg_tax,
        niit=niit,
        federal_income_tax=federal_income,
        state_tax=state_tax,
        state_regular_tax=float(state_result.regular_tax or 0),
        state_surtax=float(state_result.surtax or 0),
        social_security=social_security,
        medicare=medicare,
        additional_medicare=additional_medicare,
        total_fica=total_fica,
        total_tax=total_tax,
        effective_rate=eff,
        ordinary_marginal=ord_marginal,
        ltcg_marginal=ltcg_marg,
        state_marginal=float(state_result.marginal_rate or 0),
        ss_wage_base=ss_base,
        notes=notes,
        vest_prefills=vest_prefills or {},
    )


def build_year_vest_prefill(user_id: int, tax_year: int) -> Dict[str, Any]:
    """
    Sum historical RSU/cash vest gross value for a calendar year from VestX data.
    Suggests a W-2 equity component the user can round into wages.
    """
    from datetime import date
    from app.models.vest_event import VestEvent
    from app.models.grant import Grant, ShareType
    from sqlalchemy.orm import joinedload

    start = date(tax_year, 1, 1)
    end = date(tax_year, 12, 31)
    events = (
        VestEvent.query
        .options(joinedload(VestEvent.grant))
        .join(Grant)
        .filter(
            Grant.user_id == user_id,
            VestEvent.vest_date >= start,
            VestEvent.vest_date <= end,
        )
        .order_by(VestEvent.vest_date)
        .all()
    )

    rsu_gross = 0.0
    cash_gross = 0.0
    iso_count = 0
    rows: List[dict] = []
    for ve in events:
        if not ve.grant:
            continue
        st = ve.grant.share_type
        if st in (ShareType.ISO_5Y.value, ShareType.ISO_6Y.value):
            iso_count += 1
            # ISO exercise AMT not W-2 ordinary at vest for pure ISO hold path
            continue
        try:
            gval = float(ve.value_at_vest or 0)
        except Exception:
            gval = 0.0
        if st == ShareType.CASH.value:
            cash_gross += gval
        else:
            rsu_gross += gval
        rows.append({
            'vest_date': ve.vest_date.isoformat() if ve.vest_date else None,
            'label': f"{ve.grant.grant_type or 'grant'} · {st}",
            'shares': float(ve.shares_vested or 0),
            'gross_value': round(gval, 2),
            'share_type': st,
        })

    equity_w2 = rsu_gross + cash_gross
    return {
        'tax_year': tax_year,
        'rsu_vest_gross': round(rsu_gross, 2),
        'cash_bonus_gross': round(cash_gross, 2),
        'suggested_equity_in_w2': round(equity_w2, 2),
        'iso_vest_events_skipped': iso_count,
        'event_count': len(rows),
        'events': rows[:40],
        'note': (
            'RSU vest value is usually in W-2 box 1 already. '
            'Enter total W-2 wages (salary + equity) as one number — do not add this on top twice.'
        ),
    }


def list_years_with_vests(user_id: int, *, back: int = 8) -> List[int]:
    """Years that have vest events, plus recent calendar years for the dropdown."""
    from datetime import date
    from app.models.vest_event import VestEvent
    from app.models.grant import Grant
    from sqlalchemy import extract

    today_y = date.today().year
    years = set(range(today_y - back, today_y + 1))
    try:
        rows = (
            VestEvent.query
            .join(Grant)
            .filter(Grant.user_id == user_id)
            .with_entities(extract('year', VestEvent.vest_date))
            .distinct()
            .all()
        )
        for (y,) in rows:
            if y:
                years.add(int(y))
    except Exception:
        pass
    return sorted((y for y in years if 2018 <= y <= today_y + 1), reverse=True)
