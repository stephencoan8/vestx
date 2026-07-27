"""
Shared public market daily prices (post-IPO), not per-user encrypted.
"""

from app import db
from datetime import datetime


class MarketPrice(db.Model):
    """Daily public market close (or latest) for a listed ticker."""

    __tablename__ = 'market_prices'

    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(16), nullable=False, index=True)
    valuation_date = db.Column(db.Date, nullable=False, index=True)
    price_per_share = db.Column(db.Float, nullable=False)
    source = db.Column(db.String(32), nullable=True)  # yahoo, stooq, etc.
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('ticker', 'valuation_date', name='uq_market_ticker_date'),
    )

    def __repr__(self) -> str:
        return f'<MarketPrice {self.ticker} {self.valuation_date} ${self.price_per_share}>'
