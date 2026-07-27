"""
User tax profile — inputs the engine does not assume.
"""

from app import db
from datetime import datetime


class TaxProfile(db.Model):
    """Per-user tax configuration for equity sale / AMT analysis."""

    __tablename__ = 'tax_profiles'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True, index=True)

    # Identity for brackets / AMT exemption
    filing_status = db.Column(db.String(20), default='single')  # single, mfj, mfs, hoh
    state_code = db.Column(db.String(2), nullable=True)

    # Rates the user confirms (we do not guess state law detail)
    federal_ordinary_rate = db.Column(db.Float, nullable=True)  # if null, estimate from brackets + income
    federal_ltcg_rate = db.Column(db.Float, nullable=True)  # if null, estimate from LTCG brackets
    state_ordinary_rate = db.Column(db.Float, default=0.0)
    state_cg_rate = db.Column(db.Float, default=0.0)  # flat fallback / non-CA
    use_bracket_engine = db.Column(db.Boolean, default=True)
    # When True and state_code has an engine (CA), use progressive state brackets
    use_state_engine = db.Column(db.Boolean, default=True)

    # Non-equity income for the tax year under analysis (required for AMT / NIIT / brackets)
    other_ordinary_income = db.Column(db.Float, default=0.0)  # wages, bonus cash, etc. excl. modeled equity
    other_long_term_gains = db.Column(db.Float, default=0.0)  # non-equity LTCG
    other_short_term_gains = db.Column(db.Float, default=0.0)

    # FICA / Medicare context
    include_fica = db.Column(db.Boolean, default=True)
    ytd_wages = db.Column(db.Float, default=0.0)
    ss_wage_base_maxed = db.Column(db.Boolean, default=False)

    # NIIT & AMT
    include_niit = db.Column(db.Boolean, default=True)
    amt_credit_carryforward = db.Column(db.Float, default=0.0)  # federal minimum tax credit
    ca_amt_credit_carryforward = db.Column(db.Float, default=0.0)  # CA Schedule P credit
    prior_year_amt_paid = db.Column(db.Float, default=0.0)

    # Analysis year default
    tax_year = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('tax_profile', uselist=False))

    def __repr__(self) -> str:
        return f'<TaxProfile user={self.user_id} {self.filing_status}>'

    @classmethod
    def for_user(cls, user):
        """Get or create profile seeded from legacy User tax fields."""
        profile = cls.query.filter_by(user_id=user.id).first()
        if profile:
            return profile
        profile = cls(
            user_id=user.id,
            state_code='CA',  # default for this product audience; user can change
            federal_ordinary_rate=user.federal_tax_rate if user.federal_tax_rate is not None else 0.24,
            state_ordinary_rate=user.state_tax_rate if user.state_tax_rate is not None else 0.0,
            state_cg_rate=user.state_tax_rate if user.state_tax_rate is not None else 0.0,
            include_fica=user.include_fica if user.include_fica is not None else True,
            ss_wage_base_maxed=bool(getattr(user, 'ss_wage_base_maxed', False)),
            use_bracket_engine=True,
            use_state_engine=True,
            tax_year=datetime.utcnow().year,
        )
        db.session.add(profile)
        db.session.commit()
        return profile

    def to_engine_dict(self) -> dict:
        return {
            'filing_status': self.filing_status or 'single',
            'state_code': (self.state_code or '').upper() or None,
            'federal_ordinary_rate': self.federal_ordinary_rate,
            'federal_ltcg_rate': self.federal_ltcg_rate,
            'state_ordinary_rate': self.state_ordinary_rate or 0.0,
            'state_cg_rate': self.state_cg_rate if self.state_cg_rate is not None else (self.state_ordinary_rate or 0.0),
            'use_bracket_engine': bool(self.use_bracket_engine),
            'use_state_engine': bool(self.use_state_engine if self.use_state_engine is not None else True),
            'other_ordinary_income': self.other_ordinary_income or 0.0,
            'other_long_term_gains': self.other_long_term_gains or 0.0,
            'other_short_term_gains': self.other_short_term_gains or 0.0,
            'include_fica': bool(self.include_fica),
            'ytd_wages': self.ytd_wages or 0.0,
            'ss_wage_base_maxed': bool(self.ss_wage_base_maxed),
            'include_niit': bool(self.include_niit),
            'amt_credit_carryforward': self.amt_credit_carryforward or 0.0,
            'ca_amt_credit_carryforward': self.ca_amt_credit_carryforward or 0.0,
            'tax_year': self.tax_year or datetime.utcnow().year,
        }
