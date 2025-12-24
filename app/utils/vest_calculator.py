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
    Calculate the next vest date (either 6/15 or 11/15).
    
    Args:
        grant_date: The date the grant was issued
        
    Returns:
        The next vest date
    """
    year = grant_date.year
    
    # Check distances to both dates
    june_15 = date(year, 6, 15)
    nov_15 = date(year, 11, 15)
    
    if grant_date < june_15:
        return june_15
    elif grant_date < nov_15:
        return nov_15
    else:
        return date(year + 1, 6, 15)


def get_closest_vest_date(target_date: date) -> date:
    """
    Find the SpaceX vest date (6/15 or 11/15) closest to the target date.
    
    Args:
        target_date: The date to find the closest vest date to
        
    Returns:
        The closest vest date (6/15 or 11/15)
    """
    year = target_date.year
    
    # Check the two vest dates in the same year
    june_15 = date(year, 6, 15)
    nov_15 = date(year, 11, 15)
    
    # Also check previous and next year dates
    prev_nov_15 = date(year - 1, 11, 15)
    next_june_15 = date(year + 1, 6, 15)
    
    # Calculate distances
    candidates = [
        (abs((target_date - prev_nov_15).days), prev_nov_15),
        (abs((target_date - june_15).days), june_15),
        (abs((target_date - nov_15).days), nov_15),
        (abs((target_date - next_june_15).days), next_june_15),
    ]
    
    # Return the date with minimum distance
    return min(candidates, key=lambda x: x[0])[1]


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
    # For RSUs: Use standard SpaceX vest dates (6/15 or 11/15)
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
        
        # Set to 15th of the month (SpaceX ISOs vest on the 15th)
        vesting_start = date(vesting_start.year, vesting_start.month, 15)
        
        # Cliff is 6 months after vesting starts
        cliff_date = vesting_start + relativedelta(months=6)
    else:
        # RSU/RSA: Use standard SpaceX vest dates (6/15 or 11/15)
        # Calculate the actual cliff date, then find the closest vest date
        cliff_months = int(grant.cliff_years * 12)
        
        # Calculate actual cliff date (grant_date + cliff period)
        actual_cliff_date = grant.grant_date + relativedelta(months=cliff_months)
        
        # Find the closest SpaceX vest date to the cliff anniversary
        cliff_date = get_closest_vest_date(actual_cliff_date)
    
    # Determine vesting frequency
    if grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
        # Monthly vesting for ISOs
        vest_frequency_months = 1
    else:
        # Semi-annual vesting for RSUs/RSAs
        vest_frequency_months = 6
    
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
    
    elif vest_frequency_months == 6:
        # Semi-annual vesting
        total_vests = total_months // 6
        shares_per_vest = grant.share_quantity / total_vests
        
        # Special handling for 5-year RSU annual bonus (long_term)
        # Vests like ISO 5Y but biannually instead of monthly
        if (grant.grant_type == GrantType.ANNUAL_PERFORMANCE.value and 
            grant.bonus_type == 'long_term' and 
            grant.share_type == ShareType.RSU.value and
            grant.vest_years == 5):
            # First vest includes first 6 months worth (1 biannual period)
            # Total is 10 biannual vests over 60 months
            cliff_shares = shares_per_vest  # 1/10 of total
            
            vest_events.append({
                'vest_date': cliff_date,
                'shares': cliff_shares,
                'is_cliff': True
            })
            
            # Add remaining 9 biannual vests
            current_date = cliff_date
            remaining_shares = grant.share_quantity - cliff_shares
            remaining_vests = total_vests - 1  # Already vested the first period
            
            if remaining_vests > 0:
                shares_per_remaining_vest = remaining_shares / remaining_vests
                
                for _ in range(remaining_vests):
                    # Move to next vest date
                    if current_date.month == 6:
                        current_date = date(current_date.year, 11, 15)
                    else:
                        current_date = date(current_date.year + 1, 6, 15)
                    
                    vest_events.append({
                        'vest_date': current_date,
                        'shares': shares_per_remaining_vest,
                        'is_cliff': False
                    })
        else:
            # Standard RSU vesting (new hire, promotion, short-term bonus, etc.)
            cliff_months = int(grant.cliff_years * 12)
            cliff_periods = cliff_months // 6
            cliff_shares = shares_per_vest * cliff_periods
            
            # Special case for 6-year ISO with 2.5 year cliff
            if grant.share_type == ShareType.ISO_6Y.value and grant.cliff_years == 2.5:
                cliff_shares = grant.share_quantity * (0.5 / grant.vest_years)
            
            # Add cliff event
            vest_events.append({
                'vest_date': cliff_date,
                'shares': cliff_shares,
                'is_cliff': True
            })
            
            # Add remaining vests
            current_date = cliff_date
            remaining_shares = grant.share_quantity - cliff_shares
            remaining_vests = total_vests - cliff_periods
            
            if remaining_vests > 0:
                shares_per_remaining_vest = remaining_shares / remaining_vests
                
                for _ in range(remaining_vests):
                    # Move to next vest date
                    if current_date.month == 6:
                        current_date = date(current_date.year, 11, 15)
                    else:
                        current_date = date(current_date.year + 1, 6, 15)
                    
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
            return (1, 1.0)
        elif bonus_type == 'long_term':
            if share_type == ShareType.RSU.value:
                return (5, 1.5)  # 5 years vesting, 1.5 year cliff (like ISO 5Y but biannual)
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
