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
        """Get the stock price at vest date from user's encrypted prices."""
        try:
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
    
    def get_comprehensive_tax_breakdown(self, _tax_profile=None, _annual_incomes=None, _cached_rates=None, _ytd_at_vest=None) -> dict:
        """
        Get detailed tax breakdown including FICA, Medicare, Social Security.
        Uses user's tax profile for accurate calculations.
        Uses stored tax_year if available, otherwise defaults to vest year.
        
        Args:
            _tax_profile: INTERNAL - cached tax profile to avoid N+1 queries
            _annual_incomes: INTERNAL - dict of {year: income} to avoid N+1 queries
            _cached_rates: INTERNAL - pre-calculated tax rates dict to avoid TaxBracket queries
            _ytd_at_vest: INTERNAL - YTD wages at time of this vest (for accurate SS tax calculation)
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
            
            # Set YTD wages for SS calculation
            if _ytd_at_vest is not None:
                # Use provided YTD (calculated by caller to avoid N+1 queries)
                calculator.set_ytd_wages(_ytd_at_vest)
            elif tax_year >= date.today().year:
                # Current/future year: use actual YTD wages from profile
                calculator.set_ytd_wages(tax_profile.ytd_wages or 0.0)
            else:
                # Past year without YTD info: assume full annual income (conservative estimate)
                calculator.set_ytd_wages(annual_income)
            
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
