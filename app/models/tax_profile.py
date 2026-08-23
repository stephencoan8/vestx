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

    # Estimated-tax / safe-harbor inputs (Sold tab calendar)
    prior_year_total_tax = db.Column(db.Float, default=0.0)  # prior-year total tax (safe harbor base)
    prior_year_agi = db.Column(db.Float, nullable=True)  # if >150k → 110% safe harbor
    federal_withholding_ytd = db.Column(db.Float, default=0.0)
    state_withholding_ytd = db.Column(db.Float, default=0.0)
    estimated_payments_ytd = db.Column(db.Float, default=0.0)  # estimated tax payments made YTD

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
            # Leave federal rates null so bracket engine is used (do not seed flat User rate)
            federal_ordinary_rate=None,
            federal_ltcg_rate=None,
            state_ordinary_rate=0.0,
            state_cg_rate=0.0,
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
        """Safe for partially-migrated DBs (missing columns → defaults)."""
        def g(name, default=None):
            try:
                return getattr(self, name, default)
            except Exception:
                return default

        state_ord = g('state_ordinary_rate', 0.0) or 0.0
        state_cg = g('state_cg_rate', None)
        if state_cg is None:
            state_cg = state_ord
        use_state = g('use_state_engine', True)
        other_ord = float(g('other_ordinary_income', 0.0) or 0.0)
        ytd = float(g('ytd_wages', 0.0) or 0.0)
        # Engine stacks max(other, ytd) so wages entered in either field count for brackets/LTCG
        stacked = max(other_ord, ytd)
        return {
            'filing_status': g('filing_status') or 'single',
            'state_code': ((g('state_code') or '') or '').upper() or None,
            'federal_ordinary_rate': g('federal_ordinary_rate'),
            'federal_ltcg_rate': g('federal_ltcg_rate'),
            'state_ordinary_rate': state_ord,
            'state_cg_rate': state_cg,
            'use_bracket_engine': bool(g('use_bracket_engine', True)),
            'use_state_engine': bool(use_state if use_state is not None else True),
            # Canonical stacking base (also keep raw fields for FICA / display)
            'other_ordinary_income': stacked,
            'other_ordinary_income_raw': other_ord,
            'other_long_term_gains': g('other_long_term_gains', 0.0) or 0.0,
            'other_short_term_gains': g('other_short_term_gains', 0.0) or 0.0,
            'include_fica': bool(g('include_fica', True)),
            'ytd_wages': ytd,
            'ss_wage_base_maxed': bool(g('ss_wage_base_maxed', False)),
            'include_niit': bool(g('include_niit', True)),
            'amt_credit_carryforward': g('amt_credit_carryforward', 0.0) or 0.0,
            'ca_amt_credit_carryforward': g('ca_amt_credit_carryforward', 0.0) or 0.0,
            'tax_year': g('tax_year') or datetime.utcnow().year,
            'stacking_ordinary_income': stacked,
        }
