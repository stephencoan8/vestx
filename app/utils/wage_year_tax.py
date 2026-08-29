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
from app.utils.payroll_tax import (
    employee_fica_full_year,
    SS_EMPLOYEE_RATE,
    MEDICARE_EMPLOYEE_RATE,
    ADDITIONAL_MEDICARE_RATE,
    add_medicare_threshold,
)
from app.utils.tax_constants import (
    FED_STD_DEDUCTION,
    CA_STD_DEDUCTION,
    CA_STD_SOURCE,
    CA_SDI_RATE,
    std_for as _std_for,
)


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
    fica_marginal: float  # SS + Medicare + Add'l on next $ of wages (0 SS if maxed)
    combined_ordinary_marginal: float  # fed + CA + FICA + SDI next-dollar
    ss_wage_base: float
    sdi: float = 0.0
    sdi_marginal: float = 0.0
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
    itemize_salt: float = 0.0,
    itemize_mortgage: float = 0.0,
    itemize_charity: float = 0.0,
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
    from app.utils.tax_constants import SALT_CAP
    salt = min(max(0.0, float(itemize_salt or 0)), float(SALT_CAP.get(year, 10_000)))
    itemized = salt + max(0.0, float(itemize_mortgage or 0)) + max(0.0, float(itemize_charity or 0))
    fed_ded = fed_std
    if itemized > fed_std + 0.5:
        fed_ded = itemized
    taxable_ordinary = max(0.0, gross_ordinary - fed_ded)

    notes: List[str] = []
    if year in CA_STD_SOURCE:
        notes.append(f'CA standard deduction {year}: ${ca_std:,.0f} ({CA_STD_SOURCE[year]}).')
    if itemized > fed_std + 0.5:
        notes.append(
            f'Itemizing ${itemized:,.0f} (SALT capped at ${salt:,.0f}) vs federal std ${fed_std:,.0f}.'
        )
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

    # FICA via shared IRS Pub 15 module (SmartAsset / Pub 15 style):
    #   SS  = 6.2% × min(annual_wages, SS wage base for year)
    #   Med = 1.45% × all wages
    #   Add = 0.9% × max(0, wages − threshold)
    # Full-year W-2 NEVER uses "SS already maxed" — that flag is only for mid-year
    # incremental events (next vest after YTD already hit the base). On annual box 1
    # the first $wage_base of these wages already includes the SS that was paid.
    if include_fica and fwages > 0:
        if ss_wage_base_maxed:
            notes.append(
                '“SS wage base already maxed” is ignored on the full-year W-2 estimate — '
                'annual wages still owe Social Security on min(wages, wage base). '
                'That checkbox only affects mid-year vest/sale increments.'
            )
        fica_r = employee_fica_full_year(
            annual_wages=fwages,
            tax_year=year,
            filing_status=filing,
            ss_already_maxed=False,
        )
        social_security = fica_r.social_security
        medicare = fica_r.medicare
        additional_medicare = fica_r.additional_medicare
        ss_base = fica_r.ss_wage_base
        notes.append(
            f'FICA {year}: SS 6.2% on min(${fwages:,.0f}, ${ss_base:,.0f}) = '
            f'${social_security:,.0f}; Medicare 1.45% = ${medicare:,.0f}; '
            f'Add’l Medicare = ${additional_medicare:,.0f}.'
        )
        notes.extend(list(fica_r.notes)[:2])
    else:
        social_security = medicare = additional_medicare = 0.0
        from app.utils.payroll_tax import ss_wage_base_for_year
        ss_base = ss_wage_base_for_year(year)

    total_fica = social_security + medicare + additional_medicare
    income_tax_total = federal_income + state_tax
    sdi_amt = 0.0
    sdi_m = 0.0
    if (state_code or '').upper() == 'CA' and include_fica and fwages > 0:
        sdi_amt = fwages * CA_SDI_RATE
        sdi_m = CA_SDI_RATE
        from app.utils.tax_constants import CA_SDI_LABEL, CA_SDI_SOURCE
        notes.append(
            f'CA {CA_SDI_LABEL} {CA_SDI_RATE*100:.1f}% uncapped on ${fwages:,.0f} = ${sdi_amt:,.0f}. {CA_SDI_SOURCE}'
        )
    total_tax = income_tax_total + total_fica + sdi_amt
    tax_base = wages + other_ordinary + stcg + ltcg
    eff = (total_tax / tax_base) if tax_base > 0 else 0.0
    income_eff = (income_tax_total / tax_base) if tax_base > 0 else 0.0

    # Next $1 of ordinary (salary / RSU vest W-2): fed bracket + CA + employee FICA + SDI.
    # NIIT does not apply to wages. SS drops off at the wage base.
    state_marg = float(state_result.marginal_rate or 0)
    ss_m = SS_EMPLOYEE_RATE if (include_fica and fwages < ss_base) else 0.0
    med_m = MEDICARE_EMPLOYEE_RATE if include_fica else 0.0
    add_thr = add_medicare_threshold(filing)
    add_m = ADDITIONAL_MEDICARE_RATE if (include_fica and (fwages + 1.0) >= add_thr) else 0.0
    fica_marg = ss_m + med_m + add_m
    combined_ord_marg = ord_marginal + state_marg + fica_marg + sdi_m

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
        federal_std_deduction=fed_ded,
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
        state_marginal=state_marg,
        fica_marginal=fica_marg,
        sdi=sdi_amt,
        sdi_marginal=sdi_m,
        combined_ordinary_marginal=combined_ord_marg,
        ss_wage_base=ss_base,
        notes=notes,
        vest_prefills=vest_prefills or {},
    )


def vest_w2_kind(grant_type: str = '', share_type: str = '') -> str:
    """How a vest event hits the annual W-2 stack. ESPP/ISO are not Box 1 at vest."""
    from app.models.grant import ShareType
    from app.utils.share_labels import is_espp_grant
    st = (share_type or '').lower()
    if st in (ShareType.ISO_5Y.value, ShareType.ISO_6Y.value, 'iso'):
        return 'iso'
    if is_espp_grant(grant_type, share_type):
        return 'espp'
    if st == ShareType.CASH.value:
        return 'cash'
    return 'rsu'


def iso_bargain_for_year(user_id: int, tax_year: int) -> Dict[str, Any]:
    """
    ISO AMT preference for the calendar year.

    Tax due uses recorded ISOExercise rows (SSOT) and iso_stock lots acquired
    this year. Unexercised ISO vests are planning-only — not Form 6251 until
    an exercise is recorded.
    """
    total = 0.0
    n_ex = 0
    exercised_vests = set()
    exercised_qty: Dict[int, float] = {}
    try:
        from app.models.stock_sale import ISOExercise
        rows = ISOExercise.query.filter_by(user_id=user_id).all()
    except Exception:
        rows = []
    for e in rows:
        if e.vest_event_id:
            vid = int(e.vest_event_id)
            exercised_qty[vid] = exercised_qty.get(vid, 0.0) + float(e.shares_exercised or 0)
        if not e.exercise_date or e.exercise_date.year != int(tax_year):
            continue
        barg = e.total_bargain_element
        if barg is None:
            sh = float(e.shares_exercised or 0)
            barg = sh * max(0.0, float(e.fmv_at_exercise or 0) - float(e.strike_price or 0))
        total += float(barg or 0)
        n_ex += 1
        if e.vest_event_id:
            exercised_vests.add(int(e.vest_event_id))

    n_lots = 0
    try:
        from app.models.tax_lot import TaxLot
        lots = TaxLot.query.filter_by(user_id=user_id, kind='iso_stock').all()
    except Exception:
        lots = []
    for lot in lots:
        if not lot.acquired_date or lot.acquired_date.year != int(tax_year):
            continue
        if lot.vest_event_id and int(lot.vest_event_id) in exercised_vests:
            continue
        qty = float(lot.original_qty or 0)
        fmv = float(lot.fmv_at_open or 0)
        strike = float(lot.strike_price or lot.cost_basis_per_share or 0)
        barg = qty * max(0.0, fmv - strike)
        if barg <= 0:
            continue
        total += barg
        n_lots += 1

    n_vest = 0
    vest_barg = 0.0
    try:
        from datetime import date as _date
        from app.models.vest_event import VestEvent
        from app.models.grant import Grant, ShareType
        from app.utils.price_utils import get_latest_user_price
        from sqlalchemy.orm import joinedload
        live = float(get_latest_user_price(user_id) or 0.0)
        today = _date.today()
        start = _date(int(tax_year), 1, 1)
        end = _date(int(tax_year), 12, 31)
        iso_types = {ShareType.ISO_5Y.value, ShareType.ISO_6Y.value}
        events = (
            VestEvent.query.options(joinedload(VestEvent.grant))
            .join(Grant)
            .filter(
                Grant.user_id == user_id,
                VestEvent.vest_date >= start,
                VestEvent.vest_date <= end,
            )
            .all()
        )
        for ve in events:
            if not ve.grant or (ve.grant.share_type or '') not in iso_types:
                continue
            vested = float(ve.shares_vested or 0)
            unex = max(0.0, vested - exercised_qty.get(int(ve.id), 0.0))
            if unex <= 0:
                continue
            strike = float(ve.grant.share_price_at_grant or 0)
            is_future = bool(ve.vest_date and ve.vest_date > today)
            fmv = 0.0
            if is_future:
                fmv = live
            else:
                fmv = float(getattr(ve, 'share_price_at_vest', None) or 0)
                if fmv <= 0 and ve.value_at_vest and vested:
                    fmv = float(ve.value_at_vest) / vested
                if fmv <= 0:
                    fmv = live
            barg = unex * max(0.0, fmv - strike)
            if barg <= 0:
                continue
            vest_barg += barg
            n_vest += 1
    except Exception:
        vest_barg = 0.0
        n_vest = 0

    if n_ex:
        source = 'iso_exercise'
    elif n_lots:
        source = 'iso_stock'
    else:
        source = 'none'
    return {
        'iso_bargain': round(total, 2),
        'iso_vest_unexercised_bargain': round(vest_barg, 2),
        'exercise_count': n_ex,
        'iso_stock_lot_count': n_lots,
        'iso_vest_events': n_vest,
        'source': source,
    }


def build_year_vest_prefill(user_id: int, tax_year: int) -> Dict[str, Any]:
    """
    Sum RSU/cash vest gross for a calendar year from VestX data.

    Splits:
      - equity_vested_ytd: vest_date <= as_of (FMV at vest)
      - equity_remaining_year: future vests still in this tax year @ live FMV
    ISO vest events are skipped (no W-2 ordinary at vest for hold path).
    ESPP purchase/vest is not Box 1 — ordinary is on sale (§423).
    """
    from datetime import date
    from app.models.vest_event import VestEvent
    from app.models.grant import Grant, ShareType
    from app.utils.price_utils import get_latest_user_price
    from app.utils.share_labels import lot_kind_line
    from sqlalchemy.orm import joinedload

    start = date(tax_year, 1, 1)
    end = date(tax_year, 12, 31)
    today = date.today()
    if tax_year < today.year:
        as_of = end
    elif tax_year > today.year:
        as_of = start  # entire year still future
    else:
        as_of = today

    live = float(get_latest_user_price(user_id) or 0.0)

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

    rsu_past = 0.0
    rsu_future = 0.0
    cash_past = 0.0
    cash_future = 0.0
    espp_past = 0.0
    espp_future = 0.0
    iso_count = 0
    espp_count = 0
    rows: List[dict] = []
    for ve in events:
        if not ve.grant:
            continue
        st = ve.grant.share_type
        gt = ve.grant.grant_type or ''
        kind = vest_w2_kind(gt, st)
        sh = float(ve.shares_vested or 0)
        is_future = bool(ve.vest_date and ve.vest_date > as_of)
        if is_future:
            gval = sh * live if live > 0 else 0.0
        else:
            try:
                gval = float(ve.value_at_vest or 0)
            except Exception:
                gval = sh * live if live > 0 else 0.0

        in_w2 = kind in ('rsu', 'cash')
        if kind == 'iso':
            iso_count += 1
        elif kind == 'espp':
            espp_count += 1
            if is_future:
                espp_future += gval
            else:
                espp_past += gval
        elif kind == 'cash':
            if is_future:
                cash_future += gval
            else:
                cash_past += gval
        else:
            if is_future:
                rsu_future += gval
            else:
                rsu_past += gval

        rows.append({
            'vest_date': ve.vest_date.isoformat() if ve.vest_date else None,
            'label': lot_kind_line(gt, st),
            'shares': sh,
            'gross_value': round(gval, 2),
            'share_type': st,
            'grant_type': gt,
            'kind': kind,
            'in_w2': in_w2,
            'is_future': is_future,
        })

    equity_past = rsu_past + cash_past
    equity_future = rsu_future + cash_future
    equity_w2 = equity_past + equity_future
    bargain = iso_bargain_for_year(user_id, tax_year)
    return {
        'tax_year': tax_year,
        'as_of': as_of.isoformat(),
        'live_price': live,
        'rsu_vest_gross': round(rsu_past + rsu_future, 2),
        'rsu_vested_ytd': round(rsu_past, 2),
        'rsu_remaining_year': round(rsu_future, 2),
        'cash_bonus_gross': round(cash_past + cash_future, 2),
        'espp_purchase_gross': round(espp_past + espp_future, 2),
        'espp_not_box1': True,
        'equity_vested_ytd': round(equity_past, 2),
        'equity_remaining_year': round(equity_future, 2),
        'suggested_equity_in_w2': round(equity_w2, 2),
        'iso_vest_events_skipped': iso_count,
        'espp_events_excluded_from_w2': espp_count,
        'iso_bargain': bargain['iso_bargain'],
        'iso_vest_unexercised_bargain': bargain.get('iso_vest_unexercised_bargain') or 0,
        'iso_exercise_count': bargain['exercise_count'],
        'iso_vest_events': bargain.get('iso_vest_events') or 0,
        'iso_bargain_source': bargain['source'],
        'event_count': len(rows),
        'events': rows[:40],
    }


def build_year_sale_gains(user_id: int, tax_year: int) -> Dict[str, Any]:
    """
    Realized capital gain/loss on VestX sales in a calendar year.

    Uses StockSale.capital_gain (proceeds − basis), never gross proceeds.
    ST vs LT follows the sale row. ISO disqualifying ordinary is ignored here.
    """
    from datetime import date
    from app.models.stock_sale import StockSale

    start = date(tax_year, 1, 1)
    end = date(tax_year, 12, 31)
    try:
        sales = (
            StockSale.query
            .filter(
                StockSale.user_id == user_id,
                StockSale.sale_date >= start,
                StockSale.sale_date <= end,
            )
            .all()
        )
    except Exception:
        sales = []

    stcg = 0.0
    ltcg = 0.0
    for s in sales:
        proceeds = float(
            s.total_proceeds
            if s.total_proceeds is not None
            else float(s.shares_sold or 0) * float(s.sale_price or 0)
        )
        basis = float(s.total_cost_basis or 0)
        gain = float(s.capital_gain if s.capital_gain is not None else proceeds - basis)
        if s.is_long_term:
            ltcg += gain
        else:
            stcg += gain
    return {
        'tax_year': tax_year,
        'sale_count': len(sales),
        'stcg': round(stcg, 2),
        'ltcg': round(ltcg, 2),
        'capital_gain': round(stcg + ltcg, 2),
    }


def year_income_stack(
    user_id: int,
    tax_year: int,
    *,
    cash_wages: float = 0.0,
    other_stcg: float = 0.0,
    other_ltcg: float = 0.0,
) -> Dict[str, Any]:
    """
    Year income from VestX + the cash-wages field.

      ordinary / FICA  = cash wages + RSU/cash vests (past @ vest FMV, rest of year @ live)
      ESPP purchase is not Box 1 (ordinary at sale). ISO vest is not W-2; AMT is on exercise.
      ST/LT CG         = recorded sale capital gains + optional non-VestX extras
    """
    cash = max(0.0, float(cash_wages or 0))
    vest = build_year_vest_prefill(user_id, tax_year)
    sales = build_year_sale_gains(user_id, tax_year)
    eq_past = float(vest.get('equity_vested_ytd') or 0)
    eq_fut = float(vest.get('equity_remaining_year') or 0)
    ordinary = cash + eq_past + eq_fut
    stcg = float(sales.get('stcg') or 0) + float(other_stcg or 0)
    ltcg = float(sales.get('ltcg') or 0) + float(other_ltcg or 0)
    return {
        'cash_wages': round(cash, 2),
        'equity_vested_ytd': round(eq_past, 2),
        'equity_remaining_year': round(eq_fut, 2),
        'sale_stcg': float(sales.get('stcg') or 0),
        'sale_ltcg': float(sales.get('ltcg') or 0),
        'sale_count': int(sales.get('sale_count') or 0),
        'ordinary': round(ordinary, 2),
        'fica_wages': round(ordinary, 2),
        'stcg': round(stcg, 2),
        'ltcg': round(ltcg, 2),
        'tax_base': round(ordinary + stcg + ltcg, 2),
        'iso_bargain': float(vest.get('iso_bargain') or 0),
        'vest': vest,
        'sales': sales,
    }


def year_tax_snapshot(
    user,
    tax_year: int,
    *,
    itemize_salt: float = 0.0,
    itemize_mortgage: float = 0.0,
    itemize_charity: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """
    Same stack as Tax profile / /tax/api/year-tax for ``tax_year``.

    harbor_tax is income tax (fed+CA+NIIT), not FICA/VPDI — matches 2026 Expected tax.
    """
    if user is None or not getattr(user, 'id', None):
        return None
    try:
        from app.utils.tax_engine import resolve_engine_profile_for_year
        eng = resolve_engine_profile_for_year(user, int(tax_year))
    except Exception:
        return None
    cash = float(eng.get('other_ordinary_income_raw') or eng.get('other_ordinary_income') or 0)
    stack = year_income_stack(
        user.id,
        int(tax_year),
        cash_wages=cash,
        other_stcg=float(eng.get('other_short_term_gains') or 0),
        other_ltcg=float(eng.get('other_long_term_gains') or 0),
    )
    ordinary = float(stack.get('ordinary') or 0)
    tax_base = float(stack.get('tax_base') or 0)
    if ordinary <= 0 and tax_base <= 0:
        return None
    y = compute_w2_year_tax(
        tax_year=int(tax_year),
        filing_status=eng.get('filing_status') or 'single',
        state_code=eng.get('state_code') or 'CA',
        wages=ordinary,
        stcg=float(stack.get('stcg') or 0),
        ltcg=float(stack.get('ltcg') or 0),
        include_fica=bool(eng.get('include_fica', True)),
        ss_wage_base_maxed=False,
        use_state_engine=bool(eng.get('use_state_engine', True)),
        vest_prefills=stack.get('vest') or {},
        fica_wages=float(stack.get('fica_wages') or ordinary),
        itemize_salt=float(itemize_salt or 0),
        itemize_mortgage=float(itemize_mortgage or 0),
        itemize_charity=float(itemize_charity or 0),
    )
    income = round(float(y.income_tax_total or 0), 2)
    return {
        'tax_year': int(tax_year),
        'total_tax': round(float(y.total_tax or 0), 2),
        'income_tax_total': income,
        'harbor_tax': income,
        'agi': round(tax_base or ordinary, 2),
        'ordinary': round(ordinary, 2),
        'profile_source': eng.get('profile_source'),
    }


def attach_computed_year_income(profile: dict, user_id: int, tax_year: int) -> dict:
    """
    Attach computed ordinary (cash + RSU/cash vests) without destroying cash wages.

    other_ordinary_income / other_ordinary_income_raw stay salary (ex-equity).
    computed_ordinary is the W-2 stack for analyze_sales. ytd_wages is not a
    second income field — keep it equal to cash so leftover 200k stacks cannot
    impersonate salary.
    """
    cash = float(
        profile.get('other_ordinary_income_raw')
        if profile.get('other_ordinary_income_raw') is not None
        else (profile.get('other_ordinary_income') or 0)
    )
    stack = year_income_stack(user_id, tax_year, cash_wages=cash)
    profile['other_ordinary_income_raw'] = stack['cash_wages']
    profile['other_ordinary_income'] = stack['cash_wages']
    profile['computed_ordinary'] = stack['ordinary']
    profile['stacking_ordinary_income'] = stack['ordinary']
    profile['ytd_wages'] = stack['cash_wages']
    profile['year_income'] = stack
    return profile


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
    try:
        from app.models.stock_sale import StockSale
        sale_years = (
            StockSale.query
            .filter(StockSale.user_id == user_id)
            .with_entities(extract('year', StockSale.sale_date))
            .distinct()
            .all()
        )
        for (y,) in sale_years:
            if y:
                years.add(int(y))
    except Exception:
        pass
    return sorted((y for y in years if 2018 <= y <= today_y + 1), reverse=True)
