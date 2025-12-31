from app import db
from datetime import datetime


class UserPrice(db.Model):
    __tablename__ = 'user_prices'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    valuation_date = db.Column(db.Date, nullable=False, index=True)
    encrypted_price = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<UserPrice {self.user_id} @ {self.valuation_date}>'
