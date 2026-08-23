"""
Estimated tax reserve + quarterly calendar + simplified federal safe harbor.

Guidance only — not Form 2210 annualization or CPA advice.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from app.utils.sale_tax_estimate import lot_input_from_vest
from app.utils.tax_engine import analyze_sales, resolve_engine_profile_for_year
from app.utils.shares import whole_shares


def federal_estimated_due_dates(tax_year: int) -> List[Dict[str, Any]]:
    """
    Federal individual estimated tax due dates for ``tax_year`` income.
    Q4 is Jan 15 of tax_year+1.
    """
    # Weekend/holiday adjustment omitted (CPA tools handle that); show statutory dates.
    return [
        {'quarter': 1, 'label': 'Q1', 'due': date(tax_year, 4, 15), 'period_end': date(tax_year, 3, 31)},
        {'quarter': 2, 'label': 'Q2', 'due': date(tax_year, 6, 15), 'period_end': date(tax_year, 5, 31)},
        {'quarter': 3, 'label': 'Q3', 'due': date(tax_year, 9, 15), 'period_end': date(tax_year, 8, 31)},
        {'quarter': 4, 'label': 'Q4', 'due': date(tax_year + 1, 1, 15), 'period_end': date(tax_year, 12, 31)},
    ]


def safe_harbor_targets(
    *,
    prior_year_total_tax: float,
    prior_year_agi: Optional[float],
    current_year_estimated_tax: float,
) -> Dict[str, Any]:
    """
    Simplified federal safe harbor:
    - 100% of prior-year tax, or 110% if prior AGI > $150k
    - vs 90% of current-year estimated tax
    Pay the lower of those floors across the year to generally avoid underpayment penalty
    (exceptions / annualization not modeled).
    """
    prior = max(0.0, float(prior_year_total_tax or 0))
    cur = max(0.0, float(current_year_estimated_tax or 0))
    agi = float(prior_year_agi) if prior_year_agi is not None else None
    high_agi = agi is not None and agi > 150_000
    prior_pct = 1.10 if high_agi else 1.00
    prior_target = prior * prior_pct
    current_90 = cur * 0.90
    # Required annual payment to meet a safe harbor is the *minimum* of the two tests
    # when both are available; if no prior-year tax, use 90% current only.
    if prior > 0 and cur > 0:
        required = min(prior_target, current_90)
        which = 'prior_year' if prior_target <= current_90 else 'current_90'
    elif prior > 0:
        required = prior_target
        which = 'prior_year'
    else:
        required = current_90
        which = 'current_90'

    return {
        'prior_year_tax': prior,
        'prior_year_agi': agi,
        'high_agi_110': high_agi,
        'prior_year_safe_harbor': prior_target,
        'current_year_90pct': current_90,
        'required_annual': required,
        'binding_rule': which,
        'per_quarter': required / 4.0 if required else 0.0,
    }


def stacked_tax_on_sales(user, sales: List, tax_year: int) -> Dict[str, Any]:
    """One stacked analyze_sales for all sales in tax_year (preferred set-aside)."""
    from sqlalchemy.orm import joinedload
    from app.models.vest_event import VestEvent

    lots = []
    for sale in sales or []:
        if not sale.sale_date or sale.sale_date.year != tax_year:
            continue
        vest = sale.vest_event
        if vest is None and sale.vest_event_id:
            vest = VestEvent.query.options(joinedload(VestEvent.grant)).get(sale.vest_event_id)
        if not vest or not vest.grant:
            continue
        basis = float(sale.cost_basis_per_share or 0)
        lot = lot_input_from_vest(
            vest,
            shares=whole_shares(sale.shares_sold),
            sale_price=float(sale.sale_price or 0),
            sale_date=sale.sale_date,
            cost_basis_per_share=basis,
            user_id=user.id,
            label=f'Sale#{sale.id}',
        )
        if lot:
            lots.append(lot)

    if not lots:
        return {
            'total_tax': 0.0,
            'federal_tax': 0.0,
            'state_tax': 0.0,
            'niit': 0.0,
            'lot_count': 0,
            'method': 'none',
        }

    profile = resolve_engine_profile_for_year(user, tax_year)
    analysis = analyze_sales(profile, lots)
    niit = float(analysis.niit or 0)
    return {
        'total_tax': float(analysis.total_tax or 0),
        'federal_tax': max(0.0, float(analysis.federal_tax_total or 0) - niit),
        'state_tax': float(analysis.state_tax or 0),
        'niit': niit,
        'fica': float(analysis.fica_tax or 0),
        'amt_due': float(analysis.amt_due or 0),
        'lot_count': len(lots),
        'method': 'stacked_analyze_sales',
        'analysis': analysis,
    }


def build_estimated_tax_calendar(
    user,
    *,
    tax_year: Optional[int] = None,
    sales: Optional[List] = None,
    profile=None,
    as_of: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Full calendar payload for Sold tab.

    profile: TaxProfile ORM (optional) for withholding / prior-year fields.
    """
    as_of = as_of or date.today()
    tax_year = int(tax_year or as_of.year)

    if sales is None:
        from app.models.stock_sale import StockSale
        sales = StockSale.query.filter_by(user_id=user.id).all()

    stacked = stacked_tax_on_sales(user, sales, tax_year)
    equity_tax = float(stacked.get('total_tax') or 0)

    prior_tax = float(getattr(profile, 'prior_year_total_tax', None) or 0) if profile else 0.0
    prior_agi = getattr(profile, 'prior_year_agi', None) if profile else None
    try:
        prior_agi = float(prior_agi) if prior_agi is not None else None
    except (TypeError, ValueError):
        prior_agi = None

    fed_wh = float(getattr(profile, 'federal_withholding_ytd', None) or 0) if profile else 0.0
    state_wh = float(getattr(profile, 'state_withholding_ytd', None) or 0) if profile else 0.0
    est_paid = float(getattr(profile, 'estimated_payments_ytd', None) or 0) if profile else 0.0
    credits = fed_wh + state_wh + est_paid

    # Conservative: equity sales tax is incremental; credits may cover wages too.
    # "Still to save" for equity gains = equity tax minus payments user attributes here.
    still_to_save = max(0.0, equity_tax - credits)

    harbor = safe_harbor_targets(
        prior_year_total_tax=prior_tax,
        prior_year_agi=prior_agi,
        current_year_estimated_tax=equity_tax,  # simplified: equity slice; wages not fully modeled here
    )

    quarters = []
    for q in federal_estimated_due_dates(tax_year):
        due = q['due']
        past = due <= as_of
        quarters.append({
            **q,
            'due_iso': due.isoformat(),
            'due_label': due.strftime('%b %d, %Y').replace(' 0', ' '),
            'is_past': past,
            'is_next': False,
            'suggested_payment': 0.0,
            'safe_harbor_quarter': float(harbor['per_quarter'] or 0),
        })

    # Front-load remaining set-aside onto the next federal due date.
    # Equal-splitting Q3/Q4 made August sale tax look like two identical bills;
    # for lumpy equity gains, catch up on the next installment instead.
    for q in quarters:
        if not q['is_past']:
            q['is_next'] = True
            q['suggested_payment'] = still_to_save
            break

    return {
        'tax_year': tax_year,
        'as_of': as_of.isoformat(),
        'equity_tax_estimate': equity_tax,
        'tax_breakdown': {
            'federal': stacked.get('federal_tax', 0),
            'state': stacked.get('state_tax', 0),
            'niit': stacked.get('niit', 0),
            'fica': stacked.get('fica', 0),
            'amt': stacked.get('amt_due', 0),
            'method': stacked.get('method'),
            'lot_count': stacked.get('lot_count', 0),
        },
        'credits': {
            'federal_withholding_ytd': fed_wh,
            'state_withholding_ytd': state_wh,
            'estimated_payments_ytd': est_paid,
            'total': credits,
        },
        'still_to_save': still_to_save,
        'safe_harbor': harbor,
        'quarters': quarters,
    }
