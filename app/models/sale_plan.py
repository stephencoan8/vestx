"""
Sale Plan Model - stores user's planned vest sales by year
"""
from app import db
from datetime import datetime

class SalePlan(db.Model):
    """Track which vests user plans to sell in which year"""
    __tablename__ = 'sale_plans'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    vest_event_id = db.Column(db.Integer, db.ForeignKey('vest_events.id'), nullable=False)
    planned_sale_year = db.Column(db.Integer, nullable=False)  # Year user plans to sell
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref='sale_plans')
    vest_event = db.relationship('VestEvent', backref='sale_plans')
    
    def __repr__(self):
        return f'<SalePlan vest_event_id={self.vest_event_id} year={self.planned_sale_year}>'
