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
    
    def estimate_tax_withholding(self, current_stock_price: float = None, 
                                 federal_rate: float = 0.22, 
                                 state_rate: float = 0.093, 
                                 fica_rate: float = 0.0765) -> dict:
        """
        Estimate tax withholding for future vesting events.
        Returns dict with estimated_tax, is_estimated flag.
        
        For unvested events: calculates based on current/projected stock price and custom tax rates
        For vested events: returns actual tax_withheld
        
        Args:
            current_stock_price: Stock price to use for estimation (defaults to latest)
            federal_rate: Federal tax rate (default 22%)
            state_rate: State tax rate (default 9.3% for CA)
            fica_rate: FICA tax rate (default 7.65%)
        """
        from app.models.grant import ShareType, GrantType
        
        # If already vested, return actual taxes paid
        if self.has_vested:
            return {
                'tax_amount': self.tax_withheld,
                'is_estimated': False,
                'tax_rate': 0.0  # Not calculated for actual
            }
        
        # For future vests, estimate based on current price
        if current_stock_price is None:
            from app.utils.init_db import get_latest_stock_price
            current_stock_price = get_latest_stock_price() or 0.0
        
        # Cash grants - simple percentage
        if self.grant.share_type == ShareType.CASH.value:
            # Use custom tax rates
            estimated_rate = federal_rate + state_rate + fica_rate
            estimated_tax = self.shares_vested * estimated_rate
            return {
                'tax_amount': estimated_tax,
                'is_estimated': True,
                'tax_rate': estimated_rate
            }
        
        # Stock grants - calculate based on value at vest
        if self.grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
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
        
        # Calculate tax using custom rates
        estimated_rate = federal_rate + state_rate + fica_rate
        estimated_tax = vest_value * estimated_rate
        
        return {
            'tax_amount': estimated_tax,
            'is_estimated': True,
            'tax_rate': estimated_rate
        }
