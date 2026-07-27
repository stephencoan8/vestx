"""Full account context packing tests."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.account_context import (
    estimate_tokens,
    _lots_tsv,
    _plan_compact,
    classify_intent,
)


def test_lots_tsv_includes_header_and_rows():
    lots = [
        {
            'vest_event_id': 1,
            'share_type': 'rsu',
            'is_iso': False,
            'shares_available': 100,
            'shares_unexercised': 0,
            'cost_basis_per_share': 20,
            'strike_price': None,
            'is_long_term': True,
            'holding_days': 400,
            'vest_date': '2023-01-01',
            'grant_date': '2022-01-01',
            'exercise_date': None,
            'fmv_at_exercise': None,
            'unrealized_gain': 3000,
            'label': 'RSU A',
        },
        {
            'vest_event_id': 2,
            'share_type': 'iso_5y',
            'is_iso': True,
            'shares_available': 0,
            'shares_unexercised': 50,
            'cost_basis_per_share': 5,
            'strike_price': 5,
            'is_long_term': False,
            'holding_days': 10,
            'vest_date': '2024-01-01',
            'grant_date': '2022-06-01',
            'exercise_date': None,
            'fmv_at_exercise': None,
            'unrealized_gain': 0,
            'label': 'ISO B',
        },
    ]
    tsv = _lots_tsv(lots)
    assert 'vest_id' in tsv
    assert 'v' not in tsv.split('\n')[1] or '1' in tsv  # row data
    assert '\t' in tsv
    assert tsv.count('\n') >= 2
    # ~2 rows should be tiny
    assert estimate_tokens(tsv) < 200


def test_hundreds_of_rows_still_reasonable():
    lots = []
    for i in range(200):
        lots.append({
            'vest_event_id': i,
            'share_type': 'rsu',
            'is_iso': False,
            'shares_available': 10 + i % 5,
            'shares_unexercised': 0,
            'cost_basis_per_share': 15 + i % 10,
            'strike_price': None,
            'is_long_term': i % 2 == 0,
            'holding_days': 100 + i,
            'vest_date': '2023-01-01',
            'grant_date': '2022-01-01',
            'exercise_date': None,
            'fmv_at_exercise': None,
            'unrealized_gain': 100 * i,
            'label': f'lot{i}',
        })
    tsv = _lots_tsv(lots)
    tok = estimate_tokens(tsv)
    # 200 dense rows should be on the order of a few k tokens, not 50k+
    assert tok < 15000
    assert '## LOTS_TSV' in tsv


def test_plan_compact():
    plan = {
        'success': True,
        'achieved_net_cash': 500000,
        'total_tax': 120000,
        'shortfall': 0,
        'total_proceeds': 700000,
        'goal': {'target_net_cash': 500000},
        'picks': [
            {'vest_event_id': 1, 'action': 'sell_rsu', 'shares': 1000, 'price': 50,
             'basis_or_strike': 20, 'is_long_term': True, 'iso_disposition': 'n/a', 'reason': 'LT'},
        ],
    }
    s = _plan_compact(plan)
    assert 'PLAN' in s
    assert '500000' in s or '500000' in s.replace(',', '')


def test_classify_intent_compat():
    assert classify_intent('x')['need_lot_detail'] is True


if __name__ == '__main__':
    test_lots_tsv_includes_header_and_rows()
    test_hundreds_of_rows_still_reasonable()
    test_plan_compact()
    test_classify_intent_compat()
    print('ACCOUNT CONTEXT PACK TESTS PASSED')
