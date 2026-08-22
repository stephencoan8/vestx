"""Annual performance LTI / multi-year RSU schedules use 48 months, not 60."""

from datetime import date
from types import SimpleNamespace

from app.models.grant import GrantType, ShareType
from app.utils.vest_calculator import calculate_vest_schedule, rsu_active_vesting_months


def _grant(**kwargs):
    base = dict(
        grant_type=GrantType.ANNUAL_PERFORMANCE.value,
        share_type=ShareType.RSU.value,
        bonus_type='long_term',
        vest_years=5,
        cliff_years=1.5,
        share_quantity=1000,
        grant_date=date(2023, 10, 15),
        vest_frequency='semiannual',
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_lti_rsu_is_eight_semiannual_events_not_ten():
    events = calculate_vest_schedule(_grant())
    assert len(events) == 8
    assert sum(e['shares'] for e in events) == 1000
    # Equal tranches (whole-share rounded)
    assert events[0]['shares'] == 125
    assert all(e['shares'] == 125 for e in events)
    assert events[0]['is_cliff'] is True
    # 1.5y after Oct 2023 ≈ Apr 2025 → snap to May 15
    assert events[0]['vest_date'] == date(2025, 5, 15)
    assert events[-1]['vest_date'] == date(2028, 11, 15)


def test_lti_rsu_quarterly_is_sixteen_events():
    events = calculate_vest_schedule(_grant(vest_frequency='quarterly'))
    assert len(events) == 16
    assert sum(e['shares'] for e in events) == 1000
    assert events[0]['shares'] == 62 or events[0]['shares'] == 63  # rounding
    assert rsu_active_vesting_months(_grant()) == 48


def test_new_hire_uses_forty_eight_months():
    g = _grant(
        grant_type=GrantType.NEW_HIRE.value,
        bonus_type=None,
        cliff_years=1.0,
    )
    events = calculate_vest_schedule(g)
    # 48mo / 6 = 8 periods; cliff 12mo = 2 periods → 1 cliff + 6 remaining = 7 events
    assert len(events) == 7
    assert sum(e['shares'] for e in events) == 1000
    assert events[0]['shares'] == 250  # 25% cliff


def test_sti_still_single_period():
    g = _grant(bonus_type='short_term', vest_years=1, cliff_years=1.0)
    events = calculate_vest_schedule(g)
    assert len(events) == 1
    assert events[0]['shares'] == 1000
