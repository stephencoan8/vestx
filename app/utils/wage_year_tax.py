"""
Full-year W-2 / ordinary income tax calculator for past years.

Unlike sale planning (incremental vs profile), this returns the **full-year**
federal + CA + FICA stack on wages you enter — standard employee W-2 path
(no ISO AMT). Optional other ST/LT gains supported if you add them later.

Uses the selected calendar year's bracket / SS wage-base tables (not "today").
Applies standard deduction for federal + CA so effective rates match a simple
return more closely. Still planning-grade (no itemizing, credits, 401k).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from app.utils.tax_engine import (
    ORDINARY_BRACKETS,
    NIIT_THRESHOLD,
    progressive_tax,
    marginal_rate,
    preferential_ltcg_tax,
    _year_table,
)
from app.utils.payroll_tax import employee_fica_full_year


# Federal standard deduction (approx IRS inflation-adjusted)
FED_STD_DEDUCTION = {
    2023: {'single': 13850, 'mfj': 27700, 'mfs': 13850, 'hoh': 20800},
    2024: {'single': 14600, 'mfj': 29200, 'mfs': 14600, 'hoh': 21900},
    2025: {'single': 15000, 'mfj': 30000, 'mfs': 15000, 'hoh': 22500},
    2026: {'single': 16100, 'mfj': 32200, 'mfs': 16100, 'hoh': 24150},
}

# CA standard deduction (FTB-style planning approx)
CA_STD_DEDUCTION = {
    2023: {'single': 5202, 'mfj': 10404, 'mfs': 5202, 'hoh': 10404},
    2024: {'single': 5540, 'mfj': 11080, 'mfs': 5540, 'hoh': 11080},
    2025: {'single': 5706, 'mfj': 11412, 'mfs': 5706, 'hoh': 11412},
    2026: {'single': 5860, 'mfj': 11720, 'mfs': 5860, 'hoh': 11720},
}


def _std_for(table: dict, year: int, filing: str) -> float:
    years = sorted(table.keys())
    use = year if year in table else min(years, key=lambda y: abs(y - year))
    row = table[use]
    return float(row.get(filing) or row.get('single') or 0)


@dataclass
class YearTaxResult:
    tax_year: int
    filing_status: str
    state_code: str
    wages: float  # ordinary / box-1 used for federal+CA
    fica_wages: float  # wage base used for FICA (may differ)
    other_ordinary: float
    total_ordinary: float
    taxable_ordinary_federal: float
    federal_std_deduction: float
    ca_std_deduction: float
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
    income_tax_total: float  # federal + state (no FICA)
    effective_rate: float  # total_tax / (ordinary + gains)  all-in
    income_tax_effective_rate: float  # (fed+state) / base
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
    fica_wages: Optional[float] = None,
) -> YearTaxResult:
    """
    Full-year employee tax on W-2 wages (+ optional other ordinary / CG).

    Federal: progressive ordinary brackets for `tax_year` after standard deduction.
    CA: progressive PIT + MHST after CA standard deduction.
    FICA: on fica_wages (defaults to wages) — SS wage base for that tax year.
    """
    from app.utils.state_tax import compute_state_tax

    filing = filing_status if filing_status in ('single', 'mfj', 'mfs', 'hoh') else 'single'
    year = int(tax_year)
    wages = max(0.0, float(wages or 0))
    other_ordinary = max(0.0, float(other_ordinary or 0))
    stcg = max(0.0, float(stcg or 0))
    ltcg = max(0.0, float(ltcg or 0))
    # FICA base is separate so YTD can't inflate federal ordinary via max()
    if fica_wages is None or float(fica_wages or 0) <= 0:
        fwages = wages
    else:
        fwages = max(0.0, float(fica_wages))

    # Federal ordinary stack (wages + other ordinary + STCG as ordinary)
    gross_ordinary = wages + other_ordinary + stcg
    fed_std = _std_for(FED_STD_DEDUCTION, year, filing)
    ca_std = _std_for(CA_STD_DEDUCTION, year, filing)
    taxable_ordinary = max(0.0, gross_ordinary - fed_std)

    notes: List[str] = []
    bracket_year = year if year in ORDINARY_BRACKETS else min(
        ORDINARY_BRACKETS.keys(), key=lambda y: abs(y - year)
    )
    brackets = _year_table(ORDINARY_BRACKETS, year)[filing]
    if year not in ORDINARY_BRACKETS:
        notes.append(
            f'Federal brackets for {year} not tabled — using nearest year {bracket_year}.'
        )
    else:
        notes.append(f'Using federal ordinary brackets for tax year {year}.')

    federal_ordinary_tax = progressive_tax(taxable_ordinary, brackets)
    ord_marginal = marginal_rate(taxable_ordinary, brackets)
    # LTCG fills preferential bands on top of taxable ordinary (after std ded)
    federal_ltcg_tax, ltcg_marg = preferential_ltcg_tax(ltcg, taxable_ordinary, filing, year)

    # NIIT is on MAGI-ish base (pre standard deduction)
    niit = 0.0
    if ltcg + stcg > 0:
        magi = gross_ordinary + ltcg
        thr = NIIT_THRESHOLD.get(filing, 200000)
        inv = stcg + ltcg
        niit = min(inv, max(0.0, magi - thr)) * 0.038

    federal_income = federal_ordinary_tax + federal_ltcg_tax + niit

    # CA: apply CA std deduction to ordinary+gains stack before PIT
    ca_gross = wages + other_ordinary + stcg + ltcg
    ca_taxable_ordinary = max(0.0, (wages + other_ordinary) - ca_std)
    # Keep gains fully taxable; only ordinary wages get std ded allocation
    ca_gains = stcg + ltcg
    # If ordinary alone doesn't absorb std ded, leftover reduces gains (rare with wages)
    leftover_std = max(0.0, ca_std - (wages + other_ordinary))
    ca_gains_taxable = max(0.0, ca_gains - leftover_std)

    state_result = compute_state_tax(
        state_code=state_code,
        filing_status=filing,
        tax_year=year,
        ordinary_income=ca_taxable_ordinary,
        capital_gains=ca_gains_taxable,
        use_state_engine=use_state_engine,
        state_ordinary_rate=0.0,
        state_cg_rate=0.0,
    )
    state_tax = float(state_result.total_tax)
    notes.extend(list(state_result.notes or [])[:2])
    notes.append(
        f'Federal std. deduction ${fed_std:,.0f} · CA std. deduction ${ca_std:,.0f} '
        f'({filing}, tax year {year}).'
    )

    # FICA via shared IRS Pub 15 module (SS remaining base + Add'l Medicare)
    if include_fica and fwages > 0:
        from app.utils.payroll_tax import ss_wage_base_for_year
        ss_base_chk = ss_wage_base_for_year(year)
        # Full-year: wages determine SS. "Maxed" only if wages ≥ base or empty + override.
        force_maxed = bool(ss_wage_base_maxed) and (
            fwages >= ss_base_chk - 1.0 or fwages <= 0
        )
        if ss_wage_base_maxed and not force_maxed:
            notes.append(
                'SS wage-base maxed flag ignored for full-year calc because annual '
                f'wages ${fwages:,.0f} are under SS base ${ss_base_chk:,.0f}.'
            )
        fica_r = employee_fica_full_year(
            annual_wages=fwages,
            tax_year=year,
            filing_status=filing,
            ss_already_maxed=force_maxed,
        )
        social_security = fica_r.social_security
        medicare = fica_r.medicare
        additional_medicare = fica_r.additional_medicare
        ss_base = fica_r.ss_wage_base
        notes.append(f'SS wage base for {year}: ${ss_base:,.0f} (employee FICA module).')
        notes.extend(list(fica_r.notes)[:2])
    else:
        social_security = medicare = additional_medicare = 0.0
        from app.utils.payroll_tax import ss_wage_base_for_year
        ss_base = ss_wage_base_for_year(year)

    total_fica = social_security + medicare + additional_medicare
    income_tax_total = federal_income + state_tax
    total_tax = income_tax_total + total_fica
    tax_base = wages + other_ordinary + stcg + ltcg
    eff = (total_tax / tax_base) if tax_base > 0 else 0.0
    income_eff = (income_tax_total / tax_base) if tax_base > 0 else 0.0

    notes.append(
        'Planning estimate — not a filed return. No itemizing, credits, or pre-tax 401(k) netting.'
    )
    notes.append(
        'RSU vest ordinary belongs in W-2 wages (box 1) for this year only — '
        'do not reuse another year’s YTD.'
    )

    return YearTaxResult(
        tax_year=year,
        filing_status=filing,
        state_code=(state_code or '').upper() or '—',
        wages=wages,
        fica_wages=fwages,
        other_ordinary=other_ordinary,
        total_ordinary=gross_ordinary,
        taxable_ordinary_federal=taxable_ordinary,
        federal_std_deduction=fed_std,
        ca_std_deduction=ca_std,
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
        income_tax_total=income_tax_total,
        effective_rate=eff,
        income_tax_effective_rate=income_eff,
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
