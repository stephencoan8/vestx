"""Token-efficient context packing tests."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.account_context import (
    classify_intent,
    estimate_tokens,
    _aggregate_lots,
    _top_lot_lines,
    _plan_compact,
)


def test_intent_lot_detail():
    assert classify_intent('how do I net 500k')['need_lot_detail'] is True
    assert classify_intent('hello')['need_lot_detail'] is False
    assert classify_intent('what did I sell last year')['need_history'] is True


def test_aggregate_cheaper_than_raw():
    lots = []
    for i in range(30):
        lots.append({
            'vest_event_id': i,
            'share_type': 'rsu',
            'is_iso': False,
            'shares_available': 100 + i,
            'shares_unexercised': 0,
            'cost_basis_per_share': 20,
            'strike_price': 0,
            'is_long_term': True,
            'exercise_date': None,
            'unrealized_gain': 1000,
            'vest_date': '2023-01-01',
        })
    agg = '\n'.join(_aggregate_lots(lots, 50))
    top = '\n'.join(_top_lot_lines(lots, 50, limit=18))
    assert estimate_tokens(agg) < 200
    assert estimate_tokens(top) < 800
    # Aggregates should be far smaller than 30 full JSON objects
    assert len(agg) < 500


def test_plan_compact_goal_shape():
    plan = {
        'success': True,
        'achieved_net_cash': 500000,
        'total_tax': 120000,
        'shortfall': 0,
        'picks': [
            {'vest_event_id': 1, 'action': 'sell_rsu', 'shares': 1000},
            {'vest_event_id': 2, 'action': 'sell_rsu', 'shares': 500},
        ],
    }
    s = _plan_compact(plan)
    assert '500000' in s or '500000' in s.replace(',', '')
    assert 'v1' in s
    assert len(s) < 300


if __name__ == '__main__':
    test_intent_lot_detail()
    test_aggregate_cheaper_than_raw()
    test_plan_compact_goal_shape()
    print('ACCOUNT CONTEXT PACK TESTS PASSED')
