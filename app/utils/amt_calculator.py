"""
Alternative Minimum Tax (AMT) Calculator
Industry-standard AMT calculation for ISO exercises.
"""

from datetime import date


# 2026 AMT Constants (updated annually)
AMT_EXEMPTION_SINGLE_2026 = 88100
AMT_EXEMPTION_MARRIED_2026 = 137000
AMT_PHASEOUT_THRESHOLD_SINGLE_2026 = 609350
AMT_PHASEOUT_THRESHOLD_MARRIED_2026 = 1218700
AMT_PHASEOUT_RATE = 0.25  # Exemption phases out at 25 cents per dollar over threshold

AMT_RATE_LOWER = 0.26  # 26% on AMTI up to threshold
AMT_RATE_UPPER = 0.28  # 28% on AMTI over threshold
AMT_RATE_THRESHOLD_2026 = 232600  # Same for single and married


class AMTCalculator:
    """
    Calculate Alternative Minimum Tax (AMT) for ISO exercises.
    
    AMT ensures high-income taxpayers pay minimum tax by adding back certain deductions
    and applying different rates. ISO bargain element is a key AMT "preference item."
    """
    
    def __init__(self, tax_year: int = None, filing_status: str = 'single'):
        """
        Initialize AMT calculator.
        
        Args:
            tax_year: Tax year (defaults to current year)
            filing_status: 'single' or 'married_joint'
        """
        self.tax_year = tax_year or date.today().year
        self.filing_status = filing_status
        
        # Set year-specific constants (2026 values for now, will expand)
        if self.tax_year >= 2026:
            self.exemption = (AMT_EXEMPTION_MARRIED_2026 if filing_status == 'married_joint' 
                            else AMT_EXEMPTION_SINGLE_2026)
            self.phaseout_threshold = (AMT_PHASEOUT_THRESHOLD_MARRIED_2026 if filing_status == 'married_joint'
                                      else AMT_PHASEOUT_THRESHOLD_SINGLE_2026)
        else:
            # Use 2026 values for prior years (will update with actual historical data)
            self.exemption = (AMT_EXEMPTION_MARRIED_2026 if filing_status == 'married_joint' 
                            else AMT_EXEMPTION_SINGLE_2026)
            self.phaseout_threshold = (AMT_PHASEOUT_THRESHOLD_MARRIED_2026 if filing_status == 'married_joint'
                                      else AMT_PHASEOUT_THRESHOLD_SINGLE_2026)
    
    def calculate_amt(self, regular_taxable_income: float, iso_bargain_element: float,
                     other_adjustments: float = 0.0) -> dict:
        """
        Calculate AMT liability and compare to regular tax.
        
        Args:
            regular_taxable_income: Taxable income from regular tax calculation
            iso_bargain_element: Total ISO bargain element (FMV - strike) from exercises
            other_adjustments: Other AMT adjustments (medical, state tax, etc.)
        
        Returns:
            dict with complete AMT calculation breakdown
        """
        # Step 1: Calculate Alternative Minimum Taxable Income (AMTI)
        amti = regular_taxable_income + iso_bargain_element + other_adjustments
        
        # Step 2: Calculate AMT exemption (with phaseout)
        exemption = self._calculate_exemption(amti)
        
        # Step 3: Calculate AMT base (AMTI - exemption)
        amt_base = max(0, amti - exemption)
        
        # Step 4: Calculate tentative minimum tax
        tentative_amt = self._calculate_tentative_amt(amt_base)
        
        # Step 5: AMT liability is excess of tentative AMT over regular tax
        # Note: We don't have regular tax here, caller needs to compare
        
        return {
            'amti': amti,
            'iso_adjustment': iso_bargain_element,
            'other_adjustments': other_adjustments,
            'exemption_before_phaseout': self.exemption,
            'phaseout_amount': self.exemption - exemption,
            'exemption_allowed': exemption,
            'amt_base': amt_base,
            'tentative_amt': tentative_amt,
            'amt_rate_lower': AMT_RATE_LOWER,
            'amt_rate_upper': AMT_RATE_UPPER,
        }
    
    def calculate_amt_liability(self, regular_tax: float, regular_taxable_income: float,
                               iso_bargain_element: float, other_adjustments: float = 0.0) -> dict:
        """
        Calculate actual AMT owed (difference between AMT and regular tax).
        
        Args:
            regular_tax: Total regular tax liability
            regular_taxable_income: Taxable income from regular calculation
            iso_bargain_element: ISO bargain element for the year
            other_adjustments: Other AMT adjustments
        
        Returns:
            dict with AMT calculation and liability
        """
        amt_calc = self.calculate_amt(regular_taxable_income, iso_bargain_element, other_adjustments)
        
        tentative_amt = amt_calc['tentative_amt']
        amt_owed = max(0, tentative_amt - regular_tax)
        
        # AMT credit (can carry forward to future years)
        # Only deferral items (like ISO) create credits, not exclusion items
        amt_credit_generated = amt_owed  # Simplified - in reality only ISO portion creates credit
        
        return {
            **amt_calc,
            'regular_tax': regular_tax,
            'tentative_amt': tentative_amt,
            'amt_owed': amt_owed,
            'total_tax': regular_tax + amt_owed,
            'amt_credit_generated': amt_credit_generated,
            'effective_rate_with_amt': (regular_tax + amt_owed) / regular_taxable_income if regular_taxable_income > 0 else 0
        }
    
    def _calculate_exemption(self, amti: float) -> float:
        """Calculate AMT exemption with phaseout."""
        if amti <= self.phaseout_threshold:
            # Full exemption
            return self.exemption
        
        # Calculate phaseout
        excess = amti - self.phaseout_threshold
        phaseout = excess * AMT_PHASEOUT_RATE
        
        # Exemption can't go below zero
        return max(0, self.exemption - phaseout)
    
    def _calculate_tentative_amt(self, amt_base: float) -> float:
        """Calculate tentative AMT using two-tier rate structure."""
        if amt_base <= 0:
            return 0
        
        if amt_base <= AMT_RATE_THRESHOLD_2026:
            # All taxed at 26%
            return amt_base * AMT_RATE_LOWER
        else:
            # First portion at 26%, rest at 28%
            lower_bracket_tax = AMT_RATE_THRESHOLD_2026 * AMT_RATE_LOWER
            upper_bracket_tax = (amt_base - AMT_RATE_THRESHOLD_2026) * AMT_RATE_UPPER
            return lower_bracket_tax + upper_bracket_tax
    
    def project_amt_for_iso_exercise(self, annual_income: float, iso_shares: float,
                                     strike_price: float, fmv_at_exercise: float,
                                     regular_tax: float) -> dict:
        """
        Project AMT impact of exercising ISOs.
        
        Args:
            annual_income: Total annual income (salary + other)
            iso_shares: Number of ISO shares to exercise
            strike_price: ISO strike/exercise price
            fmv_at_exercise: Fair market value at exercise
            regular_tax: Projected regular tax liability
        
        Returns:
            dict with AMT projection and recommendation
        """
        # Calculate bargain element
        bargain_element_per_share = fmv_at_exercise - strike_price
        total_bargain_element = iso_shares * bargain_element_per_share
        
        # Calculate AMT
        amt_result = self.calculate_amt_liability(
            regular_tax=regular_tax,
            regular_taxable_income=annual_income,
            iso_bargain_element=total_bargain_element
        )
        
        return {
            **amt_result,
            'iso_shares': iso_shares,
            'strike_price': strike_price,
            'fmv_at_exercise': fmv_at_exercise,
            'bargain_element_per_share': bargain_element_per_share,
            'total_bargain_element': total_bargain_element,
            'recommendation': self._generate_recommendation(amt_result)
        }
    
    def _generate_recommendation(self, amt_result: dict) -> str:
        """Generate user-friendly recommendation based on AMT result."""
        amt_owed = amt_result['amt_owed']
        
        if amt_owed == 0:
            return "✅ No AMT triggered. Safe to exercise."
        elif amt_owed < 5000:
            return f"⚠️ Minimal AMT (${amt_owed:,.0f}). Consider exercising - you'll recover this via AMT credit."
        elif amt_owed < 25000:
            return f"⚠️ Moderate AMT (${amt_owed:,.0f}). Review your cash position and credit recovery timeline."
        else:
            return f"🚨 Significant AMT (${amt_owed:,.0f}). Consider splitting exercise across multiple years."


def calculate_amt_credit_recovery(amt_credit_available: float, future_year_regular_tax: float,
                                  future_year_tentative_amt: float) -> float:
    """
    Calculate how much AMT credit can be used in a future year.
    
    AMT credit can only be used when regular tax > tentative AMT.
    
    Args:
        amt_credit_available: Total AMT credit carried forward
        future_year_regular_tax: Projected regular tax for future year
        future_year_tentative_amt: Projected tentative AMT for future year
    
    Returns:
        Amount of credit that can be used
    """
    if future_year_regular_tax <= future_year_tentative_amt:
        # Still in AMT, can't use credit
        return 0
    
    # Can use credit up to the difference
    max_credit_usable = future_year_regular_tax - future_year_tentative_amt
    
    # But limited to available credit
    return min(amt_credit_available, max_credit_usable)
