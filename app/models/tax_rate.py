"""
Tax rate model for federal and state tax brackets.
"""

from app import db
from datetime import datetime


class TaxBracket(db.Model):
    """Tax bracket information for federal and state taxes."""
    
    __tablename__ = 'tax_brackets'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Jurisdiction
    jurisdiction = db.Column(db.String(50), nullable=False)  # 'federal' or state abbreviation (e.g., 'CA', 'TX')
    tax_year = db.Column(db.Integer, nullable=False, index=True)
    filing_status = db.Column(db.String(20), nullable=False)  # 'single', 'married_joint', 'married_separate', 'head_of_household'
    
    # Tax type
    tax_type = db.Column(db.String(20), nullable=False)  # 'ordinary', 'capital_gains_long'
    
    # Bracket details
    income_min = db.Column(db.Float, nullable=False)  # Minimum income for this bracket
    income_max = db.Column(db.Float, nullable=True)   # Maximum income (NULL for top bracket)
    rate = db.Column(db.Float, nullable=False)        # Tax rate as decimal (e.g., 0.22 for 22%)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self) -> str:
        return f'<TaxBracket {self.jurisdiction} {self.tax_year} {self.filing_status} ${self.income_min}-${self.income_max}: {self.rate*100}%>'


class UserTaxProfile(db.Model):
    """User's tax profile for automatic tax rate calculation."""
    
    __tablename__ = 'user_tax_profiles'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    
    # Tax profile
    state = db.Column(db.String(2), nullable=True)  # State abbreviation (e.g., 'CA', 'TX')
    filing_status = db.Column(db.String(20), default='single')  # 'single', 'married_joint', etc.
    annual_income = db.Column(db.Float, nullable=True)  # Total annual income
    # ytd_wages = db.Column(db.Float, default=0.0)  # Year-to-date wages for Social Security calculation (commented out until migration runs)
    
    # Manual override option
    use_manual_rates = db.Column(db.Boolean, default=False)
    manual_federal_rate = db.Column(db.Float, nullable=True)  # Manual federal rate override
    manual_state_rate = db.Column(db.Float, nullable=True)    # Manual state rate override
    manual_ltcg_rate = db.Column(db.Float, nullable=True)     # Manual long-term capital gains rate override
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('tax_profile', uselist=False))
    
    def __repr__(self) -> str:
        return f'<UserTaxProfile user_id={self.user_id} {self.state} ${self.annual_income}>'
    
    def get_tax_rates(self, tax_year: int = 2025) -> dict:
        """
        Calculate tax rates based on profile.
        
        Returns:
            dict: {'federal': float, 'state': float, 'ltcg': float}
        """
        # If using manual rates, return those
        if self.use_manual_rates:
            return {
                'federal': self.manual_federal_rate or 0.22,
                'state': self.manual_state_rate or 0.093,
                'ltcg': self.manual_ltcg_rate or 0.15
            }
        
        # Otherwise, calculate from brackets
        if not self.annual_income:
            # Default rates if no income specified
            return {'federal': 0.22, 'state': 0.093, 'ltcg': 0.15}
        
        # Get federal ordinary income rate
        federal_rate = self._get_rate_for_income('federal', 'ordinary', tax_year)
        
        # Get federal long-term capital gains rate
        ltcg_rate = self._get_rate_for_income('federal', 'capital_gains_long', tax_year)
        
        # Get state rate
        state_rate = 0.0
        if self.state:
            state_rate = self._get_rate_for_income(self.state, 'ordinary', tax_year)
        
        return {
            'federal': federal_rate,
            'state': state_rate,
            'ltcg': ltcg_rate
        }
    
    def _get_rate_for_income(self, jurisdiction: str, tax_type: str, tax_year: int) -> float:
        """Find the tax rate for the given income level."""
        # Safety check - if no filing status or income, return 0
        if not self.filing_status or not self.annual_income:
            return 0.0
            
        bracket = TaxBracket.query.filter_by(
            jurisdiction=jurisdiction,
            tax_year=tax_year,
            filing_status=self.filing_status,
            tax_type=tax_type
        ).filter(
            TaxBracket.income_min <= self.annual_income
        ).filter(
            db.or_(
                TaxBracket.income_max >= self.annual_income,
                TaxBracket.income_max.is_(None)
            )
        ).first()
        
        return bracket.rate if bracket else 0.0
