"""
Build a rich, privacy-scoped snapshot of the logged-in user's equity account
for Grok advisor prompts (server-side only — never dump secrets).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from flask_login import current_user


def build_account_context(user_id: Optional[int] = None, *, max_lots: int = 80) -> Dict[str, Any]:
    """Structured context dict for the advisor."""
    from app.models.tax_profile import TaxProfile
    from app.models.grant import Grant
    from app.models.stock_sale import StockSale, ISOExercise
    from app.utils.lot_inventory import build_lots_for_user
    from app.utils.price_utils import get_latest_user_price

    uid = user_id or (current_user.id if current_user and current_user.is_authenticated else None)
    if not uid:
        return {'error': 'not authenticated'}

    user = current_user if (current_user.is_authenticated and current_user.id == uid) else None
    profile = TaxProfile.for_user(user) if user else TaxProfile.query.filter_by(user_id=uid).first()
    eng = profile.to_engine_dict() if profile else {}

    live = get_latest_user_price(uid) or 0.0
    lots = build_lots_for_user(uid)
    grants = Grant.query.filter_by(user_id=uid).order_by(Grant.grant_date.desc()).all()
    sales = (
        StockSale.query.filter_by(user_id=uid)
        .order_by(StockSale.sale_date.desc())
        .limit(25)
        .all()
    )
    exercises = (
        ISOExercise.query.filter_by(user_id=uid)
        .order_by(ISOExercise.exercise_date.desc())
        .limit(25)
        .all()
    )

    grant_rows = []
    for g in grants[:40]:
        grant_rows.append({
            'id': g.id,
            'type': g.grant_type,
            'share_type': g.share_type,
            'grant_date': g.grant_date.isoformat() if g.grant_date else None,
            'quantity': g.share_quantity,
            'price_at_grant': g.share_price_at_grant,
            'vest_years': g.vest_years,
            'cliff_years': g.cliff_years,
        })

    lot_rows = []
    total_held = 0.0
    total_unex = 0.0
    for lot in lots[:max_lots]:
        held = float(lot.get('shares_available') or 0)
        unex = float(lot.get('shares_unexercised') or 0)
        total_held += held
        total_unex += unex
        lot_rows.append({
            'vest_event_id': lot.get('vest_event_id'),
            'label': lot.get('label'),
            'share_type': lot.get('share_type'),
            'is_iso': lot.get('is_iso'),
            'shares_available': held,
            'shares_unexercised': unex,
            'cost_basis': lot.get('cost_basis_per_share'),
            'strike': lot.get('strike_price'),
            'vest_date': lot.get('vest_date'),
            'grant_date': lot.get('grant_date'),
            'exercise_date': lot.get('exercise_date'),
            'fmv_at_exercise': lot.get('fmv_at_exercise'),
            'is_long_term': lot.get('is_long_term'),
            'holding_days': lot.get('holding_days'),
            'unrealized_gain': lot.get('unrealized_gain'),
        })

    sale_rows = [{
        'id': s.id,
        'date': s.sale_date.isoformat() if s.sale_date else None,
        'vest_event_id': s.vest_event_id,
        'shares': s.shares_sold,
        'price': s.sale_price,
        'proceeds': s.total_proceeds,
        'gain': s.capital_gain,
        'long_term': s.is_long_term,
        'iso_qd': s.is_qualifying_disposition,
    } for s in sales]

    ex_rows = [{
        'id': e.id,
        'date': e.exercise_date.isoformat() if e.exercise_date else None,
        'vest_event_id': e.vest_event_id,
        'shares': e.shares_exercised,
        'strike': e.strike_price,
        'fmv': e.fmv_at_exercise,
        'bargain_total': e.total_bargain_element,
        'still_held': e.shares_still_held,
    } for e in exercises]

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
        'grants': grant_rows,
        'lots': lot_rows,
        'recent_sales': sale_rows,
        'recent_exercises': ex_rows,
        'capabilities': {
            'goal_optimizer': True,
            'state_tax_ca': (eng.get('state_code') or '').upper() == 'CA',
            'has_xai_key': bool(user and user.has_xai_api_key()) if user else False,
        },
    }


def format_account_context_for_prompt(ctx: Dict[str, Any], *, max_chars: int = 14000) -> str:
    """Compact text block for system/user prompt injection."""
    import json
    text = json.dumps(ctx, indent=2, default=str)
    if len(text) > max_chars:
        # Drop grant detail first, keep lots/summary
        slim = dict(ctx)
        slim['grants'] = slim.get('grants', [])[:10]
        slim['lots'] = slim.get('lots', [])[:40]
        slim['recent_sales'] = slim.get('recent_sales', [])[:10]
        slim['recent_exercises'] = slim.get('recent_exercises', [])[:10]
        text = json.dumps(slim, indent=2, default=str)
        if len(text) > max_chars:
            text = text[:max_chars] + '\n…[truncated]'
    return text
