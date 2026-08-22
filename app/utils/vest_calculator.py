"""
Vesting schedule calculator for SpaceX stock grants.
"""

from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from typing import List, Dict, Tuple
from app.models.grant import Grant, GrantType, ShareType

import math


def round_vest_schedule(vest_events, total_shares):
    """Round vest events to whole shares while ensuring total matches grant amount."""
    if not vest_events:
        return vest_events
    target_total = round(total_shares)
    fractional_parts = []
    rounded_shares = []
    for i, vest in enumerate(vest_events):
        original_shares = vest['shares']
        rounded = math.floor(original_shares)
        fractional = original_shares - rounded
        rounded_shares.append(rounded)
        fractional_parts.append((i, fractional))
    current_total = sum(rounded_shares)
    shares_to_distribute = target_total - current_total
    fractional_parts.sort(key=lambda x: x[1], reverse=True)
    for i in range(int(shares_to_distribute)):
        if i < len(fractional_parts):
            vest_index = fractional_parts[i][0]
            rounded_shares[vest_index] += 1
    rounded_events = []
    for i, vest in enumerate(vest_events):
        rounded_event = vest.copy()
        rounded_event['shares'] = float(rounded_shares[i])
        rounded_events.append(rounded_event)
    return rounded_events




def get_next_vest_date(grant_date: date) -> date:
    """
    Calculate the next vest date (either 5/15 or 11/15).
    
    Args:
        grant_date: The date the grant was issued
        
    Returns:
        The next vest date
    """
    year = grant_date.year
    
    # Check distances to both dates
    may_15 = date(year, 5, 15)
    nov_15 = date(year, 11, 15)
    
    if grant_date < may_15:
        return may_15
    elif grant_date < nov_15:
        return nov_15
    else:
        return date(year + 1, 5, 15)


def _vest_months_for_frequency(frequency: str) -> List[int]:
    """Calendar months used for SpaceX-style vest dates."""
    freq = (frequency or 'semiannual').strip().lower()
    if freq in ('quarterly', 'quarter', 'q'):
        return [2, 5, 8, 11]  # mid-quarter-ish; extends May/Nov pattern
    return [5, 11]  # classic biannual


def normalize_vest_frequency(frequency: str = None) -> str:
    freq = (frequency or 'semiannual').strip().lower()
    if freq in ('quarterly', 'quarter', 'q'):
        return 'quarterly'
    return 'semiannual'


def get_vest_frequency_months(frequency: str = None) -> int:
    return 3 if normalize_vest_frequency(frequency) == 'quarterly' else 6


def get_closest_vest_date(target_date: date, frequency: str = None) -> date:
    """
    Find the SpaceX-style vest date closest to the target date.

    Semi-annual: 5/15 or 11/15.
    Quarterly: 2/15, 5/15, 8/15, or 11/15.
    """
    months = _vest_months_for_frequency(frequency)
    year = target_date.year
    candidates = []
    for y in (year - 1, year, year + 1):
        for m in months:
            candidates.append(date(y, m, 15))
    return min(candidates, key=lambda d: abs((target_date - d).days))


def advance_vest_date(current: date, frequency: str = None) -> date:
    """Next vest date after ``current`` on the selected cadence."""
    months = _vest_months_for_frequency(frequency)
    # Find next month in cycle strictly after current
    for y in (current.year, current.year + 1, current.year + 2):
        for m in months:
            d = date(y, m, 15)
            if d > current:
                return d
    # Fallback (should not hit)
    return current + relativedelta(months=get_vest_frequency_months(frequency))


def rsu_active_vesting_months(grant) -> int:
    """
    Months over which RSU/RSA shares actually deliver.

    SpaceX multi-year grants are labeled vest_years=5 (lag/cliff structure
    included) but LTI / new-hire delivery is 4 years = 48 months — same active
    window as ISO_5Y. Shorter grants use vest_years × 12.
    """
    vy = int(getattr(grant, 'vest_years', None) or 1)
    if vy >= 5:
        return 48
    return max(get_vest_frequency_months(getattr(grant, 'vest_frequency', None)), vy * 12)


def get_next_espp_date(grant_date: date) -> date:
    """
    Calculate the next ESPP date (either 5/15 or 10/15).
    
    Args:
        grant_date: The date the grant was issued
        
    Returns:
        The next ESPP payment date
    """
    year = grant_date.year
    
    may_15 = date(year, 5, 15)
    oct_15 = date(year, 10, 15)
    
    if grant_date < may_15:
        return may_15
    elif grant_date < oct_15:
        return oct_15
    else:
        return date(year + 1, 5, 15)


def calculate_vest_schedule(grant: Grant) -> List[Dict]:
    """
    Calculate the complete vesting schedule for a grant.
    
    Args:
        grant: The Grant object
        
    Returns:
        List of vest events with dates and share quantities
    """
    vest_events = []
    
    # Handle ESPP separately (immediate vest on grant date)
    # For ESPP, the grant_date is the actual receipt/vest date
    if grant.grant_type in [GrantType.ESPP.value, GrantType.NQESPP.value]:
        vest_events.append({
            'vest_date': grant.grant_date,  # ESPP vests immediately on grant date
            'shares': grant.share_quantity,
            'is_cliff': False
        })
        return vest_events
    
    # Calculate cliff date
    # For ISOs: cliff is when the FIRST vest happens (after vesting starts + 6 months)
    # For RSUs: Use standard SpaceX vest dates (5/15 or 11/15)
    if grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
        # ISO cliff calculation:
        # - Determine when vesting period starts
        # - Cliff is 6 months after vesting start
        if grant.share_type == ShareType.ISO_5Y.value:
            # Vesting starts 1 year after grant, cliff at 1.5 years (1 year + 6 months)
            vesting_start = grant.grant_date + relativedelta(years=1)
        else:  # ISO_6Y
            # Vesting starts 2 years after grant, cliff at 2.5 years (2 years + 6 months)
            vesting_start = grant.grant_date + relativedelta(years=2)
        
        # Calculate theoretical cliff date (vesting start + 6 months)
        theoretical_cliff = vesting_start + relativedelta(months=6)
        
        # Snap to closest SpaceX vest date (5/15 or 11/15)
        cliff_date = get_closest_vest_date(theoretical_cliff)
    else:
        # RSU/RSA: Use standard SpaceX vest dates for the grant's cadence
        cliff_months = int(grant.cliff_years * 12)
        actual_cliff_date = grant.grant_date + relativedelta(months=cliff_months)
        cliff_date = get_closest_vest_date(
            actual_cliff_date,
            getattr(grant, 'vest_frequency', None),
        )
    
    # Determine vesting frequency
    rsu_frequency = normalize_vest_frequency(getattr(grant, 'vest_frequency', None))
    if grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
        # Monthly vesting for ISOs
        vest_frequency_months = 1
    else:
        # Semi-annual (default) or quarterly for RSUs/RSAs
        vest_frequency_months = get_vest_frequency_months(rsu_frequency)
    
    # Calculate total vesting periods
    total_months = int(grant.vest_years * 12)
    
    if vest_frequency_months == 1:
        # Monthly vesting (for ISOs) - TRUE monthly vesting, 12 events per year
        # ISO vesting rules:
        # - ISO 5Y: Vests over 4 years (48 months) starting 1 year after grant
        #   - Grant 1/1/23 → Vesting 1/15/24 to 12/15/27 = 48 months
        # - ISO 6Y: Vests over 4 years (48 months) starting 2 years after grant  
        #   - Grant 1/1/23 → Vesting 1/15/25 to 12/15/28 = 48 months
        # - Cliff at 6 months into vesting period (6/48 of total shares)
        # - First vest at cliff includes 6 months worth (6/48)
        # - Then monthly vesting on the 15th of each month for remaining 42 months (1/48 each)
        
        # Both ISO types vest over 4 years (48 months)
        VESTING_MONTHS = 48
        
        shares_per_month = grant.share_quantity / VESTING_MONTHS
        
        # First vest at cliff includes 6 months worth
        cliff_shares = shares_per_month * 6
        
        # Add cliff event
        vest_events.append({
            'vest_date': cliff_date,
            'shares': cliff_shares,
            'is_cliff': True
        })
        
        # Add monthly vests - remaining months after cliff (months 7 to VESTING_MONTHS)
        remaining_months = VESTING_MONTHS - 6
        current_date = cliff_date
        
        for _ in range(remaining_months):
            # Move to next month
            current_date = current_date + relativedelta(months=1)
            
            vest_events.append({
                'vest_date': current_date,
                'shares': shares_per_month,
                'is_cliff': False
            })
    
    elif vest_frequency_months in (3, 6):
        # RSU/RSA: semi-annual (default) or quarterly.
        # Active delivery window is 48 months for vest_years >= 5 (not 60).
        # LTI example: 48 mo / 6 = 8 events (was wrongly 10 over 60 mo).
        active_months = rsu_active_vesting_months(grant)
        total_vests = max(1, active_months // vest_frequency_months)
        shares_per_vest = grant.share_quantity / total_vests

        # Annual-performance LTI RSU: 1y lag then 4y delivery (like ISO_5Y),
        # first cliff vest = one period only (not cliff_years worth of catch-up).
        if (grant.grant_type == GrantType.ANNUAL_PERFORMANCE.value and
            grant.bonus_type == 'long_term' and
            grant.share_type == ShareType.RSU.value and
            int(grant.vest_years or 0) >= 5):
            cliff_shares = shares_per_vest  # 1/8 semi-annual, or 1/16 quarterly

            vest_events.append({
                'vest_date': cliff_date,
                'shares': cliff_shares,
                'is_cliff': True
            })

            current_date = cliff_date
            remaining_shares = grant.share_quantity - cliff_shares
            remaining_vests = total_vests - 1

            if remaining_vests > 0:
                shares_per_remaining_vest = remaining_shares / remaining_vests
                for _ in range(remaining_vests):
                    current_date = advance_vest_date(current_date, rsu_frequency)
                    vest_events.append({
                        'vest_date': current_date,
                        'shares': shares_per_remaining_vest,
                        'is_cliff': False
                    })
        else:
            # Standard RSU (new hire, promotion, short-term, kickass, etc.)
            cliff_months = int(float(grant.cliff_years or 0) * 12)
            cliff_periods = max(1, cliff_months // vest_frequency_months) if cliff_months else 1
            # Don't credit more periods than the schedule has
            cliff_periods = min(cliff_periods, total_vests)
            cliff_shares = shares_per_vest * cliff_periods

            vest_events.append({
                'vest_date': cliff_date,
                'shares': cliff_shares,
                'is_cliff': True
            })

            current_date = cliff_date
            remaining_shares = grant.share_quantity - cliff_shares
            remaining_vests = total_vests - cliff_periods

            if remaining_vests > 0 and remaining_shares > 0:
                shares_per_remaining_vest = remaining_shares / remaining_vests
                for _ in range(remaining_vests):
                    current_date = advance_vest_date(current_date, rsu_frequency)
                    vest_events.append({
                        'vest_date': current_date,
                        'shares': shares_per_remaining_vest,
                        'is_cliff': False
                    })

    vest_events = round_vest_schedule(vest_events, grant.share_quantity)
    return vest_events


def get_grant_configuration(grant_type: str, share_type: str, bonus_type: str = None) -> Tuple[int, float]:
    """
    Get the vesting configuration for a grant.
    
    Args:
        grant_type: Type of grant
        share_type: Type of share
        bonus_type: Type of bonus (for annual performance grants)
        
    Returns:
        Tuple of (vest_years, cliff_years)
    """
    if grant_type == GrantType.NEW_HIRE.value or grant_type == GrantType.PROMOTION.value:
        return (5, 1.0)
    
    elif grant_type == GrantType.ANNUAL_PERFORMANCE.value:
        if bonus_type == 'short_term':
            return (1, 1.0)  # STI: ~20% tranche, paid in first period
        elif bonus_type == 'long_term':
            if share_type == ShareType.RSU.value:
                # LTI: labeled 5y (1y lag + 4y/48mo delivery), cliff at 1.5y
                return (5, 1.5)
            elif share_type == ShareType.ISO_5Y.value:
                return (5, 1.5)
            elif share_type == ShareType.ISO_6Y.value:
                return (6, 2.5)
        return (1, 1.0)
    
    elif grant_type == GrantType.KICKASS.value:
        # Can be 1-5 years, default to 1
        return (1, 1.0)
    
    elif grant_type in [GrantType.ESPP.value, GrantType.NQESPP.value]:
        return (0, 0)
    
    # Default
    return (1, 1.0)
