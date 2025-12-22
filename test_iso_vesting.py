#!/usr/bin/env python
"""
Test script to verify ISO vesting schedules are calculated correctly.
Based on user example: Grant 1/1/23, 100 ISO 6Y, cliff 7/1/25, last vest 12/1/29
"""

from datetime import date
from app.models.grant import Grant, GrantType, ShareType
from app.utils.vest_calculator import calculate_vest_schedule

def print_vest_schedule(grant_info, vest_events):
    """Print vest schedule in a readable format."""
    print(f"\n{'='*80}")
    print(f"Grant: {grant_info['type']} - {grant_info['share_type']}")
    print(f"Grant Date: {grant_info['grant_date']}")
    print(f"Shares: {grant_info['shares']:,.0f}")
    print(f"Vest Period: {grant_info['vest_years']} years, Cliff: {grant_info['cliff_years']} years")
    print(f"{'='*80}")
    print(f"{'Vest Date':<15} {'Shares':<15} {'Cumulative':<15} {'Type':<10}")
    print(f"{'-'*80}")
    
    cumulative = 0
    for event in vest_events:
        cumulative += event['shares']
        event_type = "CLIFF" if event['is_cliff'] else "Regular"
        print(f"{str(event['vest_date']):<15} {event['shares']:>14,.2f} {cumulative:>14,.2f} {event_type:<10}")
    
    print(f"{'-'*80}")
    print(f"Total: {cumulative:,.2f} shares")
    
    # Verify totals match
    if abs(cumulative - grant_info['shares']) < 0.01:
        print("✅ Total matches grant shares")
    else:
        print(f"❌ ERROR: Total ({cumulative:,.2f}) doesn't match grant shares ({grant_info['shares']:,.2f})")


def test_iso_5y_vesting():
    """Test ISO 5Y vesting based on corrected rules."""
    print("\n" + "="*80)
    print("TEST 1: ISO 5Y (2x multiplier)")
    print("="*80)
    print("Expected: Grant 1/1/23, vesting starts 1/1/24")
    print("          Cliff at 7/1/24 (1.5 years) with 6 months worth = 10 shares")
    print("          Monthly vesting until 12/1/29 (60 months total)")
    print("          Total: 1 cliff + 54 monthly = 55 vest events")
    
    grant = Grant(
        grant_date=date(2023, 1, 1),
        grant_type=GrantType.NEW_HIRE.value,
        share_type=ShareType.ISO_5Y.value,
        share_quantity=100,
        vest_years=5,
        cliff_years=1.5,
        share_price_at_grant=100.0
    )
    
    vest_schedule = calculate_vest_schedule(grant)
    
    grant_info = {
        'type': grant.grant_type,
        'share_type': grant.share_type,
        'grant_date': grant.grant_date,
        'shares': grant.share_quantity,
        'vest_years': grant.vest_years,
        'cliff_years': grant.cliff_years
    }
    
    print_vest_schedule(grant_info, vest_schedule)
    
    # Verify first vest
    first_vest = vest_schedule[0]
    expected_first_shares = 100 * (6/60)  # 6 months out of 60 months = 10 shares
    expected_cliff_date = date(2024, 7, 1)
    print(f"\nFirst vest verification:")
    print(f"  Expected date: {expected_cliff_date}")
    print(f"  Actual date: {first_vest['vest_date']}")
    print(f"  {'✅ PASS' if first_vest['vest_date'] == expected_cliff_date else '❌ FAIL'}")
    print(f"  Expected shares: {expected_first_shares:.2f} shares (6/60 = 10%)")
    print(f"  Actual shares: {first_vest['shares']:.2f} shares")
    print(f"  {'✅ PASS' if abs(first_vest['shares'] - expected_first_shares) < 0.01 else '❌ FAIL'}")
    
    # Verify last vest
    last_vest = vest_schedule[-1]
    expected_last_date = date(2029, 12, 1)
    print(f"\nLast vest verification:")
    print(f"  Expected date: {expected_last_date}")
    print(f"  Actual date: {last_vest['vest_date']}")
    print(f"  {'✅ PASS' if last_vest['vest_date'] == expected_last_date else '❌ FAIL'}")
    
    # Verify total number of events
    print(f"\nTotal events verification:")
    print(f"  Expected: 55 events (1 cliff + 54 monthly)")
    print(f"  Actual: {len(vest_schedule)} events")
    print(f"  {'✅ PASS' if len(vest_schedule) == 55 else '❌ FAIL'}")


def test_iso_6y_vesting():
    """Test ISO 6Y vesting based on corrected rules."""
    print("\n" + "="*80)
    print("TEST 2: ISO 6Y (3x multiplier)")
    print("="*80)
    print("Expected: Grant 1/1/23, vesting starts 1/1/25")
    print("          Cliff at 7/1/25 (2.5 years) with 6 months worth = 10 shares")
    print("          Monthly vesting until 12/1/29 (60 months total from vesting start)")
    print("          Total: 1 cliff + 54 monthly = 55 vest events")
    
    grant = Grant(
        grant_date=date(2023, 1, 1),
        grant_type=GrantType.NEW_HIRE.value,
        share_type=ShareType.ISO_6Y.value,
        share_quantity=100,
        vest_years=6,
        cliff_years=2.5,
        share_price_at_grant=100.0
    )
    
    vest_schedule = calculate_vest_schedule(grant)
    
    grant_info = {
        'type': grant.grant_type,
        'share_type': grant.share_type,
        'grant_date': grant.grant_date,
        'shares': grant.share_quantity,
        'vest_years': grant.vest_years,
        'cliff_years': grant.cliff_years
    }
    
    print_vest_schedule(grant_info, vest_schedule)
    
    # Verify first vest
    first_vest = vest_schedule[0]
    expected_first_shares = 100 * (6/60)  # 6 months out of 60 months = 10 shares
    expected_cliff_date = date(2025, 7, 1)
    print(f"\nFirst vest verification:")
    print(f"  Expected date: {expected_cliff_date}")
    print(f"  Actual date: {first_vest['vest_date']}")
    print(f"  {'✅ PASS' if first_vest['vest_date'] == expected_cliff_date else '❌ FAIL'}")
    print(f"  Expected shares: {expected_first_shares:.2f} shares (6/60 = 10%)")
    print(f"  Actual shares: {first_vest['shares']:.2f} shares")
    print(f"  {'✅ PASS' if abs(first_vest['shares'] - expected_first_shares) < 0.01 else '❌ FAIL'}")
    
    # Verify last vest
    last_vest = vest_schedule[-1]
    expected_last_date = date(2029, 12, 1)  # You said last vest is 12/1/29
    print(f"\nLast vest verification:")
    print(f"  Expected date: {expected_last_date}")
    print(f"  Actual date: {last_vest['vest_date']}")
    print(f"  {'✅ PASS' if last_vest['vest_date'] == expected_last_date else '❌ FAIL'}")
    
    # Verify total number of events
    print(f"\nTotal events verification:")
    print(f"  Expected: 55 events (1 cliff + 54 monthly)")
    print(f"  Actual: {len(vest_schedule)} events")
    print(f"  {'✅ PASS' if len(vest_schedule) == 55 else '❌ FAIL'}")


def test_rsu_vesting():
    """Test RSU vesting still works (semi-annual)."""
    print("\n" + "="*80)
    print("TEST 3: RSU (Semi-annual vesting)")
    print("="*80)
    print("Expected: 1 year cliff, then semi-annual for 5 years total")
    
    grant = Grant(
        grant_date=date(2024, 1, 1),
        grant_type=GrantType.NEW_HIRE.value,
        share_type=ShareType.RSU.value,
        share_quantity=1000,
        vest_years=5,
        cliff_years=1.0,
        share_price_at_grant=100.0
    )
    
    vest_schedule = calculate_vest_schedule(grant)
    
    grant_info = {
        'type': grant.grant_type,
        'share_type': grant.share_type,
        'grant_date': grant.grant_date,
        'shares': grant.share_quantity,
        'vest_years': grant.vest_years,
        'cliff_years': grant.cliff_years
    }
    
    print_vest_schedule(grant_info, vest_schedule)


if __name__ == '__main__':
    print("\n🧪 ISO Vesting Schedule Test Suite")
    print("Testing TRUE monthly vesting (12 events per year)")
    print("Based on: Grant 1/1/23, vesting over 72 months, cliff at 6 months")
    
    test_iso_5y_vesting()
    test_iso_6y_vesting()
    test_rsu_vesting()
    
    print("\n" + "="*80)
    print("✅ Testing complete!")
    print("="*80)
