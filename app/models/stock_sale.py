"""
Stock sale tracking model.
Records actual sales of vested shares.
"""

from app import db
from datetime import datetime


class StockSale(db.Model):
    """Track actual stock sales for tax calculation and cost basis tracking."""
    
    __tablename__ = 'stock_sales'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    vest_event_id = db.Column(db.Integer, db.ForeignKey('vest_events.id'), nullable=True)  # Which vest these shares came from
    
    # Sale details
    sale_date = db.Column(db.Date, nullable=False, index=True)
    shares_sold = db.Column(db.Float, nullable=False)
    sale_price = db.Column(db.Float, nullable=False)  # Price per share
    total_proceeds = db.Column(db.Float, nullable=False)  # Total cash received
    
    # Cost basis (from the vest event)
    cost_basis_per_share = db.Column(db.Float, nullable=False)  # FMV at vest (or strike for ISOs)
    total_cost_basis = db.Column(db.Float, nullable=False)
    
    # Calculated gain/loss
    capital_gain = db.Column(db.Float, nullable=False)  # proceeds - cost basis
    is_long_term = db.Column(db.Boolean, nullable=False)  # Held > 1 year
    
    # Actual taxes paid (user-entered after filing)
    actual_federal_tax = db.Column(db.Float, nullable=True)  # Federal tax actually paid on this sale
    actual_state_tax = db.Column(db.Float, nullable=True)    # State tax actually paid on this sale
    actual_total_tax = db.Column(db.Float, nullable=True)    # Total tax actually paid (convenience field)
    
    # ISO-specific tracking
    is_qualifying_disposition = db.Column(db.Boolean, nullable=True)  # For ISOs: met 2+1 year rule?
    disqualifying_ordinary_income = db.Column(db.Float, nullable=True)  # Bargain element if disqualifying
    
    # Wash sale tracking
    is_wash_sale = db.Column(db.Boolean, default=False)
    wash_sale_loss_disallowed = db.Column(db.Float, nullable=True)
    
    # Transaction fees
    commission_fees = db.Column(db.Float, default=0.0)
    
    # Tax lot identification method
    lot_selection_method = db.Column(db.String(20), default='FIFO')  # FIFO, LIFO, SpecID, HIFO
    
    # Notes
    notes = db.Column(db.Text, nullable=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref='stock_sales')
    vest_event = db.relationship('VestEvent', backref='stock_sales')
    
    def __repr__(self) -> str:
        return f'<StockSale {self.sale_date} - {self.shares_sold} shares @ ${self.sale_price}>'
    
    @property
    def holding_period_days(self) -> int:
        """Calculate holding period in days."""
        if not self.vest_event:
            return 0
        return (self.sale_date - self.vest_event.vest_date).days
    
    @property
    def net_proceeds(self) -> float:
        """Net proceeds after fees."""
        return self.total_proceeds - self.commission_fees
    
    @property
    def recognized_gain_loss(self) -> float:
        """Capital gain/loss after wash sale adjustment."""
        if self.is_wash_sale and self.wash_sale_loss_disallowed:
            # If it's a loss that's disallowed, gain is 0
            if self.capital_gain < 0:
                return 0.0
        return self.capital_gain


class ISOExercise(db.Model):
    """Track ISO exercise events separately (critical for AMT calculation)."""
    
    __tablename__ = 'iso_exercises'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    vest_event_id = db.Column(db.Integer, db.ForeignKey('vest_events.id'), nullable=False)
    
    # Exercise details
    exercise_date = db.Column(db.Date, nullable=False, index=True)
    shares_exercised = db.Column(db.Float, nullable=False)
    strike_price = db.Column(db.Float, nullable=False)  # Exercise price per share
    fmv_at_exercise = db.Column(db.Float, nullable=False)  # Fair market value at exercise
    
    # AMT calculation
    bargain_element_per_share = db.Column(db.Float, nullable=False)  # FMV - strike
    total_bargain_element = db.Column(db.Float, nullable=False)  # Total AMT adjustment
    amt_triggered = db.Column(db.Boolean, default=False)  # Did this trigger AMT?
    amt_paid = db.Column(db.Float, nullable=True)  # Actual AMT paid (if known)
    amt_credit_generated = db.Column(db.Float, nullable=True)  # AMT credit to carry forward
    
    # Payment method
    cash_paid = db.Column(db.Float, nullable=True)  # Cash used to exercise
    shares_surrendered = db.Column(db.Float, nullable=True)  # If cashless exercise
    
    # Status tracking
    shares_still_held = db.Column(db.Float, nullable=False)  # Shares not yet sold
    grant_date = db.Column(db.Date, nullable=True)  # For holding period calculation
    
    # Notes
    notes = db.Column(db.Text, nullable=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref='iso_exercises')
    vest_event = db.relationship('VestEvent', backref='iso_exercises')
    
    def __repr__(self) -> str:
        return f'<ISOExercise {self.exercise_date} - {self.shares_exercised} shares @ ${self.strike_price}>'
    
    @property
    def total_exercise_cost(self) -> float:
        """Total cost to exercise."""
        return self.shares_exercised * self.strike_price
    
    @property
    def meets_qualifying_holding_period(self, sale_date=None) -> bool:
        """
        Check if meets ISO qualifying disposition rules:
        - 2 years from grant date
        - 1 year from exercise date
        
        Args:
            sale_date: Date of sale (if None, checks if requirements CAN be met with today's date)
        """
        from datetime import date
        check_date = sale_date if sale_date else date.today()
        
        if not self.grant_date:
            return False
        
        # Must hold 2 years from grant
        years_from_grant = (check_date - self.grant_date).days / 365.25
        
        # Must hold 1 year from exercise
        years_from_exercise = (check_date - self.exercise_date).days / 365.25
        
        return years_from_grant >= 2 and years_from_exercise >= 1


class StockPriceScenario(db.Model):
    """User-defined future stock price scenarios for projection modeling."""
    
    __tablename__ = 'stock_price_scenarios'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Scenario details
    scenario_name = db.Column(db.String(100), nullable=False)  # "Base Case", "Bull Market", "Conservative"
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True)  # Use this scenario for projections?
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref='price_scenarios')
    price_points = db.relationship('ScenarioPricePoint', backref='scenario', cascade='all, delete-orphan')
    
    def __repr__(self) -> str:
        return f'<StockPriceScenario {self.scenario_name}>'
    
    def get_price_at_date(self, target_date):
        """Get projected stock price for a given date in this scenario."""
        from datetime import date as date_class
        
        # Get all price points for this scenario, ordered by date
        points = sorted(self.price_points, key=lambda p: p.price_date)
        
        if not points:
            return None
        
        # If before first point, use first price
        if target_date <= points[0].price_date:
            return points[0].price
        
        # If after last point, use last price
        if target_date >= points[-1].price_date:
            return points[-1].price
        
        # Linear interpolation between points
        for i in range(len(points) - 1):
            if points[i].price_date <= target_date <= points[i + 1].price_date:
                # Interpolate
                days_total = (points[i + 1].price_date - points[i].price_date).days
                days_elapsed = (target_date - points[i].price_date).days
                
                if days_total == 0:
                    return points[i].price
                
                price_change = points[i + 1].price - points[i].price
                interpolated_price = points[i].price + (price_change * days_elapsed / days_total)
                return interpolated_price
        
        return None


class ScenarioPricePoint(db.Model):
    """Individual price points within a scenario."""
    
    __tablename__ = 'scenario_price_points'
    
    id = db.Column(db.Integer, primary_key=True)
    scenario_id = db.Column(db.Integer, db.ForeignKey('stock_price_scenarios.id'), nullable=False)
    
    price_date = db.Column(db.Date, nullable=False, index=True)
    price = db.Column(db.Float, nullable=False)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self) -> str:
        return f'<ScenarioPricePoint {self.price_date}: ${self.price}>'
