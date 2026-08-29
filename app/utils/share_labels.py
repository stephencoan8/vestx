"""Human labels — never leak raw enums into the UI."""

from __future__ import annotations

SHARE_TYPE_LABELS = {
    'rsu': 'RSU',
    'iso_5y': 'ISO (5 year)',
    'iso_6y': 'ISO (6 year)',
    'cash': 'Cash',
    'espp': 'ESPP',
}

GRANT_TYPE_LABELS = {
    'new_hire': 'New hire',
    'annual_performance': 'Annual performance',
    'promotion': 'Promotion',
    'kickass': 'Special',
    'espp': 'ESPP',
    'nqespp': 'ESPP',
}

ACTION_LABELS = {
    'sell_rsu': 'Sell shares',
    'sell_iso_held': 'Sell ISO stock',
    'iso_cashless_dd': 'ISO cashless',
    'iso_exercise_hold': 'Exercise & hold',
    'iso_exercise_sell_qd': 'Exercise, hold, sell QD',
    'iso_sell_to_cover': 'Sell to cover',
    'rsu_fund_iso': 'Sell RSU to fund ISO',
}


def share_kind_label(grant_type: str = '', share_type: str = '') -> str:
    gt = (grant_type or '').lower()
    if gt in ('espp', 'nqespp'):
        return 'ESPP'
    return SHARE_TYPE_LABELS.get((share_type or '').lower(), share_type or '—')


def grant_type_label(grant_type: str = '') -> str:
    raw = grant_type or ''
    return GRANT_TYPE_LABELS.get(raw.lower(), raw.replace('_', ' ') or '—')


def action_label(action: str = '') -> str:
    return ACTION_LABELS.get(action or '', (action or '').replace('_', ' '))


def is_espp_grant(grant_type: str = '', share_type: str = '') -> bool:
    gt = (grant_type or '').lower()
    return gt in ('espp', 'nqespp') or (share_type or '').lower() == 'espp'
