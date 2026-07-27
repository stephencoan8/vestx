"""
Liquidity / sell-to-cover solver for equity events.

Answers: "How many shares must I sell (cashless DD or RSU) so that after-tax
proceeds cover the strike and/or tax cash needed for the rest of the plan?"

Uses iterative search over sell quantity; tax is recomputed each step via the
equity planner / tax engine so brackets, AMT, CA, and NIIT stay consistent.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, asdict, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from app.utils.equity_planner import (
    LotSpec,
    plan_iso_cashless_dd,
    plan_iso_exercise_hold,
    plan_mixed_default,
    plan_rsu_sell,
    _build_sale,
    _build_exercise,
    _profile_for_year,
)
from app.utils.tax_engine import analyze_sales, LotSaleInput, ExerciseInput


@dataclass
class CoverResult:
    success: bool
    mode: str
    price: float
    shares_to_sell: float
    shares_to_hold: float
    shares_total: float
    strike_outlay_held: float
    sale_proceeds: float
    incremental_tax_on_sale: float
    tax_on_hold_path: float  # AMT etc. on held exercise
    total_cash_needed: float
    net_cash_after: float
    shortfall: float
    iterations: int
    notes: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _scale_lots(lots: Sequence[LotSpec], shares: float) -> List[LotSpec]:
    """Allocate `shares` across lots pro-rata by original shares."""
    total = sum(l.shares for l in lots)
    if total <= 0 or shares <= 0:
        return []
    if shares >= total - 1e-9:
        return [deepcopy(l) for l in lots]

    remaining = shares
    out: List[LotSpec] = []
    for i, lot in enumerate(lots):
        if i == len(lots) - 1:
            take = remaining
        else:
            take = min(remaining, lot.shares * (shares / total))
            take = min(take, lot.shares)
        if take > 1e-9:
            nl = deepcopy(lot)
            nl.shares = take
            out.append(nl)
            remaining -= take
    return out


def _iso_lots(lots: Sequence[LotSpec]) -> List[LotSpec]:
    return [l for l in lots if l.is_iso and l.shares > 0]


def _rsu_lots(lots: Sequence[LotSpec]) -> List[LotSpec]:
    return [l for l in lots if (not l.is_iso) and l.shares > 0]


def solve_iso_exercise_sell_to_cover(
    profile: dict,
    lots: Sequence[LotSpec],
    *,
    exercise_date: date,
    fmv: float,
    cover_strike: bool = True,
    cover_tax: bool = True,
    max_iter: int = 28,
) -> CoverResult:
    """
    Exercise *all* selected ISO shares; sell the minimum cashless DD fraction
    so net cash from the sale funds strike on the *held* shares (+ optional AMT/tax).

    Classic "exercise and sell to cover" partial cashless.
    """
    iso = _iso_lots(lots)
    if not iso:
        return CoverResult(
            success=False, mode='iso_sell_to_cover', price=fmv,
            shares_to_sell=0, shares_to_hold=0, shares_total=0,
            strike_outlay_held=0, sale_proceeds=0, incremental_tax_on_sale=0,
            tax_on_hold_path=0, total_cash_needed=0, net_cash_after=0,
            shortfall=0, iterations=0,
            notes=['No ISO lots with shares selected.'],
        )

    total_shares = sum(l.shares for l in iso)
    strike_w = sum(l.strike_price * l.shares for l in iso) / total_shares  # avg

    def evaluate(sell_shares: float) -> Dict[str, float]:
        sell_shares = max(0.0, min(total_shares, sell_shares))
        hold_shares = total_shares - sell_shares
        sell_lots = _scale_lots(iso, sell_shares)
        hold_lots = _scale_lots(iso, hold_shares)

        sale_proceeds = 0.0
        tax_sale = 0.0
        if sell_lots:
            p_sale = plan_iso_cashless_dd(
                profile, sell_lots, event_date=exercise_date, price=fmv
            )
            sale_proceeds = p_sale.cash.sale_gross_proceeds
            tax_sale = p_sale.total_incremental_tax

        tax_hold = 0.0
        strike_hold = 0.0
        if hold_lots:
            p_hold = plan_iso_exercise_hold(
                profile, hold_lots, exercise_date=exercise_date, fmv=fmv
            )
            tax_hold = p_hold.total_incremental_tax  # mainly federal+CA AMT
            strike_hold = p_hold.cash.exercise_cash_outlay

        # Cash needed: strike on held (+ optionally tax on held). Sale must also
        # cover its own tax (net proceeds = proceeds - tax_sale - strike on sold).
        # For sold shares cashless: net = proceeds - tax - strike_sold.
        strike_sold = sum(l.strike_price * l.shares for l in sell_lots)
        net_from_sale = sale_proceeds - tax_sale - strike_sold

        need = 0.0
        if cover_strike:
            need += strike_hold
        if cover_tax:
            need += tax_hold

        surplus = net_from_sale - need
        return {
            'sell': sell_shares,
            'hold': hold_shares,
            'sale_proceeds': sale_proceeds,
            'tax_sale': tax_sale,
            'tax_hold': tax_hold,
            'strike_hold': strike_hold,
            'strike_sold': strike_sold,
            'net_from_sale': net_from_sale,
            'need': need,
            'surplus': surplus,
        }

    # Binary search minimum sell shares so surplus >= 0
    # Edge: sell none
    e0 = evaluate(0.0)
    if e0['need'] <= 0 or e0['surplus'] >= -0.5:
        return CoverResult(
            success=True,
            mode='iso_sell_to_cover',
            price=fmv,
            shares_to_sell=0,
            shares_to_hold=total_shares,
            shares_total=total_shares,
            strike_outlay_held=e0['strike_hold'],
            sale_proceeds=0,
            incremental_tax_on_sale=0,
            tax_on_hold_path=e0['tax_hold'],
            total_cash_needed=e0['need'],
            net_cash_after=e0['surplus'],
            shortfall=0,
            iterations=1,
            notes=[
                'No sale required: you can fund strike/tax from other cash (or nothing due).',
                'Or need is covered without selling (check AMT estimates carefully).',
            ],
            detail=e0,
        )

    # Sell all
    e_all = evaluate(total_shares)
    if e_all['surplus'] < -0.5:
        return CoverResult(
            success=False,
            mode='iso_sell_to_cover',
            price=fmv,
            shares_to_sell=total_shares,
            shares_to_hold=0,
            shares_total=total_shares,
            strike_outlay_held=0,
            sale_proceeds=e_all['sale_proceeds'],
            incremental_tax_on_sale=e_all['tax_sale'],
            tax_on_hold_path=0,
            total_cash_needed=e_all['need'],
            net_cash_after=e_all['surplus'],
            shortfall=-e_all['surplus'],
            iterations=1,
            notes=[
                'Even selling 100% cashless does not leave surplus after tax/strike — '
                'price may be near strike, or tax rate is high. Need external cash.',
            ],
            detail=e_all,
        )

    lo, hi = 0.0, total_shares
    best = e_all
    for i in range(max_iter):
        mid = (lo + hi) / 2
        e = evaluate(mid)
        if e['surplus'] >= 0:
            best = e
            hi = mid
        else:
            lo = mid

    notes = [
        f'Sell ~{best["sell"]:,.2f} of {total_shares:,.2f} shares cashless (DD) to cover '
        f'{"strike" if cover_strike else ""}'
        f'{" + " if cover_strike and cover_tax else ""}'
        f'{"tax/AMT on held" if cover_tax else ""} on remaining {best["hold"]:,.2f} shares.',
        'Sold tranche is same-day DD (ordinary income). Held tranche is exercise-and-hold (AMT preference).',
        'Re-check with your CPA before exercising — AMT estimates are planning-grade.',
    ]
    return CoverResult(
        success=True,
        mode='iso_sell_to_cover',
        price=fmv,
        shares_to_sell=best['sell'],
        shares_to_hold=best['hold'],
        shares_total=total_shares,
        strike_outlay_held=best['strike_hold'],
        sale_proceeds=best['sale_proceeds'],
        incremental_tax_on_sale=best['tax_sale'],
        tax_on_hold_path=best['tax_hold'],
        total_cash_needed=best['need'],
        net_cash_after=best['surplus'],
        shortfall=0 if best['surplus'] >= 0 else -best['surplus'],
        iterations=max_iter,
        notes=notes,
        detail=best,
    )


def solve_rsu_sell_to_fund_iso(
    profile: dict,
    rsu_lots: Sequence[LotSpec],
    iso_lots: Sequence[LotSpec],
    *,
    sale_date: date,
    sale_price: float,
    exercise_date: date,
    exercise_fmv: float,
    cover_strike: bool = True,
    cover_tax: bool = True,
    max_iter: int = 28,
) -> CoverResult:
    """
    Sell minimum RSU shares so after-tax RSU proceeds fund ISO exercise-and-hold
    (strike + optional AMT).
    """
    rsus = _rsu_lots(rsu_lots)
    isos = _iso_lots(iso_lots)
    if not rsus:
        return CoverResult(
            success=False, mode='rsu_fund_iso', price=sale_price,
            shares_to_sell=0, shares_to_hold=0, shares_total=0,
            strike_outlay_held=0, sale_proceeds=0, incremental_tax_on_sale=0,
            tax_on_hold_path=0, total_cash_needed=0, net_cash_after=0,
            shortfall=0, iterations=0,
            notes=['No RSU lots to sell for funding.'],
        )
    if not isos:
        return CoverResult(
            success=False, mode='rsu_fund_iso', price=sale_price,
            shares_to_sell=0, shares_to_hold=0, shares_total=0,
            strike_outlay_held=0, sale_proceeds=0, incremental_tax_on_sale=0,
            tax_on_hold_path=0, total_cash_needed=0, net_cash_after=0,
            shortfall=0, iterations=0,
            notes=['No ISO lots to fund.'],
        )

    p_hold = plan_iso_exercise_hold(
        profile, isos, exercise_date=exercise_date, fmv=exercise_fmv
    )
    need = 0.0
    if cover_strike:
        need += p_hold.cash.exercise_cash_outlay
    if cover_tax:
        need += p_hold.total_incremental_tax

    total_rsu = sum(l.shares for l in rsus)

    def evaluate(sell_shares: float) -> Dict[str, float]:
        sell_shares = max(0.0, min(total_rsu, sell_shares))
        if sell_shares <= 0:
            return {
                'sell': 0, 'proceeds': 0, 'tax': 0, 'net': 0,
                'need': need, 'surplus': -need,
            }
        scaled = _scale_lots(rsus, sell_shares)
        p = plan_rsu_sell(profile, scaled, sale_date=sale_date, sale_price=sale_price)
        net = p.cash.sale_gross_proceeds - p.total_incremental_tax
        return {
            'sell': sell_shares,
            'proceeds': p.cash.sale_gross_proceeds,
            'tax': p.total_incremental_tax,
            'net': net,
            'need': need,
            'surplus': net - need,
        }

    e0 = evaluate(0)
    if need <= 0:
        return CoverResult(
            success=True, mode='rsu_fund_iso', price=sale_price,
            shares_to_sell=0, shares_to_hold=total_rsu, shares_total=total_rsu,
            strike_outlay_held=p_hold.cash.exercise_cash_outlay,
            sale_proceeds=0, incremental_tax_on_sale=0,
            tax_on_hold_path=p_hold.total_incremental_tax,
            total_cash_needed=0, net_cash_after=0, shortfall=0, iterations=1,
            notes=['No cash needed for ISO path under current inputs.'],
            detail=e0,
        )

    e_all = evaluate(total_rsu)
    if e_all['surplus'] < 0:
        return CoverResult(
            success=False, mode='rsu_fund_iso', price=sale_price,
            shares_to_sell=total_rsu, shares_to_hold=0, shares_total=total_rsu,
            strike_outlay_held=p_hold.cash.exercise_cash_outlay,
            sale_proceeds=e_all['proceeds'], incremental_tax_on_sale=e_all['tax'],
            tax_on_hold_path=p_hold.total_incremental_tax,
            total_cash_needed=need, net_cash_after=e_all['surplus'],
            shortfall=-e_all['surplus'], iterations=1,
            notes=[
                f'Selling all RSUs still short ${-e_all["surplus"]:,.0f} of ISO strike+tax. '
                'Need more RSUs, higher price, or external cash.',
            ],
            detail={**e_all, 'iso_hold': p_hold.to_dict()},
        )

    lo, hi = 0.0, total_rsu
    best = e_all
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        e = evaluate(mid)
        if e['surplus'] >= 0:
            best = e
            hi = mid
        else:
            lo = mid

    return CoverResult(
        success=True,
        mode='rsu_fund_iso',
        price=sale_price,
        shares_to_sell=best['sell'],
        shares_to_hold=total_rsu - best['sell'],
        shares_total=total_rsu,
        strike_outlay_held=p_hold.cash.exercise_cash_outlay,
        sale_proceeds=best['proceeds'],
        incremental_tax_on_sale=best['tax'],
        tax_on_hold_path=p_hold.total_incremental_tax,
        total_cash_needed=need,
        net_cash_after=best['surplus'],
        shortfall=0,
        iterations=max_iter,
        notes=[
            f'Sell ~{best["sell"]:,.2f} RSU shares at ${sale_price:.2f} to fund ISO exercise-and-hold.',
            f'ISO cash need ≈ ${need:,.0f} (strike ${p_hold.cash.exercise_cash_outlay:,.0f} + tax ${p_hold.total_incremental_tax:,.0f}).',
            'RSU sale and ISO exercise in the same year stack on brackets — modeled together only approximately (separate plans).',
        ],
        detail={**best, 'iso_plan': {
            'outlay': p_hold.cash.exercise_cash_outlay,
            'tax': p_hold.total_incremental_tax,
        }},
    )


def run_liquidity(
    profile: dict,
    lots: Sequence[LotSpec],
    *,
    mode: str,
    sale_date: date,
    sale_price: float,
    exercise_date: date,
    exercise_fmv: float,
    cover_strike: bool = True,
    cover_tax: bool = True,
) -> Dict[str, Any]:
    mode = (mode or 'iso_sell_to_cover').lower()
    if mode in ('iso_sell_to_cover', 'sell_to_cover', 'cover'):
        r = solve_iso_exercise_sell_to_cover(
            profile,
            lots,
            exercise_date=exercise_date,
            fmv=exercise_fmv or sale_price,
            cover_strike=cover_strike,
            cover_tax=cover_tax,
        )
    elif mode in ('rsu_fund_iso', 'rsu_cover'):
        r = solve_rsu_sell_to_fund_iso(
            profile,
            _rsu_lots(lots),
            _iso_lots(lots),
            sale_date=sale_date,
            sale_price=sale_price,
            exercise_date=exercise_date,
            exercise_fmv=exercise_fmv or sale_price,
            cover_strike=cover_strike,
            cover_tax=cover_tax,
        )
    else:
        return {'success': False, 'error': f'Unknown liquidity mode: {mode}'}
    return {'success': r.success, 'cover': r.to_dict()}
