"""
Per-calendar-year tax inputs (wages, filing, credits).

Tax Profile page: pick year → load/save this row. Active year also mirrors
into TaxProfile for sale/goal engines.
"""

from __future__ import annotations

from datetime import datetime

from app import db


class TaxYearProfile(db.Model):
    __tablename__ = 'tax_year_profiles'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    tax_year = db.Column(db.Integer, nullable=False, index=True)

    filing_status = db.Column(db.String(20), default='single')
    state_code = db.Column(db.String(2), nullable=True)

    federal_ordinary_rate = db.Column(db.Float, nullable=True)
    federal_ltcg_rate = db.Column(db.Float, nullable=True)
    state_ordinary_rate = db.Column(db.Float, default=0.0)
    state_cg_rate = db.Column(db.Float, default=0.0)
    use_bracket_engine = db.Column(db.Boolean, default=True)
    use_state_engine = db.Column(db.Boolean, default=True)

    # W-2 / ordinary for this calendar year
    other_ordinary_income = db.Column(db.Float, default=0.0)
    ytd_wages = db.Column(db.Float, default=0.0)
    other_long_term_gains = db.Column(db.Float, default=0.0)
    other_short_term_gains = db.Column(db.Float, default=0.0)

    include_fica = db.Column(db.Boolean, default=True)
    ss_wage_base_maxed = db.Column(db.Boolean, default=False)
    include_niit = db.Column(db.Boolean, default=True)
    amt_credit_carryforward = db.Column(db.Float, default=0.0)
    ca_amt_credit_carryforward = db.Column(db.Float, default=0.0)

    federal_withholding_ytd = db.Column(db.Float, default=0.0)
    state_withholding_ytd = db.Column(db.Float, default=0.0)
    estimated_payments_ytd = db.Column(db.Float, default=0.0)
    itemize_salt = db.Column(db.Float, default=0.0)
    itemize_mortgage = db.Column(db.Float, default=0.0)
    itemize_charity = db.Column(db.Float, default=0.0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'tax_year', name='uq_tax_year_user_year'),
    )

    def to_form_dict(self) -> dict:
        return {
            'tax_year': self.tax_year,
            'filing_status': self.filing_status or 'single',
            'state_code': self.state_code or '',
            'federal_ordinary_rate': self.federal_ordinary_rate,
            'federal_ltcg_rate': self.federal_ltcg_rate,
            'state_ordinary_rate': self.state_ordinary_rate or 0.0,
            'state_cg_rate': self.state_cg_rate if self.state_cg_rate is not None else (self.state_ordinary_rate or 0.0),
            'use_bracket_engine': bool(self.use_bracket_engine if self.use_bracket_engine is not None else True),
            'use_state_engine': bool(self.use_state_engine if self.use_state_engine is not None else True),
            'other_ordinary_income': float(self.other_ordinary_income or 0),
            'ytd_wages': float(self.ytd_wages or 0),
            'other_long_term_gains': float(self.other_long_term_gains or 0),
            'other_short_term_gains': float(self.other_short_term_gains or 0),
            'include_fica': bool(self.include_fica if self.include_fica is not None else True),
            'ss_wage_base_maxed': bool(self.ss_wage_base_maxed),
            'include_niit': bool(self.include_niit if self.include_niit is not None else True),
            'amt_credit_carryforward': float(self.amt_credit_carryforward or 0),
            'ca_amt_credit_carryforward': float(self.ca_amt_credit_carryforward or 0),
            'federal_withholding_ytd': float(getattr(self, 'federal_withholding_ytd', 0) or 0),
            'state_withholding_ytd': float(getattr(self, 'state_withholding_ytd', 0) or 0),
            'estimated_payments_ytd': float(getattr(self, 'estimated_payments_ytd', 0) or 0),
            'itemize_salt': float(getattr(self, 'itemize_salt', 0) or 0),
            'itemize_mortgage': float(getattr(self, 'itemize_mortgage', 0) or 0),
            'itemize_charity': float(getattr(self, 'itemize_charity', 0) or 0),
        }

    @classmethod
    def get_for(cls, user_id: int, tax_year: int):
        return cls.query.filter_by(user_id=user_id, tax_year=int(tax_year)).first()

    @classmethod
    def upsert_from_form(cls, user_id: int, tax_year: int, data: dict) -> 'TaxYearProfile':
        row = cls.get_for(user_id, tax_year)
        if not row:
            row = cls(user_id=user_id, tax_year=int(tax_year))
            db.session.add(row)
        for key, val in data.items():
            if hasattr(row, key) and key not in ('id', 'user_id', 'tax_year', 'created_at'):
                setattr(row, key, val)
        row.tax_year = int(tax_year)
        row.updated_at = datetime.utcnow()
        return row

    def apply_to_main_profile(self, profile) -> None:
        """Mirror this year onto TaxProfile so sale/goal engines use it."""
        profile.tax_year = self.tax_year
        profile.filing_status = self.filing_status
        profile.state_code = self.state_code
        profile.federal_ordinary_rate = self.federal_ordinary_rate
        profile.federal_ltcg_rate = self.federal_ltcg_rate
        profile.state_ordinary_rate = self.state_ordinary_rate
        profile.state_cg_rate = self.state_cg_rate
        profile.use_bracket_engine = self.use_bracket_engine
        profile.use_state_engine = self.use_state_engine
        profile.other_ordinary_income = self.other_ordinary_income
        profile.ytd_wages = self.ytd_wages
        profile.other_long_term_gains = self.other_long_term_gains
        profile.other_short_term_gains = self.other_short_term_gains
        profile.include_fica = self.include_fica
        profile.ss_wage_base_maxed = self.ss_wage_base_maxed
        profile.include_niit = self.include_niit
        profile.amt_credit_carryforward = self.amt_credit_carryforward
        profile.ca_amt_credit_carryforward = self.ca_amt_credit_carryforward
        if hasattr(profile, 'federal_withholding_ytd'):
            profile.federal_withholding_ytd = getattr(self, 'federal_withholding_ytd', 0) or 0
            profile.state_withholding_ytd = getattr(self, 'state_withholding_ytd', 0) or 0
            profile.estimated_payments_ytd = getattr(self, 'estimated_payments_ytd', 0) or 0
        if hasattr(profile, 'itemize_salt'):
            profile.itemize_salt = getattr(self, 'itemize_salt', 0) or 0
            profile.itemize_mortgage = getattr(self, 'itemize_mortgage', 0) or 0
            profile.itemize_charity = getattr(self, 'itemize_charity', 0) or 0
