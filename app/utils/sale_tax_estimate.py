"""
Single sale-tax surface for the whole product.

All “tax if sold” / recorded-sale estimates go through tax_engine.analyze_sales
with a year-scoped Tax Profile. No flat User rates, no hard-coded 15% LTCG.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from app.utils.tax_engine import (
    LotSaleInput,
    analyze_sales,
    resolve_engine_profile_for_year,
)
from app.utils.shares import whole_shares, clamp_whole_shares


def _empty_sale_dict(
    *,
    shares_held: float = 0.0,
    cost_basis_per_share: float = 0.0,
    cost_basis: float = 0.0,
    current_value: float = 0.0,
    unrealized_gain: float = 0.0,
    days_held: int = 0,
    is_long_term: bool = False,
    holding_period: str = '—',
    method: str = 'none',
) -> Dict[str, Any]:
    return {
        'shares_held': shares_held,
        'cost_basis_per_share': cost_basis_per_share,
        'cost_basis': cost_basis,
        'current_value': current_value,
        'unrealized_gain': unrealized_gain,
        'days_held': days_held,
        'is_long_term': is_long_term,
        'holding_period': holding_period,
        'estimated_tax': 0.0,
        'federal_tax': 0.0,
        'niit_tax': 0.0,
        'state_tax': 0.0,
        'fica_tax': 0.0,
        'federal_rate': 0.0,
        'state_rate': 0.0,
        'effective_rate': 0.0,
        'method': method,
        'tax_year': None,
        'warnings': [],
    }


def _holding_period_label(days_held: int, has_vested: bool) -> str:
    if not has_vested:
        return '—'
    if days_held >= 365:
        years = days_held // 365
        return f'{years}y {days_held % 365}d'
    return f'{days_held}d'


def _iso_exercise_context(user_id: int, vest_event_id: int):
    """Latest ISO exercise for this vest, if any."""
    try:
        from app.models.stock_sale import ISOExercise
        ex = (
            ISOExercise.query.filter_by(user_id=user_id, vest_event_id=vest_event_id)
            .order_by(ISOExercise.exercise_date.desc())
            .first()
        )
        if not ex:
            return None, None
        return ex.exercise_date, float(ex.fmv_at_exercise or 0) or None
    except Exception:
        return None, None


def lot_input_from_vest(
    vest,
    *,
    shares: float,
    sale_price: float,
    sale_date: date,
    cost_basis_per_share: float,
    user_id: Optional[int] = None,
    exercise_date: Optional[date] = None,
    fmv_at_exercise: Optional[float] = None,
    label: str = '',
) -> Optional[LotSaleInput]:
    """Build LotSaleInput for one vest disposition. Returns None if not sellable."""
    from app.models.grant import ShareType

    sh = whole_shares(shares)
    if not vest or not vest.grant or sh <= 0:
        return None
    grant = vest.grant
    st = grant.share_type
    if st == ShareType.CASH.value:
        return None

    is_iso = st in (ShareType.ISO_5Y.value, ShareType.ISO_6Y.value)
    uid = user_id or grant.user_id
    ex_date = exercise_date
    fmv_ex = fmv_at_exercise
    if is_iso and ex_date is None and uid:
        ex_date, fmv_ex = _iso_exercise_context(uid, vest.id)

    return LotSaleInput(
        vest_event_id=vest.id,
        grant_id=grant.id,
        share_type=st or 'rsu',
        grant_type=grant.grant_type or 'rsu',
        shares=float(sh),
        sale_price=float(sale_price),
        sale_date=sale_date,
        vest_date=vest.vest_date,
        grant_date=grant.grant_date or vest.vest_date,
        cost_basis_per_share=float(cost_basis_per_share or 0),
        is_iso=is_iso,
        strike_price=float(grant.share_price_at_grant or 0) if is_iso else 0.0,
        exercise_date=ex_date,
        fmv_at_exercise=fmv_ex,
        label=label or f'Vest {vest.vest_date}',
    )


def analysis_to_ui_dict(
    analysis,
    *,
    shares_held: float,
    cost_basis_per_share: float,
    cost_basis: float,
    current_value: float,
    unrealized_gain: float,
    days_held: int,
    is_long_term: bool,
    holding_period: str,
) -> Dict[str, Any]:
    """Map TaxAnalysis (one or many lots) into legacy UI field names."""
    gain = max(0.0, float(unrealized_gain or 0))
    fed = float(analysis.federal_tax_total or 0)
    # federal_tax_total already includes ordinary + ltcg + niit - credits + amt
    # Split for display: prefer component fields
    federal_income = float(analysis.regular_federal_tax or 0) + float(analysis.amt_due or 0)
    # Show NIIT separately; subtract from "federal" line for display if both shown
    niit = float(analysis.niit or 0)
    federal_display = max(0.0, float(analysis.federal_tax_total or 0) - niit)
    state = float(analysis.state_tax or 0)
    fica = float(analysis.fica_tax or 0)
    total = float(analysis.total_tax or 0)
    eff = float(analysis.effective_rate_on_gain or 0)
    if eff <= 0 and gain > 0:
        eff = total / gain

    rates = analysis.rates_used or {}
    fed_rate = float(rates.get('ltcg') or rates.get('ordinary_marginal') or 0)
    if not is_long_term:
        fed_rate = float(rates.get('ordinary_marginal') or fed_rate)
    state_rate = float(
        rates.get('state_effective')
        or rates.get('state_marginal')
        or rates.get('state_ordinary')
        or 0
    )
    if state_rate <= 0 and gain > 0 and state > 0:
        state_rate = state / gain
    if fed_rate <= 0 and gain > 0 and federal_display > 0:
        fed_rate = federal_display / gain

    return {
        'shares_held': shares_held,
        'cost_basis_per_share': cost_basis_per_share,
        'cost_basis': cost_basis,
        'current_value': current_value,
        'unrealized_gain': unrealized_gain,
        'days_held': days_held,
        'is_long_term': is_long_term,
        'holding_period': holding_period,
        'estimated_tax': total,
        'federal_tax': federal_display,
        'niit_tax': niit,
        'state_tax': state,
        'fica_tax': fica,
        'federal_rate': fed_rate,
        'state_rate': state_rate,
        'effective_rate': eff,
        'method': 'engine',
        'tax_year': analysis.tax_year,
        'warnings': list(analysis.warnings or [])[:6],
        'after_tax_proceeds': float(analysis.after_tax_proceeds or 0),
        'equity_ordinary': float(analysis.equity_ordinary or 0),
        'ltcg': float(analysis.ltcg or 0),
        'stcg': float(analysis.stcg or 0),
        'federal_ltcg_tax': float(analysis.federal_ltcg_tax or 0),
        'federal_ordinary_tax': float(analysis.federal_ordinary_tax or 0),
        'amt_due': float(analysis.amt_due or 0),
        'rates_used': dict(rates),
    }


def estimate_vest_sale_tax(
    vest,
    user,
    *,
    current_stock_price: Optional[float] = None,
    total_sold: float = 0.0,
    total_exercised: float = 0.0,
    sale_date: Optional[date] = None,
    profile: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Incremental tax if remaining shares from this vest are sold on sale_date
    (default today) at current_stock_price.
    """
    from app.models.grant import ShareType
    from app.utils.price_utils import get_latest_user_price

    if not vest or not vest.grant:
        return _empty_sale_dict(method='none')

    grant = vest.grant
    if grant.share_type == ShareType.CASH.value:
        held = whole_shares(float(vest.shares_received or 0) - float(total_sold or 0))
        return _empty_sale_dict(
            shares_held=held,
            cost_basis_per_share=1.0,
            cost_basis=held,
            current_value=held,
            method='n/a',
        )

    if current_stock_price is None:
        current_stock_price = get_latest_user_price(grant.user_id) or 0.0
    sale_date = sale_date or date.today()

    shares_held = whole_shares(
        float(vest.shares_received or 0) - float(total_sold or 0) - float(total_exercised or 0)
    )
    if shares_held <= 0:
        return _empty_sale_dict(method='none')

    is_iso = grant.share_type in (ShareType.ISO_5Y.value, ShareType.ISO_6Y.value)
    has_vested = bool(vest.has_vested)
    if is_iso:
        cost_basis_per_share = float(grant.share_price_at_grant or 0)
    else:
        from app.utils.vest_basis import rsu_cost_basis_per_share
        if has_vested:
            cost_basis_per_share, _src = rsu_cost_basis_per_share(
                vest, user_id=grant.user_id, persist=True
            )
        else:
            cost_basis_per_share = float(current_stock_price or 0)

    cost_basis = shares_held * cost_basis_per_share
    current_value = shares_held * float(current_stock_price)
    unrealized_gain = current_value - cost_basis
    days_held = (sale_date - vest.vest_date).days if has_vested and vest.vest_date else 0
    is_long_term = days_held >= 365
    holding_period = _holding_period_label(days_held, has_vested)

    if not user or unrealized_gain <= 0:
        return _empty_sale_dict(
            shares_held=shares_held,
            cost_basis_per_share=cost_basis_per_share,
            cost_basis=cost_basis,
            current_value=current_value,
            unrealized_gain=unrealized_gain,
            days_held=days_held,
            is_long_term=is_long_term,
            holding_period=holding_period,
            method='none' if unrealized_gain <= 0 else 'none',
        )

    lot = lot_input_from_vest(
        vest,
        shares=shares_held,
        sale_price=float(current_stock_price),
        sale_date=sale_date,
        cost_basis_per_share=cost_basis_per_share,
        user_id=user.id,
    )
    if not lot:
        return _empty_sale_dict(
            shares_held=shares_held,
            cost_basis_per_share=cost_basis_per_share,
            cost_basis=cost_basis,
            current_value=current_value,
            unrealized_gain=unrealized_gain,
            days_held=days_held,
            is_long_term=is_long_term,
            holding_period=holding_period,
            method='none',
        )

    if profile is None:
        profile = resolve_engine_profile_for_year(user, sale_date.year)
    else:
        profile = dict(profile)
        profile['tax_year'] = sale_date.year

    analysis = analyze_sales(profile, [lot])
    return analysis_to_ui_dict(
        analysis,
        shares_held=shares_held,
        cost_basis_per_share=cost_basis_per_share,
        cost_basis=cost_basis,
        current_value=current_value,
        unrealized_gain=unrealized_gain,
        days_held=days_held,
        is_long_term=is_long_term,
        holding_period=holding_period,
    )


def estimate_lots_sale_tax(
    user,
    lots: Sequence[LotSaleInput],
    *,
    profile: Optional[dict] = None,
    tax_year: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Portfolio-level estimate: one analyze_sales over many lots (correct stacking).
    """
    lot_list = []
    for x in lots:
        if not x:
            continue
        sh = whole_shares(x.shares)
        if sh <= 0:
            continue
        if sh != x.shares:
            # Normalize to whole shares on a copy
            x = LotSaleInput(
                vest_event_id=x.vest_event_id,
                grant_id=x.grant_id,
                share_type=x.share_type,
                grant_type=x.grant_type,
                shares=float(sh),
                sale_price=x.sale_price,
                sale_date=x.sale_date,
                vest_date=x.vest_date,
                grant_date=x.grant_date,
                cost_basis_per_share=x.cost_basis_per_share,
                is_iso=x.is_iso,
                strike_price=x.strike_price,
                exercise_date=x.exercise_date,
                fmv_at_exercise=x.fmv_at_exercise,
                commission=x.commission,
                label=x.label,
            )
        lot_list.append(x)
    if not user or not lot_list:
        return {
            'estimated_tax': 0.0,
            'federal_tax': 0.0,
            'niit_tax': 0.0,
            'state_tax': 0.0,
            'fica_tax': 0.0,
            'effective_rate': 0.0,
            'method': 'engine',
            'tax_year': tax_year,
            'total_proceeds': 0.0,
            'after_tax_proceeds': 0.0,
            'warnings': [],
            'lot_count': 0,
        }

    year = int(tax_year or lot_list[0].sale_date.year)
    if profile is None:
        profile = resolve_engine_profile_for_year(user, year)
    else:
        profile = dict(profile)
        profile['tax_year'] = year

    analysis = analyze_sales(profile, list(lot_list))
    gain = max(
        0.0,
        float(analysis.total_proceeds or 0)
        - float(analysis.total_cost_basis or 0)
        + float(analysis.equity_ordinary or 0),
    )
    total = float(analysis.total_tax or 0)
    niit = float(analysis.niit or 0)
    return {
        'estimated_tax': total,
        'federal_tax': max(0.0, float(analysis.federal_tax_total or 0) - niit),
        'niit_tax': niit,
        'state_tax': float(analysis.state_tax or 0),
        'fica_tax': float(analysis.fica_tax or 0),
        'effective_rate': float(analysis.effective_rate_on_gain or 0)
        if analysis.effective_rate_on_gain
        else (total / gain if gain > 0 else 0.0),
        'method': 'engine',
        'tax_year': analysis.tax_year,
        'total_proceeds': float(analysis.total_proceeds or 0),
        'total_cost_basis': float(analysis.total_cost_basis or 0),
        'after_tax_proceeds': float(analysis.after_tax_proceeds or 0),
        'ltcg': float(analysis.ltcg or 0),
        'stcg': float(analysis.stcg or 0),
        'federal_ltcg_tax': float(analysis.federal_ltcg_tax or 0),
        'federal_ordinary_tax': float(analysis.federal_ordinary_tax or 0),
        'warnings': list(analysis.warnings or [])[:8],
        'lot_count': len(lot_list),
        'rates_used': dict(analysis.rates_used or {}),
        'analysis': analysis.to_dict(),
    }


def estimate_recorded_stock_sale(sale, user, *, profile: Optional[dict] = None) -> Dict[str, Any]:
    """Engine estimate for a recorded StockSale row."""
    if not sale or not user:
        return {
            'estimated_federal': 0.0,
            'estimated_niit': 0.0,
            'estimated_state': 0.0,
            'estimated_total': 0.0,
            'method': 'none',
            'effective_rate': 0.0,
            'is_long_term': bool(getattr(sale, 'is_long_term', False)),
            'holding_days': 0,
            'federal_rate': 0.0,
            'niit_rate': 0.0,
            'state_rate': 0.0,
        }

    vest = sale.vest_event
    if not vest or not vest.grant:
        # Fallback: no lot linkage — still try synthetic RSU lot
        return {
            'estimated_federal': 0.0,
            'estimated_niit': 0.0,
            'estimated_state': 0.0,
            'estimated_total': 0.0,
            'method': 'none',
            'effective_rate': 0.0,
            'is_long_term': bool(sale.is_long_term),
            'holding_days': 0,
            'federal_rate': 0.0,
            'niit_rate': 0.0,
            'state_rate': 0.0,
        }

    from app.models.grant import ShareType

    grant = vest.grant
    is_iso = grant.share_type in (ShareType.ISO_5Y.value, ShareType.ISO_6Y.value)

    basis = float(sale.cost_basis_per_share or 0)
    if basis <= 0:
        if is_iso:
            basis = float(grant.share_price_at_grant or 0)
        else:
            basis = float(vest.share_price_at_vest or 0)

    lot = lot_input_from_vest(
        vest,
        shares=whole_shares(sale.shares_sold),
        sale_price=float(sale.sale_price or 0),
        sale_date=sale.sale_date,
        cost_basis_per_share=basis,
        user_id=user.id,
        exercise_date=getattr(sale, 'exercise_date', None),
        fmv_at_exercise=getattr(sale, 'fmv_at_exercise', None),
        label=f'Sale {sale.sale_date}',
    )
    if not lot:
        return {
            'estimated_federal': 0.0,
            'estimated_niit': 0.0,
            'estimated_state': 0.0,
            'estimated_total': 0.0,
            'method': 'none',
            'effective_rate': 0.0,
            'is_long_term': bool(sale.is_long_term),
            'holding_days': 0,
            'federal_rate': 0.0,
            'niit_rate': 0.0,
            'state_rate': 0.0,
        }

    year = sale.sale_date.year if sale.sale_date else date.today().year
    if profile is None:
        profile = resolve_engine_profile_for_year(user, year)
    else:
        profile = dict(profile)
        profile['tax_year'] = year

    analysis = analyze_sales(profile, [lot])
    niit = float(analysis.niit or 0)
    fed = max(0.0, float(analysis.federal_tax_total or 0) - niit)
    state = float(analysis.state_tax or 0)
    total = float(analysis.total_tax or 0)
    gain = float(sale.capital_gain or 0)
    if gain <= 0:
        gain = max(0.0, float(analysis.stcg or 0) + float(analysis.ltcg or 0) + float(analysis.equity_ordinary or 0))

    holding_days = 0
    if sale.sale_date and vest.vest_date:
        holding_days = (sale.sale_date - vest.vest_date).days

    rates = analysis.rates_used or {}
    return {
        'estimated_federal': fed,
        'estimated_niit': niit,
        'estimated_state': state,
        'estimated_total': total,
        'federal_rate': float(rates.get('ltcg') or rates.get('ordinary_marginal') or 0),
        'niit_rate': 0.038 if niit > 0 else 0.0,
        'state_rate': float(
            rates.get('state_effective')
            or rates.get('state_marginal')
            or rates.get('state_ordinary')
            or 0
        ),
        'effective_rate': (total / gain) if gain > 0 else float(analysis.effective_rate_on_gain or 0),
        'is_long_term': bool(sale.is_long_term),
        'holding_days': holding_days,
        'method': 'engine',
        'tax_year': analysis.tax_year,
        'fica_tax': float(analysis.fica_tax or 0),
        'warnings': list(analysis.warnings or [])[:4],
    }
