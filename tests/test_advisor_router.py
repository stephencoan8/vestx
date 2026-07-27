"""Deterministic advisor router tests."""

import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.advisor_router import route_and_compute


def _profile():
    return {
        'filing_status': 'single',
        'tax_year': 2026,
        'state_code': 'CA',
        'use_bracket_engine': True,
        'use_state_engine': True,
        'other_ordinary_income': 200_000,
        'ytd_wages': 200_000,
        'include_niit': True,
        'include_fica': False,
        'ss_wage_base_maxed': True,
        'amt_credit_carryforward': 0,
        'ca_amt_credit_carryforward': 0,
        'other_long_term_gains': 0,
        'other_short_term_gains': 0,
    }


def _lots():
    return [
        {
            'vest_event_id': 1,
            'grant_id': 1,
            'grant_type': 'new_hire',
            'share_type': 'rsu',
            'is_iso': False,
            'vest_date': '2023-01-15',
            'grant_date': '2022-01-15',
            'shares_available': 5000,
            'shares_unexercised': 0,
            'cost_basis_per_share': 20.0,
            'strike_price': None,
            'exercise_date': None,
            'is_long_term': True,
            'unrealized_gain': 150000,
            'label': 'RSU LT',
        },
    ]


def test_net_cash_is_engine_only():
    r = route_and_compute(
        user_message='I need to net $100k after tax minimize tax',
        profile_dict=_profile(),
        inventory_lots=_lots(),
        live_price=50.0,
        sale_date=date(2026, 7, 1),
    )
    assert r.skip_grok is True
    assert r.mode == 'engine_only'
    assert r.intent == 'goal_optimize'
    assert r.deterministic_reply
    assert 'ENGINE_RESULT' in r.engine_text or r.engine_payload


def test_why_uses_grok_after_engine():
    r = route_and_compute(
        user_message='net $100k minimize tax — why this plan?',
        profile_dict=_profile(),
        inventory_lots=_lots(),
        live_price=50.0,
        sale_date=date(2026, 7, 1),
    )
    assert r.mode == 'engine_then_grok'
    assert r.skip_grok is False
    assert r.engine_payload or r.engine_text


def test_portfolio_engine_only():
    r = route_and_compute(
        user_message='How many shares do I hold and total value?',
        profile_dict=_profile(),
        inventory_lots=_lots(),
        live_price=50.0,
    )
    assert r.skip_grok is True
    assert r.intent == 'portfolio'


def test_open_uses_grok():
    r = route_and_compute(
        user_message='What are the main risks of exercising ISOs in a high-income year?',
        profile_dict=_profile(),
        inventory_lots=_lots(),
        live_price=50.0,
    )
    assert r.mode == 'grok_only'
    assert r.skip_grok is False


def test_should_i_sell_50k_engine_only():
    """User phrase that previously crashed: should I sell + 50k + minimize taxes."""
    from app.utils.advisor_router import extract_cash_target
    msg = 'what should I sell to get 50k and minimize my taxes?'
    assert extract_cash_target(msg) == 50_000
    r = route_and_compute(
        user_message=msg,
        profile_dict=_profile(),
        inventory_lots=_lots(),
        live_price=115.07,
        sale_date=date(2026, 7, 27),
    )
    assert r.mode == 'engine_only'
    assert r.skip_grok is True
    assert r.intent == 'goal_optimize'
    assert r.engine_payload
    assert r.engine_payload.get('picks') or r.engine_payload.get('actions_summary')
    assert r.deterministic_reply
    # Must be JSON-serializable for chat API
    import json
    json.dumps(r.engine_payload, default=str, allow_nan=False)


def test_update_screen_300k_liquid_engine_only():
    """User phrase: update screen + minimize taxes + 300k liquid."""
    from app.utils.advisor_router import extract_cash_target
    msg = (
        'Can you update my screen to show what i should sell '
        'to minimize taxes and get 300k liquid?'
    )
    assert extract_cash_target(msg) == 300_000
    r = route_and_compute(
        user_message=msg,
        profile_dict=_profile(),
        inventory_lots=_lots(),
        live_price=115.07,
        sale_date=date(2026, 7, 27),
    )
    assert r.mode == 'engine_only'
    assert r.skip_grok is True
    assert r.intent == 'goal_optimize'
    assert r.engine_payload
    picks = r.engine_payload.get('picks') or []
    assert len(picks) >= 1
    net = float(r.engine_payload.get('achieved_net_cash') or 0)
    assert net >= 250_000  # close to target with inventory
    import json
    json.dumps(r.engine_payload, default=str, allow_nan=False)


if __name__ == '__main__':
    test_net_cash_is_engine_only()
    test_why_uses_grok_after_engine()
    test_portfolio_engine_only()
    test_open_uses_grok()
    test_should_i_sell_50k_engine_only()
    test_update_screen_300k_liquid_engine_only()
    print('ADVISOR ROUTER TESTS PASSED')
