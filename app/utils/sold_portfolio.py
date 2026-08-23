"""
Sold-shares portfolio: realized sales, opportunity cost, tax set-aside inputs.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from app.models.stock_sale import StockSale
from app.utils.shares import whole_shares


def build_sold_portfolio(
    user,
    *,
    live_price: float,
    tax_year: Optional[int] = None,
    sales: Optional[List[StockSale]] = None,
) -> Dict[str, Any]:
    """
    Aggregate StockSale rows with mark-to-market opportunity cost.

    tax_year: if set, filter sales to that calendar year (for KPIs / set-aside).
    Table rows still include all sales unless year_filter applied by caller.
    """
    tax_year = int(tax_year or date.today().year)
    all_sales = sales
    if all_sales is None:
        all_sales = (
            StockSale.query.filter_by(user_id=user.id)
            .order_by(StockSale.sale_date.desc())
            .all()
        )

    rows: List[Dict[str, Any]] = []
    year_sales: List[StockSale] = []

    for s in all_sales:
        sh = float(s.shares_sold or 0)
        px = float(s.sale_price or 0)
        proceeds = float(s.total_proceeds if s.total_proceeds is not None else sh * px)
        basis = float(s.total_cost_basis or 0)
        gain = float(s.capital_gain if s.capital_gain is not None else proceeds - basis)
        worth_now = sh * float(live_price or 0)
        delta = worth_now - proceeds
        y = s.sale_date.year if s.sale_date else None
        if y == tax_year:
            year_sales.append(s)

        est_tax = None
        try:
            est = s.get_estimated_tax(user=user) or {}
            est_tax = float(est.get('estimated_tax') or est.get('estimated_total') or 0)
        except Exception:
            est_tax = None

        rows.append({
            'id': s.id,
            'sale_date': s.sale_date,
            'vest_event_id': s.vest_event_id,
            'shares': sh,
            'sale_price': px,
            'proceeds': proceeds,
            'cost_basis': basis,
            'capital_gain': gain,
            'is_long_term': bool(s.is_long_term),
            'commission': float(s.commission_fees or 0),
            'notes': s.notes or '',
            'worth_now': worth_now,
            'delta_vs_sale': delta,
            'estimated_tax': est_tax,
            'year': y,
        })

    def _sum_year(key_from_sale=None, key_from_row=None):
        # Prefer year_sales for aggregates tied to tax year
        return 0.0

    shares_y = sum(float(s.shares_sold or 0) for s in year_sales)
    proceeds_y = sum(
        float(s.total_proceeds if s.total_proceeds is not None else (s.shares_sold or 0) * (s.sale_price or 0))
        for s in year_sales
    )
    gain_y = sum(float(s.capital_gain or 0) for s in year_sales)
    worth_now_y = shares_y * float(live_price or 0)
    forgone_y = worth_now_y - proceeds_y  # positive = would be worth more if held

    # Per-row est tax sum for year (detail); stacked estimate preferred in calendar module
    est_tax_sum_y = 0.0
    for r in rows:
        if r.get('year') == tax_year and r.get('estimated_tax') is not None:
            est_tax_sum_y += float(r['estimated_tax'])

    shares_all = sum(float(s.shares_sold or 0) for s in all_sales)
    proceeds_all = sum(
        float(s.total_proceeds if s.total_proceeds is not None else (s.shares_sold or 0) * (s.sale_price or 0))
        for s in all_sales
    )
    worth_now_all = shares_all * float(live_price or 0)

    return {
        'tax_year': tax_year,
        'live_price': float(live_price or 0),
        'rows': rows,
        'year_sales': year_sales,
        'summary_year': {
            'shares': whole_shares(shares_y),
            'proceeds': float(proceeds_y),
            'capital_gain': float(gain_y),
            'worth_now': float(worth_now_y),
            'forgone_vs_sale': float(forgone_y),
            'estimated_tax_sum': float(est_tax_sum_y),
            'sale_count': len(year_sales),
        },
        'summary_all': {
            'shares': whole_shares(shares_all),
            'proceeds': float(proceeds_all),
            'worth_now': float(worth_now_all),
            'forgone_vs_sale': float(worth_now_all - proceeds_all),
            'sale_count': len(all_sales),
        },
    }
