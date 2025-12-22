#!/usr/bin/env python
"""
Test script to verify ISO vesting schedules are calculated correctly.
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
    """Test ISO 5Y vesting: 1.5 year cliff, then monthly for remaining 3.5 years."""
    print("\n" + "="*80)
    print("TEST 1: ISO 5Y (2x multiplier)")
    print("="*80)
    print("Expected: 1.5 year cliff with 0.5 years (6 months) worth of shares,")
    print("          then monthly vesting for remaining time")
    
    # Example: 1000 ISOs granted on Jan 1, 2024
    grant = Grant(
        grant_date=date(2024, 1, 1),
        grant_type=GrantType.NEW_HIRE.value,
        share_type=ShareType.ISO_5Y.value,
        share_quantity=1000,  # User inputs 1000, NOT 2000
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
    expected_first_shares = 1000 * (6/60)  # 0.5 years out of 5 years
    print(f"\nFirst vest verification:")
    print(f"  Expected: {expected_first_shares:.2f} shares (6 months / 60 months)")
    print(f"  Actual: {first_vest['shares']:.2f} shares")
    print(f"  {'✅ PASS' if abs(first_vest['shares'] - expected_first_shares) < 0.01 else '❌ FAIL'}")


def test_iso_6y_vesting():
    """Test ISO 6Y vesting: 2.5 year cliff, then monthly for remaining 3.5 years."""
    print("\n" + "="*80)
    print("TEST 2: ISO 6Y (3x multiplier)")
    print("="*80)
    print("Expected: 2.5 year cliff with 0.5 years (6 months) worth of shares,")
    print("          then monthly vesting for remaining time")
    
    # Example: 1000 ISOs granted on Jan 1, 2024
    grant = Grant(
        grant_date=date(2024, 1, 1),
        grant_type=GrantType.NEW_HIRE.value,
        share_type=ShareType.ISO_6Y.value,
        share_quantity=1000,  # User inputs 1000, NOT 3000
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
    expected_first_shares = 1000 * (6/72)  # 0.5 years out of 6 years
    print(f"\nFirst vest verification:")
    print(f"  Expected: {expected_first_shares:.2f} shares (6 months / 72 months)")
    print(f"  Actual: {first_vest['shares']:.2f} shares")
    print(f"  {'✅ PASS' if abs(first_vest['shares'] - expected_first_shares) < 0.01 else '❌ FAIL'}")


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
    print("Testing new vesting logic without auto-multiplication")
    
    test_iso_5y_vesting()
    test_iso_6y_vesting()
    test_rsu_vesting()
    
    print("\n" + "="*80)
    print("✅ Testing complete!")
    print("="*80)
