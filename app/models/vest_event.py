"""
Vest event model for tracking individual vesting events.
"""

from app import db
from datetime import datetime, date


class VestEvent(db.Model):
    """Individual vesting event for a grant."""
    
    __tablename__ = 'vest_events'
    
    id = db.Column(db.Integer, primary_key=True)
    grant_id = db.Column(db.Integer, db.ForeignKey('grants.id'), nullable=False, index=True)
    
    # Vest details
    vest_date = db.Column(db.Date, nullable=False)
    shares_vested = db.Column(db.Float, nullable=False)
    # Note: share_price_at_vest is now a @property that calculates dynamically
    
    # Tax handling
    payment_method = db.Column(db.String(20), default='sell_to_cover')  # 'sell_to_cover' or 'cash_to_cover'
    cash_to_cover = db.Column(db.Float, default=0.0)
    shares_sold_to_cover = db.Column(db.Float, default=0.0)
    
    # Status
    is_vested = db.Column(db.Boolean, default=False)
    vested_at = db.Column(db.DateTime, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self) -> str:
        return f'<VestEvent {self.vest_date} - {self.shares_vested} shares>'
    
    @property
    def has_vested(self) -> bool:
        """Check if vest date has passed (based on today's date)."""
        return self.vest_date <= date.today()
    
    @property
    def needs_tax_info(self) -> bool:
        """Check if vested event is missing tax payment information."""
        if not self.has_vested:
            return False
        # Need info if vested but no cash or shares specified
        return self.cash_to_cover == 0 and self.shares_sold_to_cover == 0
    
    @property
    def share_price_at_vest(self) -> float:
        """Get the stock price at vest date dynamically from stock_prices table."""
        from app.models.stock_price import StockPrice
        from app.utils.init_db import get_stock_price_at_date
        
        # Get the most recent stock price on or before the vest date
        price = get_stock_price_at_date(self.vest_date)
        return price if price else 0.0
    
    @property
    def value_at_vest(self) -> float:
        """
        Calculate value at vest based on current stock price data.
        For ISOs (stock options): value = shares × (price_at_vest - strike_price)
        For RSUs/RSAs: value = shares × price_at_vest
        """
        from app.models.grant import ShareType
        
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
        share_price = self.share_price_at_vest
        if self.payment_method == 'cash_to_cover' and self.cash_to_cover > 0 and share_price:
            # Convert cash paid to equivalent shares
            return self.cash_to_cover / share_price
        elif self.payment_method == 'sell_to_cover':
            return self.shares_sold_to_cover
        return 0.0
    
    @property
    def shares_received(self) -> float:
        """Calculate actual shares physically received after taxes."""
        return self.shares_vested - self.shares_withheld_for_taxes
    
    @property
    def net_value(self) -> float:
        """
        Calculate net value of shares received.
        For ISOs: net_value = shares_received × (price_at_vest - strike_price)
        For RSUs/RSAs: net_value = shares_received × price_at_vest
        """
        from app.models.grant import ShareType
        
        price_at_vest = self.share_price_at_vest
        if not price_at_vest:
            return 0.0
        
        # For ISOs, calculate based on spread
        if self.grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
            spread = price_at_vest - self.grant.share_price_at_grant
            return self.shares_received * spread
        
        # For RSUs/RSAs/ESPP, use full price
        return self.shares_received * price_at_vest
