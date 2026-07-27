"""
Account snapshots for Grok — optimized for **accuracy with low tokens**.

Design principles:
1. One compact block (no triple-copy of profile + lots + JSON dump).
2. Dense lines, not pretty-printed JSON.
3. Tier by question intent: CORE always; LOT_DETAIL only when SpecID/sell/tax
   questions need it; history only when asked.
4. Aggregate lots with the same tax character; list only top SpecID rows by value.
5. Plan payloads trimmed to decision fields only.

Rough token estimate: chars/4 (English-ish); logged in context_meta for tuning.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from flask_login import current_user


# --- Intent → detail level -------------------------------------------------

_DETAIL_PAT = re.compile(
    r'\b(sell|sale|lot|vest|spec|which|share|iso|rsu|net|cash|tax|amt|'
    r'exercise|hold|basis|ltcg|stcg|qualify|disqualif|optimize|planner|'
    r'cover|mhst|california|ca\b|bracket|proceed)',
    re.I,
)
_HISTORY_PAT = re.compile(
    r'\b(sold|sale history|past sale|last sale|exercised|exercise history|'
    r'ledger|what did i|previously)\b',
    re.I,
)
_GRANT_PAT = re.compile(
    r'\b(grant|vest schedule|cliff|new.?hire|performance)\b',
    re.I,
)


def classify_intent(user_text: str) -> Dict[str, bool]:
    t = user_text or ''
    return {
        'need_lot_detail': bool(_DETAIL_PAT.search(t)) or not t.strip(),
        'need_history': bool(_HISTORY_PAT.search(t)),
        'need_grants': bool(_GRANT_PAT.search(t)),
    }


def _n(x, d=2) -> str:
    try:
        v = float(x)
        if abs(v - round(v)) < 1e-6:
            return str(int(round(v)))
        return f'{v:.{d}f}'.rstrip('0').rstrip('.')
    except (TypeError, ValueError):
        return '0'


def _money(x) -> str:
    try:
        return f'{float(x):.0f}'
    except (TypeError, ValueError):
        return '0'


def estimate_tokens(text: str) -> int:
    """Cheap upper-bound estimate (~4 chars/token for mixed text)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


# --- Data load -------------------------------------------------------------

def build_account_context(user_id: Optional[int] = None, *, max_lots: int = 80) -> Dict[str, Any]:
    """Full structured dict (API/debug). Prefer pack_context_for_prompt for Grok."""
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

    total_held = sum(float(l.get('shares_available') or 0) for l in lots)
    total_unex = sum(float(l.get('shares_unexercised') or 0) for l in lots)

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
        'lots': lots,
        'recent_sales': sales,
        'recent_exercises': exercises,
        'capabilities': {
            'goal_optimizer': True,
            'state_tax_ca': (eng.get('state_code') or '').upper() == 'CA',
            'has_xai_key': bool(user and user.has_xai_api_key()) if user else False,
        },
    }


# --- Compact packing -------------------------------------------------------

def _profile_line(eng: dict) -> str:
    return (
        f"tax: file={eng.get('filing_status') or '?'} st={eng.get('state_code') or '-'} "
        f"yr={eng.get('tax_year') or '?'} wages={_money(eng.get('other_ordinary_income'))} "
        f"ytdW={_money(eng.get('ytd_wages'))} "
        f"fedAMTcr={_money(eng.get('amt_credit_carryforward'))} "
        f"caAMTcr={_money(eng.get('ca_amt_credit_carryforward'))} "
        f"niit={'Y' if eng.get('include_niit', True) else 'N'} "
        f"stateEng={'Y' if eng.get('use_state_engine', True) else 'N'}"
    )


def _aggregate_lots(lots: Sequence[dict], price: float) -> List[str]:
    """One line per tax bucket — very cheap for overview."""
    buckets: Dict[Tuple, Dict[str, float]] = defaultdict(
        lambda: {'sh': 0.0, 'unex': 0.0, 'gain': 0.0, 'n': 0}
    )
    for lot in lots:
        key = (
            'ISO' if lot.get('is_iso') else (lot.get('share_type') or 'rsu').upper()[:6],
            'held' if float(lot.get('shares_available') or 0) > 0 else 'unex',
            'LT' if lot.get('is_long_term') else 'ST',
            'ex' if lot.get('exercise_date') else 'noex',
        )
        b = buckets[key]
        b['sh'] += float(lot.get('shares_available') or 0)
        b['unex'] += float(lot.get('shares_unexercised') or 0)
        b['gain'] += float(lot.get('unrealized_gain') or 0)
        b['n'] += 1
    lines = []
    for (stype, held, term, ex), b in sorted(buckets.items(), key=lambda x: -(x[1]['sh'] + x[1]['unex'])):
        lines.append(
            f"agg {stype}/{held}/{term}/{ex}: lots={int(b['n'])} "
            f"held={_n(b['sh'])} unex={_n(b['unex'])} urGain~${_money(b['gain'])}"
        )
    return lines


def _top_lot_lines(lots: Sequence[dict], price: float, *, limit: int = 18) -> List[str]:
    """
    SpecID rows ranked by economic weight (held value, then unexercised value).
    Format: id|type|held|unex|basis|strike|term|vest|exDate|urGain
    """
    ranked = []
    for lot in lots:
        held = float(lot.get('shares_available') or 0)
        unex = float(lot.get('shares_unexercised') or 0)
        weight = held * price + unex * max(0.0, price - float(lot.get('strike_price') or 0))
        if held <= 0 and unex <= 0:
            continue
        ranked.append((weight, lot))
    ranked.sort(key=lambda x: -x[0])

    lines = []
    for _, lot in ranked[:limit]:
        st = 'ISO' if lot.get('is_iso') else (lot.get('share_type') or 'rsu')
        lines.append(
            f"v{lot.get('vest_event_id')}|{st}|"
            f"h={_n(lot.get('shares_available'))}|"
            f"u={_n(lot.get('shares_unexercised'))}|"
            f"b={_n(lot.get('cost_basis_per_share'))}|"
            f"k={_n(lot.get('strike_price') or 0)}|"
            f"{'LT' if lot.get('is_long_term') else 'ST'}|"
            f"vest={str(lot.get('vest_date') or '')[:10]}|"
            f"ex={str(lot.get('exercise_date') or '-')[:10]}|"
            f"g=${_money(lot.get('unrealized_gain'))}"
        )
    if len(ranked) > limit:
        lines.append(f"... +{len(ranked) - limit} smaller lots omitted")
    return lines


def _sale_lines(sales, *, limit: int = 5) -> List[str]:
    out = []
    for s in list(sales)[:limit]:
        out.append(
            f"sale {s.sale_date}|v{s.vest_event_id}|sh={_n(s.shares_sold)}|"
            f"px={_n(s.sale_price)}|gain=${_money(s.capital_gain)}|"
            f"{'LT' if s.is_long_term else 'ST'}|"
            f"qd={s.is_qualifying_disposition}"
        )
    return out


def _exercise_lines(exercises, *, limit: int = 5) -> List[str]:
    out = []
    for e in list(exercises)[:limit]:
        out.append(
            f"exer {e.exercise_date}|v{e.vest_event_id}|sh={_n(e.shares_exercised)}|"
            f"k={_n(e.strike_price)}|fmv={_n(e.fmv_at_exercise)}|"
            f"barg=${_money(e.total_bargain_element)}|held={_n(e.shares_still_held)}"
        )
    return out


def _grant_lines(grants, *, limit: int = 8) -> List[str]:
    out = []
    for g in list(grants)[:limit]:
        out.append(
            f"grant{g.id}|{g.share_type}|{g.grant_type}|"
            f"{g.grant_date}|qty={_n(g.share_quantity)}|px0={_n(g.share_price_at_grant)}|"
            f"vestY={g.vest_years}|cliff={g.cliff_years}"
        )
    return out


def _plan_compact(plan: Optional[dict]) -> str:
    if not plan:
        return 'plan: none'
    # Prefer goal-optimizer shape
    if plan.get('picks') is not None or plan.get('achieved_net_cash') is not None:
        picks = plan.get('picks') or []
        pick_bits = []
        for p in picks[:12]:
            pick_bits.append(
                f"v{p.get('vest_event_id')}:{p.get('action')}:"
                f"{_n(p.get('shares'))}sh"
            )
        return (
            f"plan: net=${_money(plan.get('achieved_net_cash'))} "
            f"tax=${_money(plan.get('total_tax'))} "
            f"short=${_money(plan.get('shortfall'))} "
            f"ok={plan.get('success')} picks=[{','.join(pick_bits)}]"
        )
    # Scenario plan shape
    if plan.get('cash') or plan.get('strategy'):
        cash = plan.get('cash') or {}
        return (
            f"plan: strat={plan.get('strategy') or plan.get('name')} "
            f"net=${_money(cash.get('net_cash') or plan.get('total_net_cash'))} "
            f"tax=${_money(cash.get('incremental_tax') or plan.get('total_incremental_tax'))} "
            f"outlay=${_money(cash.get('exercise_cash_outlay'))}"
        )
    # Unknown — tiny JSON
    import json
    return 'plan: ' + json.dumps(plan, separators=(',', ':'), default=str)[:600]


def pack_context_for_prompt(
    user_id: Optional[int] = None,
    *,
    user_message: str = '',
    plan: Optional[dict] = None,
    max_lot_lines: int = 18,
) -> Dict[str, Any]:
    """
    Build token-efficient prompt context.

    Returns:
      text: compact string for the model
      meta: sizes / tiers for UI & debugging
    """
    raw = build_account_context(user_id)
    if raw.get('error'):
        return {'text': 'acct: error not authenticated', 'meta': {'error': raw['error']}}

    intent = classify_intent(user_message)
    eng = raw.get('tax_profile') or {}
    lots = raw.get('lots') or []
    price = float(raw.get('live_price') or 0)
    ps = raw.get('portfolio_summary') or {}

    lines: List[str] = [
        f"acct {raw.get('as_of')} px={_n(price)} "
        f"held={_n(ps.get('shares_held_sellable'))} "
        f"unexISO={_n(ps.get('shares_unexercised_iso'))} "
        f"lots={ps.get('lot_count')} grants={ps.get('grant_count')} "
        f"val~${_money(ps.get('approx_held_value'))}",
        _profile_line(eng),
    ]

    # Always: cheap aggregates (accurate buckets without SpecID noise)
    lines.append('buckets:')
    lines.extend(_aggregate_lots(lots, price) or ['  (none)'])

    # SpecID detail when needed for sell/tax advice
    if intent['need_lot_detail']:
        lines.append(
            'lots (id|type|held|unex|basis|strike|term|vest|ex|gain) rank=value:'
        )
        lines.extend(_top_lot_lines(lots, price, limit=max_lot_lines) or ['  (none)'])
    else:
        lines.append('lots: detail omitted (ask about sell/lots/tax to expand SpecID)')

    if intent['need_history']:
        sales = raw.get('recent_sales') or []
        exs = raw.get('recent_exercises') or []
        lines.append('history:')
        lines.extend(_sale_lines(sales) or ['  no sales'])
        lines.extend(_exercise_lines(exs) or ['  no exercises'])

    if intent['need_grants']:
        grants = raw.get('grants') or []
        lines.append('grants:')
        lines.extend(_grant_lines(grants) or ['  none'])

    lines.append(_plan_compact(plan))

    # Legend once (small, saves model confusion without long prose)
    lines.append(
        'key: h=held u=unex b=basis k=strike LT/ST=holding ex=exerciseDate g=unrealized$ '
        'Engine is source of exact $; this is snapshot for advice.'
    )

    text = '\n'.join(lines)
    meta = {
        'tier': {
            'lot_detail': intent['need_lot_detail'],
            'history': intent['need_history'],
            'grants': intent['need_grants'],
        },
        'chars': len(text),
        'est_tokens': estimate_tokens(text),
        'lot_count': len(lots),
        'live_price': price,
        'as_of': raw.get('as_of'),
    }
    return {'text': text, 'meta': meta}


def format_account_context_for_prompt(ctx: Dict[str, Any], *, max_chars: int = 4000) -> str:
    """
    Backward-compatible helper. Prefer pack_context_for_prompt().
    If given a full build_account_context dict, repack densely.
    """
    if not ctx or ctx.get('error'):
        return 'acct: empty'
    # If already packed
    if 'text' in ctx and 'meta' in ctx:
        return ctx['text'][:max_chars]

    # Re-derive from lots/profile without second DB hit
    eng = ctx.get('tax_profile') or {}
    lots = ctx.get('lots') or []
    price = float(ctx.get('live_price') or 0)
    ps = ctx.get('portfolio_summary') or {}
    lines = [
        f"acct {ctx.get('as_of')} px={_n(price)} held={_n(ps.get('shares_held_sellable'))} "
        f"unex={_n(ps.get('shares_unexercised_iso'))}",
        _profile_line(eng),
    ]
    lines.extend(_aggregate_lots(lots, price))
    lines.extend(_top_lot_lines(lots, price, limit=15))
    text = '\n'.join(lines)
    return text[:max_chars]
