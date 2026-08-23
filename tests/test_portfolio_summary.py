"""Portfolio available / unavailable breakdown."""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.utils.portfolio_summary import summarize_held_portfolio


def test_unavailable_includes_future_rsu_at_fmv():
    today = date(2026, 8, 1)
    future = today + timedelta(days=60)
    lots = [{
        'vest_event_id': 1,
        'shares_available': 1000,
        'shares_unexercised': 0,
        'is_iso': False,
        'strike_price': 0,
        'cost_basis_per_share': 10,
    }]
    future_ve = SimpleNamespace(
        shares_vested=2000,
        vest_date=future,
        grant=SimpleNamespace(share_type='rsu', share_price_at_grant=0),
    )

    with patch('app.utils.portfolio_summary.build_lots_for_user', return_value=lots), \
         patch('app.utils.portfolio_summary.get_latest_user_price', return_value=100.0), \
         patch('app.utils.portfolio_summary.Grant') as GrantMock, \
         patch('app.utils.portfolio_summary.StockSale') as SaleMock, \
         patch('app.utils.portfolio_summary.VestEvent') as VEMock:
        GrantMock.query.filter_by.return_value.all.return_value = []
        SaleMock.query.filter_by.return_value.all.return_value = []
        VEMock.query.join.return_value.filter.return_value.all.return_value = [future_ve]

        s = summarize_held_portfolio(1, live_price=100.0, lots=lots, as_of=today)

    assert s['available_stock_value'] == 100_000  # 1000 * 100
    assert s['unavailable_value'] == 200_000  # 2000 * 100
    assert s['available_value'] == 100_000
    assert s['total_value'] == 300_000
