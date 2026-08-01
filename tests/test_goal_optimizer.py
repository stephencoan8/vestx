"""Goal optimizer — net cash target + SpecID lot selection."""

import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.goal_optimizer import (
    GoalRequest,
    optimize_goal,
    parse_goal_heuristic,
    inventory_to_specs,
)


def _profile(**kw):
    base = {
        'filing_status': 'single',
        'tax_year': 2026,
        'state_code': 'CA',
        'use_bracket_engine': True,
        'use_state_engine': True,
        'federal_ordinary_rate': None,
        'federal_ltcg_rate': None,
        'state_ordinary_rate': 0.0,
        'state_cg_rate': 0.0,
        'other_ordinary_income': 200_000,
        'other_long_term_gains': 0,
        'other_short_term_gains': 0,
        'include_fica': False,
        'include_niit': True,
        'ytd_wages': 200_000,
        'ss_wage_base_maxed': True,
        'amt_credit_carryforward': 0.0,
        'ca_amt_credit_carryforward': 0.0,
    }
    base.update(kw)
    return base


def _inv():
    """RSU LT lots + unexercised ISO."""
    return [
        {
            'vest_event_id': 1,
            'grant_id': 1,
            'grant_type': 'new_hire',
            'share_type': 'rsu',
            'is_iso': False,
            'vest_date': '2023-01-15',
            'grant_date': '2022-01-15',
            'shares_available': 10000,
            'shares_unexercised': 0,
            'cost_basis_per_share': 20.0,
            'strike_price': None,
            'exercise_date': None,
            'fmv_at_exercise': None,
            'is_long_term': True,
            'label': 'RSU LT high basis',
        },
        {
            'vest_event_id': 2,
            'grant_id': 2,
            'grant_type': 'annual',
            'share_type': 'rsu',
            'is_iso': False,
            'vest_date': '2023-06-01',
            'grant_date': '2022-06-01',
            'shares_available': 8000,
            'shares_unexercised': 0,
            'cost_basis_per_share': 10.0,
            'strike_price': None,
            'exercise_date': None,
            'fmv_at_exercise': None,
            'is_long_term': True,
            'label': 'RSU LT low basis',
        },
        {
            'vest_event_id': 3,
            'grant_id': 3,
            'grant_type': 'new_hire',
            'share_type': 'iso_5y',
            'is_iso': True,
            'vest_date': '2024-01-01',
            'grant_date': '2022-01-01',
            'shares_available': 0,
            'shares_unexercised': 5000,
            'cost_basis_per_share': 5.0,
            'strike_price': 5.0,
            'exercise_date': None,
            'fmv_at_exercise': None,
            'is_long_term': False,
            'label': 'ISO unexercised',
        },
    ]


def test_parse_heuristic_500k():
    g = parse_goal_heuristic('I want to net $500k after tax with minimal taxes')
    assert g.target_net_cash == 500_000
    assert g.objective == 'min_tax'


def test_parse_heuristic_k_suffix():
    g = parse_goal_heuristic('need net 500k cash')
    assert g.target_net_cash == 500_000


def test_optimize_prefers_high_basis_ltcg():
    """High-basis RSU should be preferred over low-basis for same LT treatment."""
    goal = GoalRequest(
        target_net_cash=100_000,
        objective='min_tax',
        sale_price=50.0,
        sale_date=date(2026, 7, 1),
        exercise_date=date(2026, 7, 1),
        exercise_fmv=50.0,
        allow_rsu=True,
        allow_iso_cashless=False,
        allow_iso_sell_held=False,
    )
    r = optimize_goal(_profile(), _inv(), goal)
    assert r.success
    assert r.achieved_net_cash >= 100_000 * 0.99
    assert r.picks
    # First pick should be high-basis RSU (vest 1) — less gain per share
    first = r.picks[0]
    assert first.vest_event_id == 1
    assert first.action == 'sell_rsu'
    # Should not need ISO cashless
    assert all(p.action != 'iso_cashless_dd' for p in r.picks)


def test_optimize_uses_cashless_when_needed():
    # Tiny RSU inventory, large target → need ISO cashless
    inv = [
        {
            'vest_event_id': 1,
            'grant_id': 1,
            'grant_type': 'new_hire',
            'share_type': 'rsu',
            'is_iso': False,
            'vest_date': '2023-01-15',
            'grant_date': '2022-01-15',
            'shares_available': 100,
            'shares_unexercised': 0,
            'cost_basis_per_share': 40.0,
            'strike_price': None,
            'exercise_date': None,
            'fmv_at_exercise': None,
            'is_long_term': True,
            'label': 'small RSU',
        },
        {
            'vest_event_id': 3,
            'grant_id': 3,
            'grant_type': 'new_hire',
            'share_type': 'iso_5y',
            'is_iso': True,
            'vest_date': '2024-01-01',
            'grant_date': '2022-01-01',
            'shares_available': 0,
            'shares_unexercised': 20000,
            'cost_basis_per_share': 5.0,
            'strike_price': 5.0,
            'exercise_date': None,
            'fmv_at_exercise': None,
            'is_long_term': False,
            'label': 'ISO unexercised',
        },
    ]
    goal = GoalRequest(
        target_net_cash=200_000,
        objective='min_tax',
        sale_price=50.0,
        sale_date=date(2026, 7, 1),
        exercise_fmv=50.0,
        allow_rsu=True,
        allow_iso_cashless=True,
    )
    r = optimize_goal(_profile(), inv, goal)
    assert any(p.action == 'iso_cashless_dd' for p in r.picks)


def test_inventory_to_specs():
    specs = inventory_to_specs(_inv(), 50)
    assert len(specs) >= 2


def test_parse_exercise_all_iso_and_fund():
    g = parse_goal_heuristic(
        'exercise all my ISO (dont sell just exercise) and cover cost and taxes '
        'via selling RSU. also sell RSUs to get 300k cash. minimize losses'
    )
    assert g.target_net_cash == 300_000
    assert g.exercise_all_iso is True
    assert g.allow_iso_exercise_hold is True
    assert g.allow_iso_cashless is False
    assert g.iso_prefer_hold_fraction == 1.0


def test_optimize_exercise_all_iso_funds_via_rsu_and_pocket():
    """
    Exercise all unexercised ISO (hold) + sell min-tax RSU so pocket net ≥ target.
    Picks must include iso_exercise_hold and sell_rsu; strike outlay > 0.
    """
    inv = _inv()
    # Ensure large enough RSU pool for $50k pocket + ISO costs
    inv[0]['shares_available'] = 20_000
    inv[1]['shares_available'] = 20_000
    goal = GoalRequest(
        target_net_cash=50_000,
        objective='min_tax',
        sale_price=100.0,
        sale_date=date(2026, 7, 1),
        exercise_date=date(2026, 7, 1),
        exercise_fmv=100.0,
        allow_rsu=True,
        allow_iso_cashless=False,
        allow_iso_sell_held=False,
        exercise_all_iso=True,
        allow_iso_exercise_hold=True,
        iso_prefer_hold_fraction=1.0,
    )
    r = optimize_goal(_profile(), inv, goal)
    assert any(p.action == 'iso_exercise_hold' for p in r.picks), r.picks
    assert any(p.action == 'sell_rsu' for p in r.picks), r.picks
    assert r.total_strike_outlay > 0
    # 5000 ISO @ $5 strike = $25k outlay
    assert abs(r.total_strike_outlay - 25_000) < 1
    assert r.achieved_net_cash >= 50_000 * 0.99
    assert r.success
    # No ISO cashless when exercise-all hold
    assert all(p.action != 'iso_cashless_dd' for p in r.picks)
    iso_sh = sum(p.shares for p in r.picks if p.action == 'iso_exercise_hold')
    assert abs(iso_sh - 5000) < 1


if __name__ == '__main__':
    test_parse_heuristic_500k()
    test_parse_heuristic_k_suffix()
    test_optimize_prefers_high_basis_ltcg()
    test_optimize_uses_cashless_when_needed()
    test_inventory_to_specs()
    print('ALL GOAL OPTIMIZER TESTS PASSED')
