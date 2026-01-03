"""
Comprehensive tax calculator for RSU/ISO vesting.
Handles Federal, State, FICA (Social Security + Medicare), and Additional Medicare tax.
"""

# 2026 Tax Constants (updated annually)
SOCIAL_SECURITY_RATE = 0.062  # 6.2%
SOCIAL_SECURITY_WAGE_BASE = 176100  # 2026 wage base limit (increased from $168,600 in 2025)

MEDICARE_RATE = 0.0145  # 1.45%
ADDITIONAL_MEDICARE_RATE = 0.009  # 0.9% on income over threshold
ADDITIONAL_MEDICARE_THRESHOLD_SINGLE = 200000
ADDITIONAL_MEDICARE_THRESHOLD_MARRIED = 250000


class TaxCalculator:
    """Calculate comprehensive paycheck taxes for stock compensation vesting."""
    
    def __init__(self, annual_income: float, filing_status: str = 'single', state: str = None):
        """
        Initialize tax calculator.
        
        Args:
            annual_income: Total annual income (including salary)
            filing_status: 'single' or 'married_joint'
            state: State abbreviation (e.g., 'CA')
        """
        self.annual_income = annual_income
        self.filing_status = filing_status
        self.state = state
        self.ytd_wages = 0  # Year-to-date wages for SS calculation
        self.effective_rates = None  # Effective rates for all taxes (for past years with known total income)
        
    def set_ytd_wages(self, ytd_wages: float):
        """Set year-to-date wages for accurate Social Security calculation."""
        self.ytd_wages = ytd_wages
    
    def set_effective_rates(self, effective_federal: float, effective_state: float, 
                           effective_medicare: float, effective_ss: float):
        """
        Set effective tax rates for all taxes (use for past years with known total income).
        
        Args:
            effective_federal: Effective federal tax rate (total fed tax / total income)
            effective_state: Effective state tax rate (total state tax / total income)
            effective_medicare: Effective medicare rate (includes base + additional)
            effective_ss: Effective social security rate (accounts for wage base cap)
        """
        self.effective_rates = {
            'federal': effective_federal,
            'state': effective_state,
            'medicare': effective_medicare,
            'social_security': effective_ss
        }
        
    def calculate_vest_taxes(self, vest_value: float, federal_rate: float, state_rate: float) -> dict:
        """
        Calculate all taxes on a vesting event.
        
        Args:
            vest_value: Gross value of vested shares
            federal_rate: Federal marginal tax rate (as decimal, e.g., 0.24) - ignored if effective_rates set
            state_rate: State marginal tax rate (as decimal, e.g., 0.093) - ignored if effective_rates set
            
        Returns:
            dict with detailed tax breakdown
        """
        # Use effective rates if set (for past years), otherwise use marginal rates
        if self.effective_rates:
            # Past year with known total income - use effective rates
            federal_tax = vest_value * self.effective_rates['federal']
            state_tax = vest_value * self.effective_rates['state']
            medicare_tax = vest_value * self.effective_rates['medicare']
            social_security_tax = vest_value * self.effective_rates['social_security']
            
            # For display purposes
            displayed_federal_rate = self.effective_rates['federal']
            displayed_state_rate = self.effective_rates['state']
            displayed_medicare_rate = self.effective_rates['medicare']
            displayed_ss_rate = self.effective_rates['social_security']
            additional_medicare_tax = 0.0  # Already included in medicare rate
            displayed_additional_medicare_rate = 0.0
        else:
            # Current/future year or no effective rates - use marginal rates with progressive calculation
            federal_tax = vest_value * federal_rate
            state_tax = vest_value * state_rate if state_rate else 0.0
            
            # Social Security Tax (6.2% up to wage base)
            social_security_tax = self._calculate_social_security(vest_value)
            displayed_ss_rate = (social_security_tax / vest_value) if vest_value > 0 else 0.0
            
            # Medicare Tax (1.45% on all wages)
            medicare_tax = vest_value * MEDICARE_RATE
            displayed_medicare_rate = MEDICARE_RATE
            
            # Additional Medicare Tax (0.9% over threshold)
            additional_medicare_tax = self._calculate_additional_medicare(vest_value)
            displayed_additional_medicare_rate = ADDITIONAL_MEDICARE_RATE if additional_medicare_tax > 0 else 0.0
            
            displayed_federal_rate = federal_rate
            displayed_state_rate = state_rate
        
        # Total FICA (Social Security + Medicare + Additional Medicare)
        total_fica = social_security_tax + medicare_tax + additional_medicare_tax
        
        # Total Tax Withheld
        total_tax = federal_tax + state_tax + total_fica
        
        # Net Amount (after all taxes)
        net_amount = vest_value - total_tax
        
        # Effective Tax Rate
        effective_rate = (total_tax / vest_value) if vest_value > 0 else 0.0
        
        return {
            'gross_value': vest_value,
            'federal_tax': federal_tax,
            'federal_rate': displayed_federal_rate,
            'state_tax': state_tax,
            'state_rate': displayed_state_rate,
            'social_security_tax': social_security_tax,
            'social_security_rate': displayed_ss_rate,
            'medicare_tax': medicare_tax,
            'medicare_rate': displayed_medicare_rate,
            'additional_medicare_tax': additional_medicare_tax,
            'additional_medicare_rate': displayed_additional_medicare_rate,
            'total_fica': total_fica,
            'total_tax': total_tax,
            'net_amount': net_amount,
            'effective_rate': effective_rate
        }
    
    def _calculate_social_security(self, vest_value: float) -> float:
        """Calculate Social Security tax considering wage base limit."""
        # If already over the wage base, no SS tax
        if self.ytd_wages >= SOCIAL_SECURITY_WAGE_BASE:
            return 0.0
        
        # Calculate how much of the vest is subject to SS tax
        taxable_amount = min(
            vest_value,
            SOCIAL_SECURITY_WAGE_BASE - self.ytd_wages
        )
        
        return max(0.0, taxable_amount * SOCIAL_SECURITY_RATE)
    
    def _calculate_additional_medicare(self, vest_value: float) -> float:
        """Calculate Additional Medicare Tax (0.9% over threshold)."""
        # Determine threshold based on filing status
        threshold = (ADDITIONAL_MEDICARE_THRESHOLD_MARRIED 
                    if self.filing_status == 'married_joint' 
                    else ADDITIONAL_MEDICARE_THRESHOLD_SINGLE)
        
        # Total income including this vest
        total_income = self.annual_income + vest_value
        
        # If total doesn't exceed threshold, no additional Medicare tax
        if total_income <= threshold:
            return 0.0
        
        # Calculate portion of vest that's over the threshold
        if self.annual_income >= threshold:
            # Already over threshold, entire vest is taxed
            taxable_amount = vest_value
        else:
            # Partially over threshold
            taxable_amount = total_income - threshold
        
        return taxable_amount * ADDITIONAL_MEDICARE_RATE


def get_all_us_states():
    """Return list of all US states with income tax."""
    return [
        ('AL', 'Alabama'), ('AK', 'Alaska'), ('AZ', 'Arizona'), ('AR', 'Arkansas'),
        ('CA', 'California'), ('CO', 'Colorado'), ('CT', 'Connecticut'), ('DE', 'Delaware'),
        ('FL', 'Florida'), ('GA', 'Georgia'), ('HI', 'Hawaii'), ('ID', 'Idaho'),
        ('IL', 'Illinois'), ('IN', 'Indiana'), ('IA', 'Iowa'), ('KS', 'Kansas'),
        ('KY', 'Kentucky'), ('LA', 'Louisiana'), ('ME', 'Maine'), ('MD', 'Maryland'),
        ('MA', 'Massachusetts'), ('MI', 'Michigan'), ('MN', 'Minnesota'), ('MS', 'Mississippi'),
        ('MO', 'Missouri'), ('MT', 'Montana'), ('NE', 'Nebraska'), ('NV', 'Nevada'),
        ('NH', 'New Hampshire'), ('NJ', 'New Jersey'), ('NM', 'New Mexico'), ('NY', 'New York'),
        ('NC', 'North Carolina'), ('ND', 'North Dakota'), ('OH', 'Ohio'), ('OK', 'Oklahoma'),
        ('OR', 'Oregon'), ('PA', 'Pennsylvania'), ('RI', 'Rhode Island'), ('SC', 'South Carolina'),
        ('SD', 'South Dakota'), ('TN', 'Tennessee'), ('TX', 'Texas'), ('UT', 'Utah'),
        ('VT', 'Vermont'), ('VA', 'Virginia'), ('WA', 'Washington'), ('WV', 'West Virginia'),
        ('WI', 'Wisconsin'), ('WY', 'Wyoming'), ('DC', 'District of Columbia')
    ]


# States with no income tax
NO_INCOME_TAX_STATES = ['AK', 'FL', 'NV', 'NH', 'SD', 'TN', 'TX', 'WA', 'WY']
