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


def _enrich_lots(lots: Sequence[dict], live_price: float) -> List[dict]:
    """Add market-value fields models use for ranking."""
    out = []
    for lot in lots:
        row = dict(lot)
        held = float(lot.get('shares_available') or 0)
        unex = float(lot.get('shares_unexercised') or 0)
        basis = float(lot.get('cost_basis_per_share') or 0)
        strike = float(lot.get('strike_price') or 0) if lot.get('is_iso') else basis
        px = float(lot.get('current_price') or live_price or 0)
        held_mkt = held * px
        held_gain = held * (px - basis) if held else 0.0
        unex_spread = unex * max(0.0, px - strike) if unex else 0.0
        row['_held_mkt'] = held_mkt
        row['_held_gain'] = held_gain
        row['_unex_spread'] = unex_spread
        row['_rank'] = held_mkt + unex_spread
        row['_px'] = px
        out.append(row)
    out.sort(key=lambda r: -float(r.get('_rank') or 0))
    return out


def _readable_summary(raw: dict, lots: Sequence[dict], eng: dict, price: float) -> str:
    """Plain-English inventory the model can quote without parsing TSV."""
    ps = raw.get('portfolio_summary') or {}
    lines = [
        '## READABLE_SUMMARY (use this first; TSV is the full source of truth)',
        f"- As of {raw.get('as_of')}: live share price **${_n(price, 2)}**.",
        f"- Sellable shares held: **{_n(ps.get('shares_held_sellable'), 2)}** "
        f"(~${_n(ps.get('approx_held_value'), 0)} at live price).",
        f"- Unexercised ISO options: **{_n(ps.get('shares_unexercised_iso'), 2)}**.",
        f"- Tax lots: **{ps.get('lot_count')}** · Grants: **{ps.get('grant_count')}** · "
        f"Recorded sales: **{ps.get('recorded_sales')}** · Exercises: **{ps.get('recorded_exercises')}**.",
        f"- Filing **{eng.get('filing_status') or '?'}** · State **{eng.get('state_code') or '-'}** · "
        f"Tax year **{eng.get('tax_year') or '?'}** · "
        f"Other ordinary income **${_n(eng.get('other_ordinary_income'), 0)}** · "
        f"YTD wages **${_n(eng.get('ytd_wages'), 0)}**.",
    ]
    if not lots:
        lines.append(
            '- ⚠ **No tax lots loaded.** Do not invent holdings. Tell the user lots may be empty '
            'or failed to load; suggest checking Grants / Sales & Tax.'
        )
        return '\n'.join(lines)

    rsu = [l for l in lots if not l.get('is_iso')]
    iso = [l for l in lots if l.get('is_iso')]
    rsu_held = sum(float(l.get('shares_available') or 0) for l in rsu)
    iso_held = sum(float(l.get('shares_available') or 0) for l in iso)
    iso_unex = sum(float(l.get('shares_unexercised') or 0) for l in iso)
    lt_held = sum(
        float(l.get('shares_available') or 0)
        for l in lots if l.get('is_long_term') and float(l.get('shares_available') or 0) > 0
    )
    st_held = sum(
        float(l.get('shares_available') or 0)
        for l in lots if (not l.get('is_long_term')) and float(l.get('shares_available') or 0) > 0
    )
    lines.append(
        f"- Mix: RSU held **{_n(rsu_held, 2)}** · ISO stock held **{_n(iso_held, 2)}** · "
        f"ISO unexercised **{_n(iso_unex, 2)}** · among held stock LT **{_n(lt_held, 2)}** / "
        f"ST **{_n(st_held, 2)}**."
    )
    lines.append('- Largest lots by economic weight (vest_id / type / held / unex / basis / mkt$ / gain$):')
    ranked = _enrich_lots(lots, price)[:12]
    for l in ranked:
        lines.append(
            f"  · v{l.get('vest_event_id')} {l.get('share_type')} "
            f"held={_n(l.get('shares_available'), 2)} unex={_n(l.get('shares_unexercised'), 2)} "
            f"basis=${_n(l.get('cost_basis_per_share'), 2)} "
            f"{'LT' if l.get('is_long_term') else 'ST'} "
            f"mkt=${_n(l.get('_held_mkt'), 0)} gain=${_n(l.get('_held_gain'), 0)} "
            f"unexSpread=${_n(l.get('_unex_spread'), 0)} "
            f"| {str(l.get('label') or '')[:50]}"
        )
    return '\n'.join(lines)


def _lots_tsv(lots: Sequence[dict], live_price: float = 0.0) -> str:
    """
    Full SpecID table — one row per vest lot, sorted by economic weight.
    """
    enriched = _enrich_lots(lots, live_price)
    header = (
        'vest_id\tshare_type\tiso\theld\tunex\tbasis\tstrike\tpx\t'
        'held_mkt\theld_gain\tunex_spread\t'
        'lt\thold_days\tvest\tgrant\tex_date\tfmv_ex\tlabel'
    )
    rows = [header]
    for lot in enriched:
        rows.append(
            '\t'.join([
                str(lot.get('vest_event_id') or ''),
                str(lot.get('share_type') or ''),
                '1' if lot.get('is_iso') else '0',
                _n(lot.get('shares_available')),
                _n(lot.get('shares_unexercised')),
                _n(lot.get('cost_basis_per_share')),
                _n(lot.get('strike_price') if lot.get('strike_price') is not None else ''),
                _n(lot.get('_px'), 2),
                _n(lot.get('_held_mkt'), 2),
                _n(lot.get('_held_gain'), 2),
                _n(lot.get('_unex_spread'), 2),
                '1' if lot.get('is_long_term') else '0',
                _n(lot.get('holding_days'), 0),
                _d(lot.get('vest_date')),
                _d(lot.get('grant_date')),
                _d(lot.get('exercise_date')),
                _n(lot.get('fmv_at_exercise') if lot.get('fmv_at_exercise') is not None else ''),
                str(lot.get('label') or '').replace('\t', ' ')[:60],
            ])
        )
    return (
        '## LOTS_TSV (all SpecID tax lots, richest first)\n'
        'vest_id = SpecID key. held = shares you can sell now. unex = ISO not yet exercised.\n'
        'basis = cost basis/sh (RSU=FMV at vest). strike = ISO strike. px = live price.\n'
        'held_mkt/held_gain/unex_spread in $ at live price.\n'
        + '\n'.join(rows)
    )


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
        ranked = _enrich_lots(lots, price)[:max_lot_lines]
        lines = [
            _readable_summary(raw, lots, eng, price),
            _profile_block(eng),
            _lots_tsv(ranked, price),
            _plan_compact(plan),
        ]
        text = '\n'.join(lines)
    else:
        # Full feed — readable summary first, then complete tables
        blocks = [
            _readable_summary(raw, lots, eng, price),
            _profile_block(eng),
            _grants_tsv(raw.get('grants') or []),
            _lots_tsv(lots, price),
            _sales_tsv(raw.get('recent_sales') or []),
            _exercises_tsv(raw.get('recent_exercises') or []),
            _plan_compact(plan),
            '## RULES_FOR_MODEL',
            'Cite real vest_id values from LOTS_TSV only. Never invent lot IDs or share counts.',
            'If READABLE_SUMMARY says no lots, say so — do not fabricate inventory.',
            'ISO QD = 2y from grant AND 1y from exercise. CA taxes capital gains as ordinary income.',
            'If ENGINE_RESULT is present above this block, its $ and picks are authoritative.',
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
