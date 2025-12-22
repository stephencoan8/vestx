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
        
        # Set to 1st of the month
        vesting_start = date(vesting_start.year, vesting_start.month, 1)
        
        # Cliff is 6 months after vesting starts
        cliff_date = vesting_start + relativedelta(months=6)
    else:
        # RSU/RSA: Use standard SpaceX vest dates (6/15 or 11/15)
        first_vest_date = get_next_vest_date(grant.grant_date)
        cliff_months = int(grant.cliff_years * 12)
        
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
        # - Both ISO 5Y and 6Y vest over 5 years (60 months) from vesting start
        # - ISO 5Y: Vesting starts 1 year after grant (1/1/24 for grant 1/1/23)
        # - ISO 6Y: Vesting starts 2 years after grant (1/1/25 for grant 1/1/23)
        # - Cliff at 6 months into vesting period
        # - First vest at cliff includes 6 months worth (6/60 of total)
        # - Then monthly vesting on the 1st of each month for remaining 54 months (1/60 each)
        # Example: Grant 1/1/23, ISO 6Y, vesting 1/1/25-12/1/29 = 60 months
        
        VESTING_MONTHS = 60  # Both ISO 5Y and 6Y vest over 5 years
        shares_per_month = grant.share_quantity / VESTING_MONTHS
        
        # First vest at cliff includes 6 months worth (6/60 of total)
        cliff_shares = shares_per_month * 6
        
        # Add cliff event
        vest_events.append({
            'vest_date': cliff_date,
            'shares': cliff_shares,
            'is_cliff': True
        })
        
        # Add monthly vests - 54 more months (months 7-60)
        # Cliff vested months 1-6, we need to vest months 7-60 = 54 more vests
        current_date = cliff_date
        
        for _ in range(54):  # 54 more vests after cliff (months 7-60)
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
