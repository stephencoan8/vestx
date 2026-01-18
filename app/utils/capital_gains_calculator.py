"""
Capital Gains Tax Calculator - matches professional financial software methodology.

Key principles:
1. Uses TOTAL annual income (salary + vests + gains) to determine bracket
2. Long-term capital gains: 0%, 15%, or 20% based on total income
3. Short-term capital gains: Taxed as ordinary income
4. Net Investment Income Tax (NIIT): 3.8% surtax on investment income for high earners
5. State taxes: Most states tax capital gains as ordinary income
"""

from typing import Dict, Optional
from datetime import date, timedelta


# 2026 Long-Term Capital Gains Tax Brackets (Federal)
# Based on taxable income (after standard deduction)
LTCG_BRACKETS_2026 = {
    'single': [
        {'max': 47025, 'rate': 0.00},      # 0% bracket
        {'max': 518900, 'rate': 0.15},     # 15% bracket  
        {'max': None, 'rate': 0.20}        # 20% bracket
    ],
    'married_joint': [
        {'max': 94050, 'rate': 0.00},      # 0% bracket
        {'max': 583750, 'rate': 0.15},     # 15% bracket
        {'max': None, 'rate': 0.20}        # 20% bracket
    ]
}

# Net Investment Income Tax (NIIT) - 3.8% surtax
# Applies to investment income when MAGI exceeds threshold
NIIT_RATE = 0.038
NIIT_THRESHOLD_SINGLE = 200000
NIIT_THRESHOLD_MARRIED = 250000

# Short-term capital gains holding period (1 year)
SHORT_TERM_HOLDING_PERIOD_DAYS = 365


class CapitalGainsCalculator:
    """
    Calculate capital gains taxes using professional-grade methodology.
    
    Matches how top financial software handles multi-event tax years:
    - Uses total annual income to determine bracket (not incremental)
    - Separates short-term vs long-term treatment
    - Applies NIIT for high earners
    - Coordinates with state taxes
    """
    
    def __init__(self, total_annual_income: float, filing_status: str = 'single', 
                 state: str = None, tax_year: int = None):
        """
        Initialize capital gains calculator.
        
        Args:
            total_annual_income: TOTAL annual income including salary, vests, and all gains
            filing_status: 'single' or 'married_joint'
            state: State abbreviation (e.g., 'CA')
            tax_year: Tax year (defaults to current year)
        """
        self.total_annual_income = total_annual_income
        self.filing_status = filing_status
        self.state = state
        self.tax_year = tax_year or date.today().year
        
        # Get brackets for filing status
        self.ltcg_brackets = LTCG_BRACKETS_2026.get(
            filing_status, 
            LTCG_BRACKETS_2026['single']
        )
    
    def calculate_sale_taxes(self, 
                            capital_gain: float,
                            purchase_date: date,
                            sale_date: date,
                            state_rate: float = None) -> Dict:
        """
        Calculate all taxes on a stock sale.
        
        Args:
            capital_gain: Gain/loss amount (positive for gain, negative for loss)
            purchase_date: Date shares were acquired (vest date for RSUs)
            sale_date: Date shares were sold
            state_rate: State marginal income tax rate (if not using state-specific calculator)
            
        Returns:
            dict with detailed tax breakdown
        """
        # No tax on losses (but can offset other gains - not calculated here)
        if capital_gain <= 0:
            return {
                'capital_gain': capital_gain,
                'is_long_term': self._is_long_term(purchase_date, sale_date),
                'holding_days': (sale_date - purchase_date).days,
                'federal_tax': 0,
                'federal_rate': 0,
                'niit_tax': 0,
                'niit_rate': 0,
                'state_tax': 0,
                'state_rate': 0,
                'total_tax': 0,
                'effective_rate': 0,
                'net_gain': capital_gain
            }
        
        # Determine if long-term or short-term
        is_long_term = self._is_long_term(purchase_date, sale_date)
        holding_days = (sale_date - purchase_date).days
        
        # Calculate federal tax
        if is_long_term:
            federal_rate = self._get_ltcg_rate()
            federal_tax = capital_gain * federal_rate
        else:
            # Short-term gains taxed as ordinary income
            # Use estimated marginal rate based on total income
            federal_rate = self._estimate_ordinary_income_rate()
            federal_tax = capital_gain * federal_rate
        
        # Calculate NIIT (applies to both short-term and long-term)
        niit_tax = self._calculate_niit(capital_gain)
        niit_rate = NIIT_RATE if niit_tax > 0 else 0
        
        # Calculate state tax
        # Most states tax capital gains as ordinary income (no preferential rate)
        if state_rate is not None:
            state_tax = capital_gain * state_rate
            effective_state_rate = state_rate
        elif self.state:
            # Use state-specific calculator if available
            effective_state_rate = self._get_state_rate()
            state_tax = capital_gain * effective_state_rate
        else:
            state_tax = 0
            effective_state_rate = 0
        
        # Total tax
        total_tax = federal_tax + niit_tax + state_tax
        
        # Net gain after taxes
        net_gain = capital_gain - total_tax
        
        # Effective rate on the gain
        effective_rate = (total_tax / capital_gain) if capital_gain > 0 else 0
        
        return {
            'capital_gain': capital_gain,
            'is_long_term': is_long_term,
            'holding_days': holding_days,
            'federal_tax': federal_tax,
            'federal_rate': federal_rate,
            'niit_tax': niit_tax,
            'niit_rate': niit_rate,
            'state_tax': state_tax,
            'state_rate': effective_state_rate,
            'total_tax': total_tax,
            'effective_rate': effective_rate,
            'net_gain': net_gain,
            'tax_breakdown': {
                'federal': f'{federal_rate*100:.1f}%',
                'niit': f'{niit_rate*100:.1f}%' if niit_tax > 0 else 'N/A',
                'state': f'{effective_state_rate*100:.1f}%' if state_tax > 0 else 'N/A',
                'total_rate': f'{effective_rate*100:.1f}%'
            }
        }
    
    def _is_long_term(self, purchase_date: date, sale_date: date) -> bool:
        """Determine if holding period qualifies as long-term (>1 year)."""
        holding_days = (sale_date - purchase_date).days
        return holding_days > SHORT_TERM_HOLDING_PERIOD_DAYS
    
    def _get_ltcg_rate(self) -> float:
        """
        Get long-term capital gains rate based on TOTAL annual income.
        
        This is the key: we use total income (salary + all vests + all gains)
        to determine the bracket, not incremental stacking.
        """
        for bracket in self.ltcg_brackets:
            if bracket['max'] is None or self.total_annual_income <= bracket['max']:
                return bracket['rate']
        
        # Shouldn't reach here, but return top rate as fallback
        return 0.20
    
    def _estimate_ordinary_income_rate(self) -> float:
        """
        Estimate marginal ordinary income tax rate based on total annual income.
        
        2026 Federal Tax Brackets (estimated):
        Single: 10%, 12%, 22%, 24%, 32%, 35%, 37%
        """
        # Simplified marginal rate estimation based on income level
        if self.filing_status == 'married_joint':
            if self.total_annual_income <= 23200:
                return 0.10
            elif self.total_annual_income <= 94300:
                return 0.12
            elif self.total_annual_income <= 201050:
                return 0.22
            elif self.total_annual_income <= 383900:
                return 0.24
            elif self.total_annual_income <= 487450:
                return 0.32
            elif self.total_annual_income <= 731200:
                return 0.35
            else:
                return 0.37
        else:  # single
            if self.total_annual_income <= 11600:
                return 0.10
            elif self.total_annual_income <= 47150:
                return 0.12
            elif self.total_annual_income <= 100525:
                return 0.22
            elif self.total_annual_income <= 191950:
                return 0.24
            elif self.total_annual_income <= 243725:
                return 0.32
            elif self.total_annual_income <= 609350:
                return 0.35
            else:
                return 0.37
    
    def _calculate_niit(self, investment_income: float) -> float:
        """
        Calculate Net Investment Income Tax (3.8% surtax).
        
        Applies when Modified Adjusted Gross Income (MAGI) exceeds threshold.
        For simplicity, we use total_annual_income as proxy for MAGI.
        """
        threshold = (NIIT_THRESHOLD_MARRIED 
                    if self.filing_status == 'married_joint' 
                    else NIIT_THRESHOLD_SINGLE)
        
        # If below threshold, no NIIT
        if self.total_annual_income <= threshold:
            return 0.0
        
        # NIIT applies to the LESSER of:
        # 1. Net investment income, OR
        # 2. Amount by which MAGI exceeds threshold
        
        excess_income = self.total_annual_income - threshold
        taxable_investment_income = min(investment_income, excess_income)
        
        return taxable_investment_income * NIIT_RATE
    
    def _get_state_rate(self) -> float:
        """
        Get state capital gains tax rate.
        
        Most states tax capital gains as ordinary income.
        For now, return estimated rate based on income.
        """
        if not self.state:
            return 0.0
        
        # California taxes capital gains as ordinary income
        if self.state == 'CA':
            # Simplified CA rate estimation
            if self.total_annual_income <= 68350:
                return 0.08
            elif self.total_annual_income <= 349137:
                return 0.093
            elif self.total_annual_income <= 418961:
                return 0.103
            elif self.total_annual_income <= 698271:
                return 0.113
            else:
                return 0.123  # Top bracket
        
        # For other states, return 0 for now (can be extended)
        return 0.0


def estimate_capital_gains_tax(capital_gain: float, 
                               holding_days: int,
                               total_annual_income: float,
                               filing_status: str = 'single',
                               state: str = None) -> Dict:
    """
    Quick utility function to estimate capital gains tax.
    
    Args:
        capital_gain: Gain amount
        holding_days: Days between purchase and sale
        total_annual_income: Total annual income for bracket determination
        filing_status: 'single' or 'married_joint'
        state: State abbreviation
        
    Returns:
        dict with tax breakdown
    """
    # Determine purchase/sale dates from holding period
    sale_date = date.today()
    purchase_date = sale_date - timedelta(days=holding_days)
    
    calculator = CapitalGainsCalculator(
        total_annual_income=total_annual_income,
        filing_status=filing_status,
        state=state
    )
    
    return calculator.calculate_sale_taxes(
        capital_gain=capital_gain,
        purchase_date=purchase_date,
        sale_date=sale_date
    )
