"""
Account snapshots for Grok advisor.

Default: **full inventory every turn** as dense TSV (hundreds of rows is fine —
typically a few thousand tokens, not tens of thousands).

Also supports optional compact mode via pack_context_for_prompt(..., mode='compact').
ENGINE_RESULT from the deterministic router is prepended separately by the API.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from flask_login import current_user


def _n(x, d=4) -> str:
    try:
        if x is None:
            return ''
        v = float(x)
        if abs(v - round(v)) < 1e-9:
            return str(int(round(v)))
        return f'{v:.{d}f}'.rstrip('0').rstrip('.')
    except (TypeError, ValueError):
        return str(x) if x is not None else ''


def _d(x) -> str:
    if x is None:
        return ''
    if hasattr(x, 'isoformat'):
        return x.isoformat()[:10]
    s = str(x)
    return s[:10] if len(s) >= 10 else s


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def build_account_context(user_id: Optional[int] = None, *, max_lots: int = 500) -> Dict[str, Any]:
    """Load full account structures for packing."""
    from app.models.tax_profile import TaxProfile
    from app.models.grant import Grant
    from app.models.stock_sale import StockSale, ISOExercise
    from app.utils.lot_inventory import build_lots_for_user
    from app.utils.price_utils import get_latest_user_price

    from app.models.user import User

    uid = user_id
    if not uid:
        try:
            if getattr(current_user, 'is_authenticated', False):
                uid = current_user.id
        except Exception:
            uid = None
    if not uid:
        return {'error': 'not authenticated'}

    # Prefer explicit User row — never assume request-local current_user works
    user = User.query.get(uid)
    if user is None:
        return {'error': 'user not found'}

    try:
        profile = TaxProfile.for_user(user)
        eng = profile.to_engine_dict() if profile else {}
    except Exception:
        eng = {}

    try:
        live = get_latest_user_price(uid) or 0.0
    except Exception:
        live = 0.0

    try:
        lots = build_lots_for_user(uid) or []
    except Exception:
        lots = []

    try:
        grants = Grant.query.filter_by(user_id=uid).order_by(Grant.grant_date.desc()).all()
    except Exception:
        grants = []

    try:
        sales = (
            StockSale.query.filter_by(user_id=uid)
            .order_by(StockSale.sale_date.desc())
            .limit(100)
            .all()
        )
    except Exception:
        sales = []

    try:
        exercises = (
            ISOExercise.query.filter_by(user_id=uid)
            .order_by(ISOExercise.exercise_date.desc())
            .limit(100)
            .all()
        )
    except Exception:
        exercises = []

    total_held = sum(float(l.get('shares_available') or 0) for l in lots)
    total_unex = sum(float(l.get('shares_unexercised') or 0) for l in lots)

    try:
        has_key = bool(user.has_xai_api_key())
    except Exception:
        has_key = False

    return {
        'as_of': date.today().isoformat(),
        'username': user.username if user else None,
        'live_price': live,
        'tax_profile': eng,
        'portfolio_summary': {
            'grant_count': len(grants),
            'lot_count': len(lots),
            'shares_held_sellable': total_held,
            'shares_unexercised_iso': total_unex,
            'recorded_sales': len(sales),
            'recorded_exercises': len(exercises),
            'approx_held_value': total_held * live if live else None,
        },
        'grants': grants,
        'lots': lots[:max_lots],
        'recent_sales': sales,
        'recent_exercises': exercises,
        'capabilities': {
            'goal_optimizer': True,
            'state_tax_ca': (eng.get('state_code') or '').upper() == 'CA',
            'has_xai_key': has_key,
        },
    }


def _profile_block(eng: dict) -> str:
    keys = [
        'filing_status', 'state_code', 'tax_year',
        'other_ordinary_income', 'other_long_term_gains', 'other_short_term_gains',
        'ytd_wages', 'ss_wage_base_maxed',
        'include_fica', 'include_niit',
        'use_bracket_engine', 'use_state_engine',
        'federal_ordinary_rate', 'federal_ltcg_rate',
        'state_ordinary_rate', 'state_cg_rate',
        'amt_credit_carryforward', 'ca_amt_credit_carryforward',
    ]
    lines = ['## TAX_PROFILE']
    for k in keys:
        v = eng.get(k)
        if v is None or v == '':
            continue
        if isinstance(v, float) and k.endswith('_rate') and 0 < abs(v) < 1:
            lines.append(f'{k}={v:.4f}')
        else:
            lines.append(f'{k}={_n(v) if isinstance(v, (int, float)) else v}')
    return '\n'.join(lines)


def _lots_tsv(lots: Sequence[dict]) -> str:
    """
    Full SpecID table — one row per vest lot.
    Columns chosen for tax planning accuracy.
    """
    header = (
        'vest_id\tshare_type\tiso\theld\tunex\tbasis\tstrike\t'
        'lt\thold_days\tvest\tgrant\tex_date\tfmv_ex\tur_gain\tlabel'
    )
    rows = [header]
    for lot in lots:
        rows.append(
            '\t'.join([
                str(lot.get('vest_event_id') or ''),
                str(lot.get('share_type') or ''),
                '1' if lot.get('is_iso') else '0',
                _n(lot.get('shares_available')),
                _n(lot.get('shares_unexercised')),
                _n(lot.get('cost_basis_per_share')),
                _n(lot.get('strike_price') if lot.get('strike_price') is not None else ''),
                '1' if lot.get('is_long_term') else '0',
                _n(lot.get('holding_days'), 0),
                _d(lot.get('vest_date')),
                _d(lot.get('grant_date')),
                _d(lot.get('exercise_date')),
                _n(lot.get('fmv_at_exercise') if lot.get('fmv_at_exercise') is not None else ''),
                _n(lot.get('unrealized_gain'), 2),
                str(lot.get('label') or '').replace('\t', ' ')[:60],
            ])
        )
    return '## LOTS_TSV (all tax lots / SpecID)\n' + '\n'.join(rows)


def _grants_tsv(grants) -> str:
    header = 'grant_id\ttype\tshare_type\tgrant_date\tqty\tpx_grant\tvest_y\tcliff_y'
    rows = [header]
    for g in grants:
        rows.append(
            '\t'.join([
                str(g.id),
                str(g.grant_type or ''),
                str(g.share_type or ''),
                _d(g.grant_date),
                _n(g.share_quantity),
                _n(g.share_price_at_grant),
                _n(g.vest_years, 0),
                _n(g.cliff_years, 2),
            ])
        )
    return '## GRANTS_TSV\n' + '\n'.join(rows)


def _sales_tsv(sales) -> str:
    header = 'sale_id\tdate\tvest_id\tshares\tpx\tproceeds\tgain\tlt\tiso_qd'
    rows = [header]
    for s in sales:
        rows.append(
            '\t'.join([
                str(s.id),
                _d(s.sale_date),
                str(s.vest_event_id or ''),
                _n(s.shares_sold),
                _n(s.sale_price),
                _n(s.total_proceeds, 2),
                _n(s.capital_gain, 2),
                '1' if s.is_long_term else '0',
                '' if s.is_qualifying_disposition is None else ('1' if s.is_qualifying_disposition else '0'),
            ])
        )
    return '## SALES_TSV (recent)\n' + '\n'.join(rows)


def _exercises_tsv(exercises) -> str:
    header = 'ex_id\tdate\tvest_id\tshares\tstrike\tfmv\tbargain\tstill_held'
    rows = [header]
    for e in exercises:
        rows.append(
            '\t'.join([
                str(e.id),
                _d(e.exercise_date),
                str(e.vest_event_id or ''),
                _n(e.shares_exercised),
                _n(e.strike_price),
                _n(e.fmv_at_exercise),
                _n(e.total_bargain_element, 2),
                _n(e.shares_still_held),
            ])
        )
    return '## EXERCISES_TSV (recent)\n' + '\n'.join(rows)


def _plan_compact(plan: Optional[dict]) -> str:
    if not plan:
        return '## PLAN\nnone'
    if plan.get('picks') is not None or plan.get('achieved_net_cash') is not None:
        lines = [
            '## PLAN (deterministic goal engine)',
            f"success={plan.get('success')} target={_n((plan.get('goal') or {}).get('target_net_cash'))} "
            f"achieved_net={_n(plan.get('achieved_net_cash'))} tax={_n(plan.get('total_tax'))} "
            f"shortfall={_n(plan.get('shortfall'))} proceeds={_n(plan.get('total_proceeds'))}",
            'picks_tsv: vest_id\taction\tshares\tprice\tbasis\tlt\tdisp\treason',
        ]
        for p in (plan.get('picks') or []):
            lines.append(
                '\t'.join([
                    str(p.get('vest_event_id') or ''),
                    str(p.get('action') or ''),
                    _n(p.get('shares')),
                    _n(p.get('price')),
                    _n(p.get('basis_or_strike')),
                    '1' if p.get('is_long_term') else '0',
                    str(p.get('iso_disposition') or ''),
                    str(p.get('reason') or '').replace('\t', ' ')[:80],
                ])
            )
        return '\n'.join(lines)
    if plan.get('cash') or plan.get('strategy'):
        cash = plan.get('cash') or {}
        return (
            f"## PLAN strategy={plan.get('strategy') or plan.get('name')} "
            f"net={_n(cash.get('net_cash') or plan.get('total_net_cash'))} "
            f"tax={_n(cash.get('incremental_tax') or plan.get('total_incremental_tax'))}"
        )
    import json
    return '## PLAN\n' + json.dumps(plan, separators=(',', ':'), default=str)[:2000]


def pack_context_for_prompt(
    user_id: Optional[int] = None,
    *,
    user_message: str = '',
    plan: Optional[dict] = None,
    mode: str = 'full',
    max_lot_lines: int = 18,
) -> Dict[str, Any]:
    """
    mode='full' (default): entire lot table + grants + sales + exercises + profile.
    mode='compact': aggregates + top lots (legacy cheaper path).
    """
    raw = build_account_context(user_id)
    if raw.get('error'):
        return {'text': 'ACCOUNT\nerror=not_authenticated', 'meta': {'error': raw['error']}}

    eng = raw.get('tax_profile') or {}
    lots = raw.get('lots') or []
    price = float(raw.get('live_price') or 0)
    ps = raw.get('portfolio_summary') or {}

    if mode == 'compact':
        # Minimal path kept for optional callers
        from collections import defaultdict
        lines = [
            f"## SNAPSHOT as_of={raw.get('as_of')} px={_n(price)} "
            f"held={_n(ps.get('shares_held_sellable'))} unexISO={_n(ps.get('shares_unexercised_iso'))} "
            f"lots={ps.get('lot_count')} val~{_n(ps.get('approx_held_value'))}",
            _profile_block(eng),
        ]
        # top lots only
        ranked = sorted(
            lots,
            key=lambda l: -(
                float(l.get('shares_available') or 0) * price
                + float(l.get('shares_unexercised') or 0) * price
            ),
        )[:max_lot_lines]
        lines.append(_lots_tsv(ranked))
        lines.append(_plan_compact(plan))
        text = '\n'.join(lines)
    else:
        # Full feed — default for chat quality
        blocks = [
            f"## SNAPSHOT as_of={raw.get('as_of')} live_price={_n(price)} "
            f"held_sh={_n(ps.get('shares_held_sellable'))} unex_iso_sh={_n(ps.get('shares_unexercised_iso'))} "
            f"lot_count={ps.get('lot_count')} grant_count={ps.get('grant_count')} "
            f"approx_held_value={_n(ps.get('approx_held_value'))}",
            _profile_block(eng),
            _grants_tsv(raw.get('grants') or []),
            _lots_tsv(lots),
            _sales_tsv(raw.get('recent_sales') or []),
            _exercises_tsv(raw.get('recent_exercises') or []),
            _plan_compact(plan),
            '## COLUMN_LEGEND',
            'lots: vest_id SpecID; held=sellable stock; unex=unexercised options; '
            'basis=cost basis/sh; strike=ISO strike; lt=1 long-term; ur_gain=unrealized $ on held',
            'ISO QD needs grant+2y AND exercise+1y. CA taxes CG as ordinary. ENGINE_RESULT overrides $ if present.',
        ]
        text = '\n'.join(blocks)

    meta = {
        'mode': mode,
        'chars': len(text),
        'est_tokens': estimate_tokens(text),
        'lot_count': len(lots),
        'live_price': price,
        'as_of': raw.get('as_of'),
        'tier': {'full': mode == 'full'},
    }
    return {'text': text, 'meta': meta}


def format_account_context_for_prompt(ctx: Dict[str, Any], *, max_chars: int = 80000) -> str:
    """Legacy helper."""
    if not ctx or ctx.get('error'):
        return 'ACCOUNT empty'
    if 'text' in ctx and 'meta' in ctx:
        return ctx['text'][:max_chars]
    return pack_context_for_prompt(mode='full')['text'][:max_chars]


# Back-compat names used by older tests
def classify_intent(user_text: str) -> Dict[str, bool]:
    return {
        'need_lot_detail': True,
        'need_history': True,
        'need_grants': True,
    }
