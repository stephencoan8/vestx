"""
Annual income tracking by year for accurate historical tax calculations.
"""

from app import db
from datetime import datetime


class AnnualIncome(db.Model):
    """Track user's annual income by year for historical tax rate calculations."""
    
    __tablename__ = 'annual_incomes'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    year = db.Column(db.Integer, nullable=False, index=True)
    annual_income = db.Column(db.Float, nullable=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Unique constraint: one income record per user per year
    __table_args__ = (
        db.UniqueConstraint('user_id', 'year', name='uq_user_year_income'),
    )
    
    def __repr__(self) -> str:
        return f'<AnnualIncome user_id={self.user_id} year={self.year} income=${self.annual_income:,.0f}>'
