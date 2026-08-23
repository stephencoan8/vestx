"""Portfolio available / unavailable breakdown."""

from types import SimpleNamespace

from app.utils.portfolio_summary import value_unvested_events


def test_unavailable_includes_future_rsu_at_fmv():
    future_ve = SimpleNamespace(
        shares_vested=2000,
        grant=SimpleNamespace(share_type='rsu', share_price_at_grant=0),
    )
    s = value_unvested_events([future_ve], 100.0)
    assert s['unavailable_value'] == 200_000
    assert s['unavailable_shares_rsu'] == 2000


def test_unavailable_iso_uses_intrinsic():
    ve = SimpleNamespace(
        shares_vested=1000,
        grant=SimpleNamespace(share_type='iso_5y', share_price_at_grant=40.0),
    )
    s = value_unvested_events([ve], 100.0)
    assert s['unavailable_value'] == 60_000  # (100-40)*1000
