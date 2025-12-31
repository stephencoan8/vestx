"""
Grant model for stock compensation grants.
"""

from app import db
from datetime import datetime
from enum import Enum


class GrantType(str, Enum):
    """Types of grants available."""
    NEW_HIRE = "new_hire"
    ANNUAL_PERFORMANCE = "annual_performance"
    PROMOTION = "promotion"
    KICKASS = "kickass"
    ESPP = "espp"
    NQESPP = "nqespp"


class ShareType(str, Enum):
    """Types of shares."""
    RSU = "rsu"
    ISO_5Y = "iso_5y"
    ISO_6Y = "iso_6y"
    CASH = "cash"


class BonusType(str, Enum):
    """Bonus payout types."""
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


class Grant(db.Model):
    """Grant model representing a stock compensation grant."""
    
    __tablename__ = 'grants'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Grant details
    grant_date = db.Column(db.Date, nullable=False)
    grant_type = db.Column(db.String(50), nullable=False)
    share_type = db.Column(db.String(20), nullable=False)
    share_quantity = db.Column(db.Float, nullable=False)
    share_price_at_grant = db.Column(db.Float, nullable=False)
    
    # Vesting details
    vest_years = db.Column(db.Integer, nullable=False)
    cliff_years = db.Column(db.Float, nullable=False)
    
    # ESPP specific - discount percentage (typically 15% = 0.15)
    espp_discount = db.Column(db.Float, nullable=True, default=0.0)
    
    # For annual performance grants
    bonus_type = db.Column(db.String(20), nullable=True)  # short_term or long_term
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text, nullable=True)
    
    # Relationships
    vest_events = db.relationship('VestEvent', backref='grant', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self) -> str:
        return f'<Grant {self.grant_type} - {self.share_quantity} {self.share_type}>'
    
    @property
    def total_value_at_grant(self) -> float:
        """
        Calculate total value at grant (historical).
        For ISOs: returns 0 (options have no intrinsic value at grant)
        For RSUs/RSAs: returns shares × price at grant
        For CASH: returns the cash amount (share_quantity represents USD amount)
        """
        # Cash bonuses: share_quantity represents USD amount
        if self.share_type == ShareType.CASH.value:
            return self.share_quantity
        
        # ISOs are options with no intrinsic value at grant
        if self.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
            return 0.0
        
        # For RSUs/RSAs/ESPP, calculate actual share value
        return self.share_quantity * self.share_price_at_grant
    
    @property
    def current_share_price(self) -> float:
        """Get the current (latest) stock price from user's encrypted prices."""
        try:
            from app.models.user_price import UserPrice
            from app.utils.encryption import decrypt_for_user
            from flask_login import current_user
            
            # Get the user's latest price entry
            price_entry = UserPrice.query.filter_by(user_id=self.user_id).order_by(
                UserPrice.valuation_date.desc()
            ).first()
            
            if price_entry and current_user.is_authenticated:
                user_key = current_user.get_decrypted_user_key()
                price_str = decrypt_for_user(user_key, price_entry.encrypted_price)
                return float(price_str)
            
            # Default to 0 if no price found
            return 0.0
        except Exception:
            # If any error occurs, return 0
            return 0.0
    
    @property
    def actual_cost_basis(self) -> float:
        """
        Calculate actual cost basis per share.
        For ESPP: market_price × (1 - discount) = what you actually paid
        For NQESPP: full market price
        For ISOs: strike price
        For RSUs/Cash: $0 (granted, not purchased)
        """
        # ESPP with discount (typically 15%)
        if self.grant_type == GrantType.ESPP.value and self.espp_discount:
            return self.share_price_at_grant * (1 - self.espp_discount)
        
        # NQESPP or ISOs - full strike/market price
        if self.grant_type == GrantType.NQESPP.value or self.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
            return self.share_price_at_grant
        
        # RSUs and Cash grants - no cost basis (granted)
        return 0.0
    
    @property
    def espp_discount_gain(self) -> float:
        """
        Calculate immediate gain from ESPP discount.
        This is the discount value: shares × market_price × discount_rate
        Example: 100 shares × $100 × 0.15 = $1,500 immediate gain
        """
        if self.grant_type == GrantType.ESPP.value and self.espp_discount:
            return self.share_quantity * self.share_price_at_grant * self.espp_discount
        return 0.0
    
    @property
    def current_value(self) -> float:
        """
        Calculate current total value based on latest stock price.
        For ISOs (stock options): value = shares × (current_price - strike_price)
        For RSUs/RSAs: value = shares × current_price
        For CASH: value = cash amount (doesn't change with stock price)
        """
        # Cash bonuses: value is fixed USD amount
        if self.share_type == ShareType.CASH.value:
            return self.share_quantity
        
        current_price = self.current_share_price
        
        # For ISOs, calculate the spread (current price - strike price)
        if self.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
            spread = current_price - self.share_price_at_grant
            return self.share_quantity * spread
        
        # For RSUs/RSAs/ESPP, use full current price
        return self.share_quantity * current_price
