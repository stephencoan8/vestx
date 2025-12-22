"""
Vesting schedule calculator for SpaceX stock grants.
"""

from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from typing import List, Dict, Tuple
from app.models.grant import Grant, GrantType, ShareType


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
    
    # Handle ESPP separately (immediate)
    if grant.grant_type in [GrantType.ESPP.value, GrantType.NQESPP.value]:
        vest_date = get_next_espp_date(grant.grant_date)
        vest_events.append({
            'vest_date': vest_date,
            'shares': grant.share_quantity,
            'is_cliff': False
        })
        return vest_events
    
    # Calculate cliff date
    first_vest_date = get_next_vest_date(grant.grant_date)
    cliff_months = int(grant.cliff_years * 12)
    
    # For ISOs, cliff_years is 1.5 or 2.5, so we need to handle the fractional part
    # ISO 5Y: 1.5 years = 18 months cliff
    # ISO 6Y: 2.5 years = 30 months cliff
    
    # Add cliff months to first vest date
    cliff_date = first_vest_date
    months_to_add = cliff_months
    while months_to_add >= 6:
        if cliff_date.month == 6:
            cliff_date = date(cliff_date.year, 11, 15)
        else:
            cliff_date = date(cliff_date.year + 1, 6, 15)
        months_to_add -= 6
    
    # Determine vesting frequency
    if grant.share_type == ShareType.RSU.value:
        # Semi-annual vesting
        vest_frequency_months = 6
    elif grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
        # Monthly vesting
        vest_frequency_months = 1
    else:
        # Default to semi-annual
        vest_frequency_months = 6
    
    # Calculate total vesting periods
    total_months = int(grant.vest_years * 12)
    
    if vest_frequency_months == 6:
        # Semi-annual vesting
        total_vests = total_months // 6
        shares_per_vest = grant.share_quantity / total_vests
        
        # Calculate cliff shares
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
    
    else:
        # Monthly vesting (for ISOs)
        total_vests = total_months
        shares_per_month = grant.share_quantity / total_months
        
        # For ISOs, the first vest includes 6 months worth of shares (0.5 years)
        # ISO 5Y: cliff at 18 months, first vest = 6 months worth
        # ISO 6Y: cliff at 30 months, first vest = 6 months worth
        cliff_shares = shares_per_month * 6  # Always 0.5 years worth
        
        # Add cliff event
        vest_events.append({
            'vest_date': cliff_date,
            'shares': cliff_shares,
            'is_cliff': True
        })
        
        # Add monthly vests (but align to vest dates)
        # Start counting from 6 months (since cliff already vested 6 months worth)
        current_date = cliff_date
        months_vested = 6  # We've vested 6 months worth at cliff
        
        while months_vested < total_months:
            # Move to next vest date (6/15 or 11/15)
            if current_date.month == 6:
                current_date = date(current_date.year, 11, 15)
                months_elapsed = 5
            else:
                current_date = date(current_date.year + 1, 6, 15)
                months_elapsed = 7
            
            # Calculate shares for this period
            months_to_vest = min(months_elapsed, total_months - months_vested)
            shares_this_period = shares_per_month * months_to_vest
            
            if shares_this_period > 0:
                vest_events.append({
                    'vest_date': current_date,
                    'shares': shares_this_period,
                    'is_cliff': False
                })
            
            months_vested += months_to_vest
    
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
                return (1, 1.5)  # 1 year + 0.5 year stacked
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
