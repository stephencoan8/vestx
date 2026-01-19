"""
Vest event model for tracking individual vesting events.
"""

from app import db
from datetime import datetime, date
from app.utils.price_utils import get_latest_user_price


class VestEvent(db.Model):
    """Individual vesting event for a grant."""
    
    __tablename__ = 'vest_events'
    
    id = db.Column(db.Integer, primary_key=True)
    grant_id = db.Column(db.Integer, db.ForeignKey('grants.id'), nullable=False, index=True)
    
    # Vest details
    vest_date = db.Column(db.Date, nullable=False)
    shares_vested = db.Column(db.Float, nullable=False)
    # Note: share_price_at_vest is now a @property that calculates dynamically
    
    # Tax handling - simplified flow:
    # 1. User enters cash_paid (cash paid towards taxes)
    # 2. User selects cash_covered_all (did cash cover all taxes?)
    # 3. If not fully covered, user enters shares_sold (shares sold to cover remainder)
    cash_paid = db.Column(db.Float, default=0.0)  # Cash paid towards taxes
    cash_covered_all = db.Column(db.Boolean, default=True)  # Did cash cover all taxes?
    shares_sold = db.Column(db.Float, default=0.0)  # Shares sold to cover remaining taxes
    tax_year = db.Column(db.Integer, nullable=True)  # Tax year for historical rate tracking
    notes = db.Column(db.Text, nullable=True)  # User notes about this vest event
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self) -> str:
        return f'<VestEvent {self.vest_date} - {self.shares_vested} shares>'
    
    @property
    def has_vested(self) -> bool:
        """Check if vest date has passed (based on today's date)."""
        vest_date = self.vest_date
        # Handle both datetime and date objects
        if isinstance(vest_date, datetime):
            vest_date = vest_date.date()
        return vest_date <= date.today()
    
    @property
    def share_price_at_vest(self) -> float:
        """
        Get the stock price at vest date from user's encrypted prices.
        For unvested events (future dates), returns current stock price as estimate.
        For vested events, returns actual historical price at vest date.
        """
        try:
            # For unvested shares, use latest available price (current price)
            if not self.has_vested:
                price = get_latest_user_price(self.grant.user_id)  # Latest price (today or before)
                return price if price is not None else 0.0
            
            # For vested shares, get actual price at vest date
            price = get_latest_user_price(self.grant.user_id, as_of_date=self.vest_date)
            return price if price is not None else 0.0
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error getting share_price_at_vest: {str(e)}", exc_info=True)
            return 0.0
    
    @property
    def value_at_vest(self) -> float:
        """
        Calculate value at vest based on current stock price data.
        For ISOs (stock options): value = shares × (price_at_vest - strike_price)
        For RSUs/RSAs: value = shares × price_at_vest
        For CASH: value = cash amount (shares_vested represents USD amount)
        """
        from app.models.grant import ShareType
        
        # Cash bonuses: shares_vested represents USD amount
        if self.grant.share_type == ShareType.CASH.value:
            return self.shares_vested
        
        price_at_vest = self.share_price_at_vest
        
        # For ISOs, calculate the spread (price at vest - strike price)
        if self.grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
            spread = price_at_vest - self.grant.share_price_at_grant
            return self.shares_vested * spread
        
        # For RSUs/RSAs/ESPP, use full price at vest
        return self.shares_vested * price_at_vest
    
    @property
    def shares_withheld_for_taxes(self) -> float:
        """Calculate total shares withheld/sold for taxes."""
        # For cash bonuses, this represents USD withheld
        # New simplified logic: just return shares_sold directly
        # (shares_sold is what user enters when cash didn't cover all taxes)
        return self.shares_sold if self.shares_sold else 0.0
    
    @property
    def shares_received(self) -> float:
        """Calculate actual shares physically received after taxes (or USD for cash bonuses)."""
        return self.shares_vested - self.shares_withheld_for_taxes
    
    @property
    def needs_tax_info(self) -> bool:
        """Check if vested event is missing tax payment information."""
        if not self.has_vested:
            return False
        # ESPP/nqESPP don't need tax info - taxes handled at receipt
        if self.grant.grant_type in ['espp', 'nqespp']:
            return False
        # Needs info if vested but no cash paid recorded (for past vests)
        return self.cash_paid == 0 and self.shares_sold == 0
    
    @property
    def net_value(self) -> float:
        """
        Calculate net value of shares received.
        For ISOs: net_value = shares_received × (price_at_vest - strike_price)
        For RSUs/RSAs: net_value = shares_received × price_at_vest
        For CASH: net_value = USD amount received after taxes
        """
        from app.models.grant import ShareType
        
        # Cash bonuses: shares_received represents USD amount
        if self.grant.share_type == ShareType.CASH.value:
            return self.shares_received
        
        price_at_vest = self.share_price_at_vest
        if not price_at_vest:
            return 0.0
        
        # For ISOs, calculate based on spread
        if self.grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
            spread = price_at_vest - self.grant.share_price_at_grant
            return self.shares_received * spread
        
        # For RSUs/RSAs/ESPP, use full price
        return self.shares_received * price_at_vest
    
    @property
    def tax_withheld(self) -> float:
        """
        Calculate total tax withheld (cash paid + value of shares sold).
        For cash bonuses: cash_paid + shares_sold (both in USD)
        For stock grants: cash_paid + (shares_sold × price_at_vest)
        """
        from app.models.grant import ShareType
        
        total_tax = self.cash_paid
        
        # Cash bonuses: shares_sold represents USD amount withheld
        if self.grant.share_type == ShareType.CASH.value:
            total_tax += self.shares_sold
        else:
            # For stock grants: convert shares_sold to USD
            if self.shares_sold > 0:
                total_tax += self.shares_sold * self.share_price_at_vest
        
        return total_tax
    
    def get_comprehensive_tax_breakdown(self, _tax_profile=None, _annual_incomes=None, _cached_rates=None, _year_income=None) -> dict:
        """
        Get detailed tax breakdown including FICA, Medicare, Social Security.
        Uses user's tax profile for accurate calculations.
        Uses stored tax_year if available, otherwise defaults to vest year.
        
        Args:
            _tax_profile: INTERNAL - cached tax profile to avoid N+1 queries
            _annual_incomes: INTERNAL - dict of {year: income} to avoid N+1 queries
            _cached_rates: INTERNAL - pre-calculated tax rates dict to avoid TaxBracket queries
            _year_income: INTERNAL - total income for this vest's year (for effective SS rate calculation)
        """
        try:
            from app.models.tax_rate import UserTaxProfile
            from app.models.annual_income import AnnualIncome
            from app.utils.tax_calculator import TaxCalculator
            from datetime import date
            
            # Get user's tax profile (use cached if available)
            if _tax_profile is None:
                tax_profile = UserTaxProfile.query.filter_by(user_id=self.grant.user_id).first()
            else:
                tax_profile = _tax_profile
                
            if not tax_profile:
                # Return basic breakdown if no profile
                return {
                    'has_breakdown': False,
                    'gross_value': self.value_at_vest,
                    'total_tax': self.tax_withheld,
                    'net_value': self.net_value
                }
            
            # Determine tax year: use stored tax_year, or default to vest year
            tax_year = self.tax_year or self.vest_date.year
            
            # Get income for the specific year (use cached if available)
            if _annual_incomes is not None:
                annual_income = _annual_incomes.get(tax_year, tax_profile.annual_income)
            else:
                year_income = AnnualIncome.query.filter_by(
                    user_id=self.grant.user_id,
                    year=tax_year
                ).first()
                annual_income = year_income.annual_income if year_income else tax_profile.annual_income
            
            if not annual_income:
                # Return basic breakdown if no income data
                return {
                    'has_breakdown': False,
                    'gross_value': self.value_at_vest,
                    'total_tax': self.tax_withheld,
                    'net_value': self.net_value,
                    'missing_year': tax_year  # Flag that we're missing this year's income
                }
            
            # Get federal and state rates for the specific tax year using year-specific income
            # Use cached rates if provided (to avoid TaxBracket queries)
            if _cached_rates:
                rates = _cached_rates
            else:
                # Create temporary profile with year-specific income
                temp_profile = UserTaxProfile(
                    user_id=tax_profile.user_id,
                    state=tax_profile.state,
                    filing_status=tax_profile.filing_status or 'single',
                    annual_income=annual_income,
                    use_manual_rates=False
                )
                rates = temp_profile.get_tax_rates(tax_year=tax_year)
            
            # Ensure filing_status has a default value
            filing_status = tax_profile.filing_status or 'single'
            
            # Initialize tax calculator with YEAR-SPECIFIC income (not current year)
            calculator = TaxCalculator(
                annual_income=annual_income,  # Use the year-specific income from above
                filing_status=filing_status,
                state=tax_profile.state
            )
            
            # For past years, use effective rates based on total annual income
            # For current/future years, use marginal rates with progressive calculation
            if tax_year < date.today().year:
                # Past year: calculate effective rates for ALL taxes based on total income
                year_total_income = _year_income if _year_income is not None else annual_income
                
                # Get effective rates from tax profile
                effective_rates = tax_profile.get_effective_tax_rates(year_total_income, tax_year)
                
                # Set effective rates in calculator
                calculator.set_effective_rates(
                    effective_federal=effective_rates['federal'],
                    effective_state=effective_rates['state'],
                    effective_medicare=effective_rates['medicare'],
                    effective_ss=effective_rates['social_security']
                )
            else:
                # Current/future year: use actual YTD wages from profile for progressive SS calc
                # If YTD wages not configured, use annual_income as proxy (assumes even distribution)
                # This prevents double-taxation (high federal + full SS) on high earners
                ytd_wages = tax_profile.ytd_wages if tax_profile.ytd_wages else annual_income
                calculator.set_ytd_wages(ytd_wages)
            
            # Calculate comprehensive taxes
            breakdown = calculator.calculate_vest_taxes(
                vest_value=self.value_at_vest,
                federal_rate=rates['federal'],
                state_rate=rates['state']
            )
            
            breakdown['has_breakdown'] = True
            breakdown['tax_year'] = tax_year
            return breakdown
            
        except Exception as e:
            # Log the error but don't crash
            import logging
            logging.getLogger(__name__).error(f"Error in get_comprehensive_tax_breakdown: {e}")
            # Fallback to basic breakdown
            return {
                'has_breakdown': False,
                'gross_value': self.value_at_vest,
                'total_tax': self.tax_withheld,
                'net_value': self.net_value
            }
    
    def estimate_tax_withholding(self, current_stock_price: float = None, 
                                 federal_rate: float = None, 
                                 state_rate: float = None, 
                                 fica_rate: float = None,
                                 _tax_profile = None) -> dict:
        """
        Estimate tax withholding for future vesting events.
        Uses comprehensive tax calculator for accurate FICA calculations.
        Returns dict with estimated_tax, is_estimated flag.
        
        For unvested events: calculates based on current/projected stock price and tax profile
        For vested events: returns actual tax_withheld
        
        Args:
            current_stock_price: Stock price to use for estimation (defaults to latest)
            federal_rate: Federal tax rate (optional - will use tax profile if not provided)
            state_rate: State tax rate (optional - will use tax profile if not provided)
            fica_rate: DEPRECATED - now calculated accurately using TaxCalculator
            _tax_profile: INTERNAL - cached tax profile to avoid N+1 queries
        """
        from app.models.grant import ShareType, GrantType
        from app.models.tax_rate import UserTaxProfile
        from app.utils.tax_calculator import TaxCalculator
        
        # If already vested, return actual taxes paid
        if self.has_vested:
            return {
                'tax_amount': self.tax_withheld,
                'is_estimated': False,
                'tax_rate': 0.0  # Not calculated for actual
            }
        
        # For future vests, estimate based on current price
        if current_stock_price is None:
            from app.utils.price_utils import get_latest_user_price
            current_stock_price = get_latest_user_price(self.grant.user_id) or 0.0
        
        # Get user's tax profile for accurate calculation (use cached if available)
        if _tax_profile is None:
            tax_profile = UserTaxProfile.query.filter_by(user_id=self.grant.user_id).first()
        else:
            tax_profile = _tax_profile
        
        # Calculate vest value based on grant type
        if self.grant.share_type == ShareType.CASH.value:
            vest_value = self.shares_vested
        elif self.grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
            # ISOs: tax on spread (current_price - strike_price)
            spread = current_stock_price - self.grant.share_price_at_grant
            vest_value = self.shares_vested * spread if spread > 0 else 0.0
        elif self.grant.grant_type == GrantType.ESPP.value and self.grant.espp_discount:
            # ESPP: tax on discount portion (ordinary income)
            discount_gain = self.shares_vested * current_stock_price * self.grant.espp_discount
            vest_value = discount_gain
        else:
            # RSUs/RSAs: full value is taxable as ordinary income
            vest_value = self.shares_vested * current_stock_price
        
        # If we have a tax profile, use comprehensive calculator
        if tax_profile and tax_profile.annual_income:
            try:
                # Get tax rates for this year (use provided rates if available)
                if federal_rate is None or state_rate is None:
                    rates = tax_profile.get_tax_rates(tax_year=self.vest_date.year)
                    if federal_rate is None:
                        federal_rate = rates['federal']
                    if state_rate is None:
                        state_rate = rates['state']
                
                # Use TaxCalculator for accurate FICA calculation
                calculator = TaxCalculator(
                    annual_income=tax_profile.annual_income,
                    filing_status=tax_profile.filing_status or 'single',
                    state=tax_profile.state
                )
                # Set YTD wages for SS wage base limit
                # Use actual YTD wages if provided (from paycheck), otherwise use annual_income
                # YTD wages determine when SS tax stops (at $168,600 wage base)
                ytd_for_calc = tax_profile.ytd_wages if tax_profile.ytd_wages else tax_profile.annual_income
                calculator.set_ytd_wages(ytd_for_calc)
                
                # Calculate comprehensive taxes
                breakdown = calculator.calculate_vest_taxes(
                    vest_value=vest_value,
                    federal_rate=federal_rate,
                    state_rate=state_rate
                )
                
                return {
                    'tax_amount': breakdown['total_tax'],
                    'is_estimated': True,
                    'tax_rate': breakdown['effective_rate']
                }
            except Exception:
                # Fall back to simple calculation if comprehensive fails
                pass
        
        # Fallback: simple calculation if no tax profile
        # Use provided rates or defaults
        if federal_rate is None:
            federal_rate = 0.22
        if state_rate is None:
            state_rate = 0.093
        if fica_rate is None:
            fica_rate = 0.0765
        
        estimated_rate = federal_rate + state_rate + fica_rate
        estimated_tax = vest_value * estimated_rate
        
        return {
            'tax_amount': estimated_tax,
            'is_estimated': True,
            'tax_rate': estimated_rate
        }
    
    def get_estimated_sale_tax(self, current_stock_price: float = None, 
                               total_sold: float = 0, 
                               total_exercised: float = 0,
                               _tax_profile=None,
                               _annual_incomes=None) -> dict:
        """
        Calculate estimated capital gains tax on remaining shares if sold today.
        
        This is the SINGLE SOURCE OF TRUTH for sale tax calculations across the app.
        Used by finance_deep_dive, vest_detail, and any other page showing sale tax estimates.
        
        Args:
            current_stock_price: Current stock price (defaults to latest user price)
            total_sold: Total shares already sold from this vest
            total_exercised: Total shares already exercised (for ISOs)
            _tax_profile: INTERNAL - cached tax profile to avoid N+1 queries
            _annual_incomes: INTERNAL - dict of {year: income} to avoid N+1 queries
            
        Returns:
            dict with:
                - shares_held: Remaining shares
                - cost_basis_per_share: Cost basis per share
                - cost_basis: Total cost basis
                - current_value: Current market value
                - unrealized_gain: Capital gain if sold today
                - days_held: Days since vest
                - is_long_term: True if held > 365 days
                - holding_period: Formatted string (e.g., "2y 45d" or "180d")
                - estimated_tax: Total estimated tax
                - federal_tax: Federal capital gains tax
                - niit_tax: NIIT (3.8% surtax)
                - state_tax: State capital gains tax
                - federal_rate: Federal rate used
                - state_rate: State rate used
                - method: 'professional' or 'simplified'
        """
        from app.models.grant import ShareType
        from app.models.tax_rate import UserTaxProfile
        from app.models.annual_income import AnnualIncome
        from app.utils.capital_gains_calculator import CapitalGainsCalculator
        from datetime import date
        
        # Get current stock price if not provided
        if current_stock_price is None:
            from app.utils.price_utils import get_latest_user_price
            current_stock_price = get_latest_user_price(self.grant.user_id) or 0.0
        
        # Calculate remaining shares
        shares_held = self.shares_received - total_sold - total_exercised
        
        # For cash grants, no capital gains (cash doesn't appreciate)
        if self.grant.share_type == ShareType.CASH.value:
            return {
                'shares_held': shares_held,
                'cost_basis_per_share': 1.0,
                'cost_basis': shares_held,
                'current_value': shares_held,
                'unrealized_gain': 0.0,
                'days_held': 0,
                'is_long_term': False,
                'holding_period': '—',
                'estimated_tax': 0.0,
                'federal_tax': 0.0,
                'niit_tax': 0.0,
                'state_tax': 0.0,
                'federal_rate': 0.0,
                'state_rate': 0.0,
                'method': 'n/a'
            }
        
        # Determine cost basis based on grant type
        # ISOs: cost basis is strike price (share_price_at_grant)
        # RSUs/RSAs/ESPP: cost basis is FMV at vest (share_price_at_vest)
        if self.grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
            cost_basis_per_share = self.grant.share_price_at_grant
        else:
            # For unvested shares, share_price_at_vest is 0 (unknown future price)
            # Use current price as estimated cost basis for projection purposes
            cost_basis_per_share = self.share_price_at_vest if self.has_vested else current_stock_price
        
        # Calculate values
        cost_basis = shares_held * cost_basis_per_share
        current_value = shares_held * current_stock_price
        unrealized_gain = current_value - cost_basis
        
        # Calculate holding period
        today = date.today()
        days_held = (today - self.vest_date).days if self.has_vested else 0
        is_long_term = days_held >= 365
        
        if self.has_vested:
            if days_held >= 365:
                years = days_held // 365
                holding_period = f"{years}y {days_held % 365}d"
            else:
                holding_period = f"{days_held}d"
        else:
            holding_period = "—"
        
        # Calculate estimated tax
        # Get tax profile (use cached if available)
        if _tax_profile is None:
            tax_profile = UserTaxProfile.query.filter_by(user_id=self.grant.user_id).first()
        else:
            tax_profile = _tax_profile
        
        if not tax_profile or unrealized_gain <= 0:
            # No tax profile or no gain = no tax
            return {
                'shares_held': shares_held,
                'cost_basis_per_share': cost_basis_per_share,
                'cost_basis': cost_basis,
                'current_value': current_value,
                'unrealized_gain': unrealized_gain,
                'days_held': days_held,
                'is_long_term': is_long_term,
                'holding_period': holding_period,
                'estimated_tax': 0.0,
                'federal_tax': 0.0,
                'niit_tax': 0.0,
                'state_tax': 0.0,
                'federal_rate': 0.0,
                'state_rate': 0.0,
                'method': 'none'
            }
        
        # Get total annual income for current year (use cached if available)
        if _annual_incomes is not None:
            total_annual_income = _annual_incomes.get(today.year, tax_profile.annual_income)
        else:
            year_income = AnnualIncome.query.filter_by(
                user_id=self.grant.user_id,
                year=today.year
            ).first()
            total_annual_income = year_income.annual_income if year_income else tax_profile.annual_income
        
        # Use professional capital gains calculator
        try:
            # Get state rate safely before starting calculation
            state_rate = 0.093  # Default CA rate
            try:
                if tax_profile.use_manual_rates and tax_profile.manual_state_rate:
                    state_rate = tax_profile.manual_state_rate
                elif tax_profile.state:
                    # Try to get calculated state rate, but don't fail if it errors
                    rates = tax_profile.get_tax_rates()
                    state_rate = rates.get('state', 0.093)
            except Exception:
                # If getting tax rates fails, use default
                state_rate = 0.093
            
            calculator = CapitalGainsCalculator(
                total_annual_income=total_annual_income,
                filing_status=tax_profile.filing_status or 'single',
                state=tax_profile.state,
                tax_year=today.year
            )
            
            # Calculate taxes on the unrealized gain
            tax_result = calculator.calculate_sale_taxes(
                capital_gain=unrealized_gain,
                purchase_date=self.vest_date,
                sale_date=today,
                state_rate=state_rate
            )
            
            return {
                'shares_held': shares_held,
                'cost_basis_per_share': cost_basis_per_share,
                'cost_basis': cost_basis,
                'current_value': current_value,
                'unrealized_gain': unrealized_gain,
                'days_held': days_held,
                'is_long_term': is_long_term,
                'holding_period': holding_period,
                'estimated_tax': tax_result.get('total_tax', 0),
                'federal_tax': tax_result.get('federal_tax', 0),
                'niit_tax': tax_result.get('niit_tax', 0),
                'state_tax': tax_result.get('state_tax', 0),
                'federal_rate': tax_result.get('federal_rate', 0),
                'state_rate': tax_result.get('state_rate', 0),
                'method': 'professional'
            }
        except Exception as e:
            # Fallback to simplified calculation
            import logging
            logging.getLogger(__name__).error(f"Error in professional sale tax calc: {e}")
            
            # Use hardcoded rates to avoid additional DB queries in failed transaction
            federal_rate = 0.15 if is_long_term else 0.24
            state_rate = 0.093
            
            # Try to get manual rates if available (no DB query needed)
            try:
                if tax_profile.use_manual_rates:
                    if is_long_term and tax_profile.manual_ltcg_rate:
                        federal_rate = tax_profile.manual_ltcg_rate
                    elif not is_long_term and tax_profile.manual_federal_rate:
                        federal_rate = tax_profile.manual_federal_rate
                    if tax_profile.manual_state_rate:
                        state_rate = tax_profile.manual_state_rate
            except Exception:
                pass  # Use defaults
            
            federal_tax = unrealized_gain * federal_rate
            state_tax = unrealized_gain * state_rate
            estimated_tax = federal_tax + state_tax
            
            return {
                'shares_held': shares_held,
                'cost_basis_per_share': cost_basis_per_share,
                'cost_basis': cost_basis,
                'current_value': current_value,
                'unrealized_gain': unrealized_gain,
                'days_held': days_held,
                'is_long_term': is_long_term,
                'holding_period': holding_period,
                'estimated_tax': estimated_tax,
                'federal_tax': federal_tax,
                'niit_tax': 0.0,
                'state_tax': state_tax,
                'federal_rate': federal_rate,
                'state_rate': state_rate,
                'method': 'simplified'
            }
