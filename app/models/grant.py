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
        """Calculate total value at grant (historical)."""
        return self.share_quantity * self.share_price_at_grant
    
    @property
    def current_share_price(self) -> float:
        """Get the current (latest) stock price."""
        from app.utils.init_db import get_latest_stock_price
        return get_latest_stock_price()
    
    @property
    def current_value(self) -> float:
        """
        Calculate current total value based on latest stock price.
        For ISOs (stock options): value = shares × (current_price - strike_price)
        For RSUs/RSAs: value = shares × current_price
        """
        current_price = self.current_share_price
        
        # For ISOs, calculate the spread (current price - strike price)
        if self.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
            spread = current_price - self.share_price_at_grant
            return self.share_quantity * spread
        
        # For RSUs/RSAs/ESPP, use full current price
        return self.share_quantity * current_price
