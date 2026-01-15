"""
California state tax calculator with stock compensation specifics.
Framework designed to be extended for other states.
"""

from typing import Dict, Optional


# California 2026 Tax Brackets (will be updated annually)
CA_TAX_BRACKETS_2026 = {
    'single': [
        {'min': 0, 'max': 10412, 'rate': 0.01},
        {'min': 10412, 'max': 24684, 'rate': 0.02},
        {'min': 24684, 'max': 38959, 'rate': 0.04},
        {'min': 38959, 'max': 54081, 'rate': 0.06},
        {'min': 54081, 'max': 68350, 'rate': 0.08},
        {'min': 68350, 'max': 349137, 'rate': 0.093},
        {'min': 349137, 'max': 418961, 'rate': 0.103},
        {'min': 418961, 'max': 698271, 'rate': 0.113},
        {'min': 698271, 'max': None, 'rate': 0.123},  # Top bracket
    ],
    'married_joint': [
        {'min': 0, 'max': 20824, 'rate': 0.01},
        {'min': 20824, 'max': 49368, 'rate': 0.02},
        {'min': 49368, 'max': 77918, 'rate': 0.04},
        {'min': 77918, 'max': 108162, 'rate': 0.06},
        {'min': 108162, 'max': 136700, 'rate': 0.08},
        {'min': 136700, 'max': 698274, 'rate': 0.093},
        {'min': 698274, 'max': 837922, 'rate': 0.103},
        {'min': 837922, 'max': 1000000, 'rate': 0.113},
        {'min': 1000000, 'max': None, 'rate': 0.123},
    ]
}

# California Mental Health Services Tax (additional 1% on income > $1M)
CA_MENTAL_HEALTH_TAX_THRESHOLD = 1000000
CA_MENTAL_HEALTH_TAX_RATE = 0.01

# California SDI (State Disability Insurance) - on wages only
CA_SDI_RATE_2026 = 0.009  # 0.9%
CA_SDI_WAGE_LIMIT_2026 = 153164  # Max wage base


class CaliforniaStateTax:
    """
    California-specific tax calculator.
    
    California has specific rules for stock compensation:
    - ISOs: Bargain element NOT added back for AMT (unlike federal)
    - Capital gains: Taxed as ordinary income (no preferential rate)
    - SDI: Applied to stock compensation as wages
    - Mental Health Services Tax: Additional 1% on income > $1M
    """
    
    def __init__(self, filing_status: str = 'single', tax_year: int = 2026):
        """
        Initialize California tax calculator.
        
        Args:
            filing_status: 'single' or 'married_joint'
            tax_year: Tax year (defaults to 2026)
        """
        self.filing_status = filing_status
        self.tax_year = tax_year
        self.brackets = CA_TAX_BRACKETS_2026.get(filing_status, CA_TAX_BRACKETS_2026['single'])
    
    def calculate_state_income_tax(self, taxable_income: float) -> Dict:
        """
        Calculate California state income tax using progressive brackets.
        
        Args:
            taxable_income: California taxable income
        
        Returns:
            dict with tax breakdown
        """
        if taxable_income <= 0:
            return {
                'taxable_income': 0,
                'base_tax': 0,
                'mental_health_tax': 0,
                'total_tax': 0,
                'effective_rate': 0,
                'marginal_rate': self.brackets[0]['rate']
            }
        
        # Calculate base tax using progressive brackets
        total_tax = 0
        marginal_rate = 0
        
        for bracket in self.brackets:
            bracket_min = bracket['min']
            bracket_max = bracket['max']
            rate = bracket['rate']
            
            if taxable_income <= bracket_min:
                continue
            
            # Determine taxable amount in this bracket
            if bracket_max is None:
                # Top bracket
                amount_in_bracket = taxable_income - bracket_min
                marginal_rate = rate
            elif taxable_income <= bracket_max:
                # Partially in this bracket
                amount_in_bracket = taxable_income - bracket_min
                marginal_rate = rate
            else:
                # Fully in this bracket
                amount_in_bracket = bracket_max - bracket_min
            
            total_tax += amount_in_bracket * rate
        
        # Mental Health Services Tax (1% on income > $1M)
        mental_health_tax = 0
        if taxable_income > CA_MENTAL_HEALTH_TAX_THRESHOLD:
            mental_health_tax = (taxable_income - CA_MENTAL_HEALTH_TAX_THRESHOLD) * CA_MENTAL_HEALTH_TAX_RATE
        
        total_with_mh = total_tax + mental_health_tax
        effective_rate = total_with_mh / taxable_income if taxable_income > 0 else 0
        
        return {
            'taxable_income': taxable_income,
            'base_tax': total_tax,
            'mental_health_tax': mental_health_tax,
            'total_tax': total_with_mh,
            'effective_rate': effective_rate,
            'marginal_rate': marginal_rate + (CA_MENTAL_HEALTH_TAX_RATE if taxable_income > CA_MENTAL_HEALTH_TAX_THRESHOLD else 0)
        }
    
    def calculate_sdi(self, wage_income: float, ytd_wages: float = 0) -> Dict:
        """
        Calculate California State Disability Insurance (SDI).
        
        SDI applies to stock compensation as it's treated as wages.
        
        Args:
            wage_income: This period's wage income (including stock comp)
            ytd_wages: Year-to-date wages before this payment
        
        Returns:
            dict with SDI calculation
        """
        if ytd_wages >= CA_SDI_WAGE_LIMIT_2026:
            # Already over limit, no SDI owed
            return {
                'wage_income': wage_income,
                'taxable_amount': 0,
                'sdi_tax': 0,
                'sdi_rate': CA_SDI_RATE_2026,
                'ytd_wages': ytd_wages
            }
        
        # Calculate taxable amount (limited to wage base)
        taxable_amount = min(wage_income, CA_SDI_WAGE_LIMIT_2026 - ytd_wages)
        sdi_tax = taxable_amount * CA_SDI_RATE_2026
        
        return {
            'wage_income': wage_income,
            'taxable_amount': taxable_amount,
            'sdi_tax': sdi_tax,
            'sdi_rate': CA_SDI_RATE_2026,
            'ytd_wages': ytd_wages,
            'remaining_wage_base': max(0, CA_SDI_WAGE_LIMIT_2026 - ytd_wages - wage_income)
        }
    
    def calculate_vest_taxes(self, vest_value: float, ytd_income: float = 0, ytd_wages: float = 0) -> Dict:
        """
        Calculate all California taxes on a vesting event.
        
        Args:
            vest_value: Gross value of vested shares
            ytd_income: Year-to-date income (for bracket calculation)
            ytd_wages: Year-to-date wages (for SDI calculation)
        
        Returns:
            dict with complete tax breakdown
        """
        # Total income including this vest
        total_income = ytd_income + vest_value
        
        # Calculate state income tax on total, then subtract tax on YTD
        total_tax_calc = self.calculate_state_income_tax(total_income)
        ytd_tax_calc = self.calculate_state_income_tax(ytd_income)
        
        # Incremental tax on this vest
        incremental_tax = total_tax_calc['total_tax'] - ytd_tax_calc['total_tax']
        
        # SDI on this vest
        sdi_calc = self.calculate_sdi(vest_value, ytd_wages)
        
        # Total California tax withheld
        total_ca_tax = incremental_tax + sdi_calc['sdi_tax']
        
        return {
            'vest_value': vest_value,
            'state_income_tax': incremental_tax,
            'marginal_rate': total_tax_calc['marginal_rate'],
            'sdi_tax': sdi_calc['sdi_tax'],
            'total_ca_tax': total_ca_tax,
            'effective_rate': total_ca_tax / vest_value if vest_value > 0 else 0,
            'includes_mental_health_tax': vest_value > CA_MENTAL_HEALTH_TAX_THRESHOLD
        }
    
    def calculate_capital_gains_tax(self, capital_gain: float, ytd_income: float = 0) -> Dict:
        """
        Calculate California tax on capital gains.
        
        CRITICAL: California does NOT have preferential capital gains rates.
        All capital gains are taxed as ordinary income.
        
        Args:
            capital_gain: Capital gain (or loss) from stock sale
            ytd_income: Year-to-date income
        
        Returns:
            dict with capital gains tax
        """
        if capital_gain <= 0:
            return {
                'capital_gain': capital_gain,
                'state_tax': 0,
                'note': 'Loss - no California tax (but can offset other gains)'
            }
        
        # Calculate as ordinary income
        total_income = ytd_income + capital_gain
        
        total_tax_calc = self.calculate_state_income_tax(total_income)
        ytd_tax_calc = self.calculate_state_income_tax(ytd_income)
        
        incremental_tax = total_tax_calc['total_tax'] - ytd_tax_calc['total_tax']
        
        return {
            'capital_gain': capital_gain,
            'state_tax': incremental_tax,
            'marginal_rate': total_tax_calc['marginal_rate'],
            'note': 'California taxes capital gains as ordinary income (no preferential rate)'
        }
    
    def iso_bargain_element_treatment(self) -> str:
        """
        California-specific ISO treatment.
        
        Returns:
            Explanation of California's ISO rules
        """
        return """
        California ISO Treatment:
        
        ✅ GOOD NEWS: California does NOT add ISO bargain element to AMT calculation.
        This is a key difference from federal AMT.
        
        At Exercise:
        - Federal: Bargain element added to AMT income
        - California: NO adjustment, no AMT triggered by ISOs
        
        At Sale (Qualifying):
        - Federal: LTCG on full gain (FMV at sale - strike)
        - California: Ordinary income (no preferential rate)
        
        At Sale (Disqualifying):
        - Federal: Ordinary income on bargain element, STCG on additional gain
        - California: Ordinary income on total gain
        
        Bottom line: California treats ISOs more favorably at exercise,
        but less favorably at sale (due to no LTCG rate).
        """


class StateTaxFactory:
    """
    Factory for creating state-specific tax calculators.
    Designed to be extended as we add more states.
    """
    
    @staticmethod
    def get_calculator(state: str, filing_status: str = 'single', tax_year: int = 2026):
        """
        Get appropriate state tax calculator.
        
        Args:
            state: State abbreviation ('CA', 'TX', 'NY', etc.)
            filing_status: 'single' or 'married_joint'
            tax_year: Tax year
        
        Returns:
            State-specific tax calculator instance
        """
        state = state.upper() if state else 'CA'
        
        if state == 'CA':
            return CaliforniaStateTax(filing_status, tax_year)
        elif state in ['TX', 'FL', 'WA', 'NV', 'TN', 'SD', 'WY', 'AK', 'NH']:
            # No state income tax
            return NoStateTax(state)
        else:
            # Placeholder for future states
            return GenericStateTax(state, filing_status, tax_year)


class NoStateTax:
    """Placeholder for states with no income tax."""
    
    def __init__(self, state: str):
        self.state = state
    
    def calculate_state_income_tax(self, taxable_income: float) -> Dict:
        return {
            'taxable_income': taxable_income,
            'total_tax': 0,
            'effective_rate': 0,
            'marginal_rate': 0,
            'note': f'{self.state} has no state income tax'
        }
    
    def calculate_vest_taxes(self, vest_value: float, **kwargs) -> Dict:
        return {
            'vest_value': vest_value,
            'total_ca_tax': 0,
            'note': f'{self.state} has no state income tax'
        }


class GenericStateTax:
    """Placeholder for states not yet implemented."""
    
    def __init__(self, state: str, filing_status: str, tax_year: int):
        self.state = state
        self.filing_status = filing_status
        self.tax_year = tax_year
    
    def calculate_state_income_tax(self, taxable_income: float) -> Dict:
        return {
            'taxable_income': taxable_income,
            'total_tax': 0,
            'effective_rate': 0,
            'marginal_rate': 0,
            'note': f'{self.state} tax calculator not yet implemented - using manual rates only'
        }
