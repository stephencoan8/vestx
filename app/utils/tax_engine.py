"""
Equity tax engine — explicit inputs only, no silent assumptions beyond
published federal bracket tables for the analysis year.

Supports:
- Ordinary income (incl. RSU vest income, ISO disqualifying ordinary)
- Short-term / long-term capital gains
- NIIT (3.8%) with MAGI thresholds by filing status
- State tax using user-provided rates
- AMT (ISO bargain element) vs regular tax
- ISO qualifying vs disqualifying disposition
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any, Dict, List, Optional, Tuple


# --- Federal tables (2025 approx; engine year param allows future updates) ---

ORDINARY_BRACKETS = {
    2023: {
        'single': [(0, 0.10), (11000, 0.12), (44725, 0.22), (95375, 0.24),
                   (182100, 0.32), (231250, 0.35), (578125, 0.37)],
        'mfj': [(0, 0.10), (22000, 0.12), (89450, 0.22), (190750, 0.24),
                (364200, 0.32), (462500, 0.35), (693750, 0.37)],
        'mfs': [(0, 0.10), (11000, 0.12), (44725, 0.22), (95375, 0.24),
                (182100, 0.32), (231250, 0.35), (346875, 0.37)],
        'hoh': [(0, 0.10), (15700, 0.12), (59850, 0.22), (95350, 0.24),
                (182100, 0.32), (231250, 0.35), (578100, 0.37)],
    },
    2024: {
        'single': [(0, 0.10), (11600, 0.12), (47150, 0.22), (100525, 0.24),
                   (191950, 0.32), (243725, 0.35), (609350, 0.37)],
        'mfj': [(0, 0.10), (23200, 0.12), (94300, 0.22), (201050, 0.24),
                (383900, 0.32), (487450, 0.35), (731200, 0.37)],
        'mfs': [(0, 0.10), (11600, 0.12), (47150, 0.22), (100525, 0.24),
                (191950, 0.32), (243725, 0.35), (365600, 0.37)],
        'hoh': [(0, 0.10), (16550, 0.12), (63100, 0.22), (100500, 0.24),
                (191950, 0.32), (243700, 0.35), (609350, 0.37)],
    },
    2025: {
        'single': [(0, 0.10), (11925, 0.12), (48475, 0.22), (103350, 0.24),
                   (197300, 0.32), (250525, 0.35), (626350, 0.37)],
        'mfj': [(0, 0.10), (23850, 0.12), (96950, 0.22), (206700, 0.24),
                (394600, 0.32), (501050, 0.35), (751600, 0.37)],
        'mfs': [(0, 0.10), (11925, 0.12), (48475, 0.22), (103350, 0.24),
                (197300, 0.32), (250525, 0.35), (375800, 0.37)],
        'hoh': [(0, 0.10), (17000, 0.12), (64850, 0.22), (103350, 0.24),
                (197300, 0.32), (250500, 0.35), (626350, 0.37)],
    },
    2026: {
        'single': [(0, 0.10), (12400, 0.12), (50400, 0.22), (105700, 0.24),
                   (201775, 0.32), (256225, 0.35), (640600, 0.37)],
        'mfj': [(0, 0.10), (24800, 0.12), (100800, 0.22), (211400, 0.24),
                (403550, 0.32), (512450, 0.35), (768700, 0.37)],
        'mfs': [(0, 0.10), (12400, 0.12), (50400, 0.22), (105700, 0.24),
                (201775, 0.32), (256225, 0.35), (384350, 0.37)],
        'hoh': [(0, 0.10), (17700, 0.12), (67500, 0.22), (105700, 0.24),
                (201750, 0.32), (256200, 0.35), (640600, 0.37)],
    },
}

# LTCG 0/15/20 breakpoints (taxable income) — IRS Rev. Proc. style
LTCG_BRACKETS = {
    2023: {
        'single': [(0, 0.0), (44625, 0.15), (492300, 0.20)],
        'mfj': [(0, 0.0), (89250, 0.15), (553850, 0.20)],
        'mfs': [(0, 0.0), (44625, 0.15), (276900, 0.20)],
        'hoh': [(0, 0.0), (59750, 0.15), (523050, 0.20)],
    },
    2024: {
        'single': [(0, 0.0), (47025, 0.15), (518900, 0.20)],
        'mfj': [(0, 0.0), (94050, 0.15), (583750, 0.20)],
        'mfs': [(0, 0.0), (47025, 0.15), (291850, 0.20)],
        'hoh': [(0, 0.0), (63000, 0.15), (551350, 0.20)],
    },
    2025: {
        'single': [(0, 0.0), (48350, 0.15), (533400, 0.20)],
        'mfj': [(0, 0.0), (96700, 0.15), (600050, 0.20)],
        'mfs': [(0, 0.0), (48350, 0.15), (300000, 0.20)],
        'hoh': [(0, 0.0), (64750, 0.15), (566700, 0.20)],
    },
    2026: {
        'single': [(0, 0.0), (49450, 0.15), (545500, 0.20)],
        'mfj': [(0, 0.0), (98900, 0.15), (613700, 0.20)],
        'mfs': [(0, 0.0), (49450, 0.15), (306850, 0.20)],
        'hoh': [(0, 0.0), (66300, 0.15), (579600, 0.20)],
    },
}

NIIT_THRESHOLD = {
    'single': 200000,
    'hoh': 200000,
    'mfs': 125000,
    'mfj': 250000,
}

# Federal AMT tables live in app.utils.amt (kept re-exports for older imports)
from app.utils.amt import (  # noqa: E402
    compute_federal_tmt,
    compute_ca_tmt,
    compute_amt_stack,
    FED_AMT_EXEMPTION as AMT_EXEMPTION,
    FED_AMT_PHASEOUT_START as AMT_PHASEOUT_START,
    FED_AMT_RATE_LOW as AMT_RATE_LOW,
    FED_AMT_RATE_HIGH as AMT_RATE_HIGH,
    FED_AMT_28_THRESHOLD as AMT_HIGH_THRESHOLD,
)

# FICA constants — single source of truth is app.utils.payroll_tax (IRS Pub 15)
from app.utils.payroll_tax import (  # noqa: E402
    SS_EMPLOYEE_RATE as SS_RATE,
    MEDICARE_EMPLOYEE_RATE as MEDICARE_RATE,
    ADDITIONAL_MEDICARE_RATE,
    SS_WAGE_BASE,
    ADD_MEDICARE_THRESHOLD,
    employee_fica,
    profile_ytd_before_equity,
)


def _year_table(table: dict, year: int) -> dict:
    if year in table:
        return table[year]
    # nearest available
    years = sorted(table.keys())
    if year < years[0]:
        return table[years[0]]
    return table[years[-1]]


def progressive_tax(income: float, brackets: List[Tuple[float, float]]) -> float:
    """brackets: list of (floor, rate) ascending."""
    if income <= 0:
        return 0.0
    tax = 0.0
    for i, (floor, rate) in enumerate(brackets):
        next_floor = brackets[i + 1][0] if i + 1 < len(brackets) else None
        if next_floor is None:
            tax += max(0.0, income - floor) * rate
            break
        if income > floor:
            segment = min(income, next_floor) - floor
            if segment > 0:
                tax += segment * rate
    return tax


def marginal_rate(income: float, brackets: List[Tuple[float, float]]) -> float:
    if income <= 0:
        return brackets[0][1]
    rate = brackets[0][1]
    for floor, r in brackets:
        if income >= floor:
            rate = r
    return rate


def ltcg_rate_for_income(taxable_income: float, filing: str, year: int) -> float:
    """Top preferential rate that applies at this taxable-income level (0/15/20)."""
    brackets = _year_table(LTCG_BRACKETS, year)[filing]
    rate = 0.0
    for floor, r in brackets:
        if taxable_income >= floor:
            rate = r
    return rate


def preferential_ltcg_tax(
    ltcg: float,
    ordinary_taxable: float,
    filing: str,
    year: int,
) -> Tuple[float, float]:
    """
    Preferential LTCG tax with correct bracket fill (not a single flat rate).

    LTCG stacks on top of ordinary taxable income and fills the 0% → 15% → 20%
    bands by total taxable income. Returns (tax, marginal_rate_on_last_dollar).
    """
    if ltcg <= 0:
        return 0.0, 0.0
    brackets = [(float(f), float(r)) for f, r in _year_table(LTCG_BRACKETS, year)[filing]]
    ordinary = max(0.0, float(ordinary_taxable or 0.0))
    end = ordinary + float(ltcg)
    pos = ordinary
    tax = 0.0
    last_rate = 0.0
    for i, (floor, rate) in enumerate(brackets):
        next_floor = brackets[i + 1][0] if i + 1 < len(brackets) else float('inf')
        seg_lo = max(pos, floor)
        seg_hi = min(end, next_floor)
        if seg_hi > seg_lo:
            tax += (seg_hi - seg_lo) * rate
            last_rate = rate
            pos = seg_hi
        if pos >= end - 1e-9:
            break
    if pos < end - 1e-9:
        top = brackets[-1][1]
        tax += (end - pos) * top
        last_rate = top
    return tax, last_rate


@dataclass
class LotSaleInput:
    """One proposed or recorded disposition of a tax lot (vest)."""
    vest_event_id: int
    grant_id: int
    share_type: str  # rsu, iso_5y, iso_6y, cash
    grant_type: str
    shares: float
    sale_price: float
    sale_date: date
    vest_date: date
    grant_date: date
    # Cost basis for capital gain (RSU: FMV at vest; ISO QD: strike; ISO DD: FMV at exercise often)
    cost_basis_per_share: float
    # ISO fields
    is_iso: bool = False
    strike_price: float = 0.0
    exercise_date: Optional[date] = None
    fmv_at_exercise: Optional[float] = None
    # ESPP §423
    espp_discount: float = 0.0
    fmv_at_grant: float = 0.0
    fmv_at_purchase: float = 0.0
    # Commission allocated to this lot
    commission: float = 0.0
    label: str = ''


@dataclass
class ExerciseInput:
    """
    ISO (or NSO) exercise as its own taxable-year event — distinct from sale.

    For ISOs: no regular federal income at exercise; AMT preference = bargain
    element (FMV − strike) × shares, unless shares are disqualified in the
    same calendar year (handled by pairing with LotSaleInput / planner).
    """
    vest_event_id: int
    shares: float
    exercise_date: date
    strike_price: float
    fmv_at_exercise: float
    grant_date: Optional[date] = None
    label: str = ''
    is_iso: bool = True

    @property
    def bargain_element(self) -> float:
        return max(0.0, self.fmv_at_exercise - self.strike_price) * self.shares

    @property
    def cash_outlay(self) -> float:
        return self.strike_price * self.shares


@dataclass
class LotSaleResult:
    vest_event_id: int
    label: str
    shares: float
    proceeds: float
    cost_basis: float
    capital_gain: float
    ordinary_income: float
    is_long_term: bool
    is_iso: bool
    iso_disposition: str  # n/a, qualifying, disqualifying
    holding_days: int
    amt_bargain_element: float  # recognized at exercise (for planning same-year exercise)
    notes: List[str] = field(default_factory=list)


@dataclass
class TaxAnalysis:
    tax_year: int
    filing_status: str
    lots: List[LotSaleResult]
    # Stacked income
    other_ordinary: float
    equity_ordinary: float
    stcg: float
    ltcg: float
    total_ordinary: float
    # Taxes
    federal_ordinary_tax: float
    federal_ltcg_tax: float
    federal_stcg_tax: float  # included in ordinary progressive for STCG
    niit: float
    state_tax: float
    state_regular_tax: float = 0.0
    state_surtax: float = 0.0  # e.g. CA MHST 1% over $1M
    state_engine: str = 'flat'
    state_taxable_income: float = 0.0
    fica_tax: float = 0.0
    regular_federal_tax: float = 0.0
    amt_tax: float = 0.0
    amt_due: float = 0.0  # max(0, amt - regular)
    federal_tax_total: float = 0.0
    total_tax: float = 0.0
    total_proceeds: float = 0.0
    total_cost_basis: float = 0.0
    after_tax_proceeds: float = 0.0
    effective_rate_on_gain: float = 0.0
    missing_inputs: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    rates_used: Dict[str, float] = field(default_factory=dict)
    state_breakdown: Dict[str, Any] = field(default_factory=dict)
    state_notes: List[str] = field(default_factory=list)
    # AMT credit rollforward (federal + CA)
    ca_amt_due: float = 0.0
    ca_amt_tmt: float = 0.0
    federal_amt_credit_opening: float = 0.0
    federal_amt_credit_used: float = 0.0
    federal_amt_credit_generated: float = 0.0
    federal_amt_credit_ending: float = 0.0
    ca_amt_credit_opening: float = 0.0
    ca_amt_credit_used: float = 0.0
    ca_amt_credit_generated: float = 0.0
    ca_amt_credit_ending: float = 0.0
    amt_detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['lots'] = [asdict(x) for x in self.lots]
        return d


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        # Feb 29 → Feb 28
        return d.replace(year=d.year + years, month=2, day=28)


def classify_iso_disposition(
    grant_date: date,
    exercise_date: Optional[date],
    sale_date: date,
) -> str:
    if exercise_date is None:
        return 'unknown'
    # IRC §422: hold ≥2 years from grant and ≥1 year from exercise
    if sale_date >= _add_years(grant_date, 2) and sale_date >= _add_years(exercise_date, 1):
        return 'qualifying'
    return 'disqualifying'


def earliest_qualifying_sale_date(grant_date: date, exercise_date: date) -> date:
    """First calendar date a sale can be a qualifying disposition."""
    return max(_add_years(grant_date, 2), _add_years(exercise_date, 1))


def _analyze_espp_lot(
    lot: LotSaleInput,
    proceeds: float,
    holding_days: int,
    is_lt: bool,
    notes: List[str],
) -> LotSaleResult:
    """§423 ESPP: QD vs DD. Lookback purchase price = (1 − discount) × min(grant FMV, purchase FMV)."""
    disc = min(0.5, max(0.0, float(lot.espp_discount or 0)))
    fmv_g = float(lot.fmv_at_grant or 0)
    fmv_p = float(lot.fmv_at_purchase or lot.cost_basis_per_share or 0)
    cands = [x for x in (fmv_g, fmv_p) if x > 0]
    lookback = min(cands) if cands else float(lot.cost_basis_per_share or 0)
    purchase_px = lookback * (1.0 - disc) if lookback > 0 else float(lot.cost_basis_per_share or 0)
    qd_on = max(_add_years(lot.grant_date, 2), _add_years(lot.vest_date, 1))
    is_qd = lot.sale_date >= qd_on
    purchase_basis = purchase_px * lot.shares
    actual_gain = proceeds - purchase_basis
    if is_qd:
        grant_bargain = max(0.0, (fmv_g or lookback) - purchase_px) * lot.shares
        ordinary = min(grant_bargain, max(0.0, actual_gain)) if actual_gain > 0 else 0.0
        capital_gain = actual_gain - ordinary
        notes.append(
            f'ESPP qualifying disposition (§423): {disc*100:.0f}% lookback discount as ordinary; '
            f'rest capital gain. Purchase ${purchase_px:.2f}/sh.'
        )
        disp = 'qualifying'
        from app.utils.tax_constants import ESPP_ANNUAL_LIMIT
        if fmv_g and lot.shares * fmv_g > ESPP_ANNUAL_LIMIT:
            notes.append(
                f'Offering FMV ${lot.shares * fmv_g:,.0f} exceeds ${ESPP_ANNUAL_LIMIT:,.0f} §423 annual limit — check offering cap.'
            )
    else:
        ordinary = max(0.0, fmv_p - purchase_px) * lot.shares
        capital_gain = proceeds - (purchase_basis + ordinary)
        notes.append(
            f'ESPP disqualifying: bargain at purchase as ordinary; residual capital gain. '
            f'QD window opens {qd_on.isoformat()}.'
        )
        disp = 'disqualifying'
    return LotSaleResult(
        vest_event_id=lot.vest_event_id,
        label=lot.label or 'ESPP',
        shares=lot.shares,
        proceeds=proceeds,
        cost_basis=purchase_basis + ordinary,
        capital_gain=capital_gain,
        ordinary_income=ordinary,
        is_long_term=is_lt if is_qd else is_lt,
        is_iso=False,
        iso_disposition=disp,
        holding_days=holding_days,
        amt_bargain_element=0.0,
        notes=notes,
    )


def analyze_lot(lot: LotSaleInput) -> LotSaleResult:
    notes: List[str] = []
    proceeds = lot.shares * lot.sale_price - lot.commission
    holding_days = (lot.sale_date - lot.vest_date).days
    is_lt = holding_days >= 365

    ordinary = 0.0
    capital_gain = 0.0
    cost_basis_total = lot.shares * lot.cost_basis_per_share
    iso_disp = 'n/a'
    amt_bargain = 0.0

    from app.utils.share_labels import is_espp_grant
    if is_espp_grant(lot.grant_type, lot.share_type) and lot.espp_discount > 0:
        return _analyze_espp_lot(lot, proceeds, holding_days, is_lt, notes)

    if lot.share_type == 'cash':
        notes.append('Cash awards are not capital assets; no CG model applied.')
        return LotSaleResult(
            vest_event_id=lot.vest_event_id,
            label=lot.label,
            shares=lot.shares,
            proceeds=proceeds,
            cost_basis=lot.shares,
            capital_gain=0.0,
            ordinary_income=0.0,
            is_long_term=False,
            is_iso=False,
            iso_disposition='n/a',
            holding_days=holding_days,
            amt_bargain_element=0.0,
            notes=notes,
        )

    if lot.is_iso:
        iso_disp = classify_iso_disposition(lot.grant_date, lot.exercise_date, lot.sale_date)
        strike = lot.strike_price
        fmv_ex = lot.fmv_at_exercise if lot.fmv_at_exercise is not None else lot.sale_price
        bargain_per = max(0.0, fmv_ex - strike)
        # Default: AMT preference is an *exercise-year* item. On a sale-only row it is
        # usually 0 unless the planner models exercise+hold via ExerciseInput.
        amt_bargain = 0.0

        if iso_disp == 'unknown':
            notes.append('ISO sale requires exercise_date to classify QD vs DD.')
            # Conservative: treat as DD at sale if no exercise date (same-day cashless common)
            iso_disp = 'disqualifying'
            notes.append('Assumed disqualifying until exercise_date is provided.')

        if iso_disp == 'disqualifying':
            # Ordinary income = min(sale, FMV at exercise) - strike, roughly bargain limited by sale
            bargain_for_regular = max(0.0, min(lot.sale_price, fmv_ex) - strike) * lot.shares
            ordinary = bargain_for_regular
            # Basis becomes strike + ordinary per share
            adj_basis = (strike * lot.shares) + ordinary
            capital_gain = proceeds - adj_basis
            cost_basis_total = adj_basis
            notes.append('ISO disqualifying disposition: bargain as ordinary; residual as capital gain.')
            # Same-year DD: bargain is already in regular tax → no ISO AMT preference
            if lot.exercise_date and lot.exercise_date.year == lot.sale_date.year:
                notes.append(
                    'Same-year exercise + DD: spread is ordinary income; no ISO AMT preference on these shares.'
                )
            else:
                notes.append(
                    'Prior-year exercise: any AMT was in the exercise year; sale year has ordinary/CG only. '
                    'AMT credit carryforward may apply (set in Tax Profile).'
                )
            # Holding period for residual CG runs from exercise
            if lot.exercise_date:
                holding_days = (lot.sale_date - lot.exercise_date).days
                is_lt = holding_days >= 365
        else:
            # Qualifying: entire appreciation over strike is LTCG (preferential)
            cost_basis_total = strike * lot.shares
            capital_gain = proceeds - cost_basis_total
            is_lt = True
            notes.append('ISO qualifying disposition: gain over strike as long-term capital gain (federal).')
            notes.append(
                'AMT was generally due in the exercise year on the bargain element; '
                'sale year is LTCG + possible AMT credit usage (Tax Profile).'
            )
            if lot.exercise_date:
                qd_ready = earliest_qualifying_sale_date(lot.grant_date, lot.exercise_date)
                notes.append(f'Earliest QD date for this lot was {qd_ready.isoformat()}.')
    else:
        # RSU / ESPP simplified: cost basis = FMV at vest (already taxed as ordinary at vest)
        capital_gain = proceeds - cost_basis_total
        notes.append('RSU/ESPP path: vest ordinary income is separate; sale is capital gain vs vest FMV basis.')

    return LotSaleResult(
        vest_event_id=lot.vest_event_id,
        label=lot.label,
        shares=lot.shares,
        proceeds=proceeds,
        cost_basis=cost_basis_total,
        capital_gain=capital_gain,
        ordinary_income=ordinary,
        is_long_term=is_lt,
        is_iso=lot.is_iso,
        iso_disposition=iso_disp,
        holding_days=holding_days,
        amt_bargain_element=amt_bargain if lot.is_iso else 0.0,
        notes=notes,
    )


def stacking_ordinary_income(profile: dict) -> float:
    """
    Ordinary income used for federal/CA brackets, LTCG bands, NIIT MAGI, and AMT base.

    Prefer computed_ordinary (cash wages + VestX vests). Legacy profiles still
    fall back to max(other_ordinary_income, ytd_wages).
    """
    if profile.get('computed_ordinary') is not None:
        try:
            return max(0.0, float(profile.get('computed_ordinary') or 0.0))
        except (TypeError, ValueError):
            pass
    other = float(profile.get('other_ordinary_income') or 0.0)
    ytd = float(profile.get('ytd_wages') or 0.0)
    return max(other, ytd)


def compute_fica_components(ordinary_equity: float, profile: dict) -> Dict[str, float]:
    """
    Incremental employee FICA on equity ordinary — delegates to payroll_tax.employee_fica.

    YTD before this equity is inferred from the year Tax Profile (peels equity
    out of full-year W-2 so SS remaining base is correct).
    """
    equity = max(0.0, float(ordinary_equity or 0.0))
    year = int(profile.get('tax_year') or date.today().year)
    filing = profile.get('filing_status') or 'single'
    if not profile.get('include_fica', True) or equity <= 0:
        from app.utils.payroll_tax import ss_wage_base_for_year
        return {
            'social_security': 0.0,
            'medicare': 0.0,
            'additional_medicare': 0.0,
            'total': 0.0,
            'ss_rate': 0.0,
            'medicare_rate': 0.0,
            'additional_medicare_rate': 0.0,
            'ss_wage_base': ss_wage_base_for_year(year),
            'ytd_for_fica': profile_ytd_before_equity(profile, 0.0),
        }

    # Sale/goal path: profile wages are non-equity base (do not peel equity).
    # Vest path calls employee_fica directly with an explicit YTD.
    ytd_before = profile_ytd_before_equity(profile, equity, wages_include_equity=False)
    from app.utils.payroll_tax import ss_wage_base_for_year
    wage_base = ss_wage_base_for_year(year)
    # Honor maxed only when YTD already clears the base, or empty YTD + user flag
    force_maxed = False
    if bool(profile.get('ss_wage_base_maxed')):
        if ytd_before >= wage_base - 1.0 or ytd_before <= 0:
            force_maxed = True

    r = employee_fica(
        period_wages=equity,
        ytd_wages_before=ytd_before,
        tax_year=year,
        filing_status=filing,
        ss_already_maxed=force_maxed,
    )
    return {
        'social_security': r.social_security,
        'medicare': r.medicare,
        'additional_medicare': r.additional_medicare,
        'total': r.total,
        'ss_rate': r.ss_effective_rate,
        'medicare_rate': r.medicare_rate,
        'additional_medicare_rate': r.additional_medicare_effective_rate,
        'ss_wage_base': r.ss_wage_base,
        'ytd_for_fica': r.ytd_wages_before,
        'ss_taxable_wages': r.ss_taxable_wages,
        'ss_remaining_before': r.ss_remaining_before,
        'notes': list(r.notes),
    }


def compute_fica(ordinary_equity: float, profile: dict) -> float:
    return float(compute_fica_components(ordinary_equity, profile)['total'])


def resolve_engine_profile_for_year(user, tax_year: int) -> dict:
    """
    Build tax-engine profile dict for a calendar year.

    Prefer TaxYearProfile for that year; else main TaxProfile money only if its
    active tax_year matches; else zero wages (never bleed another year's income).
    """
    from app.models.tax_profile import TaxProfile
    from app.models.tax_year_profile import TaxYearProfile

    year = int(tax_year)
    main = TaxProfile.for_user(user)
    year_row = TaxYearProfile.get_for(user.id, year)

    if year_row:
        d = {
            'filing_status': year_row.filing_status or 'single',
            'state_code': (year_row.state_code or 'CA').upper(),
            'federal_ordinary_rate': year_row.federal_ordinary_rate,
            'federal_ltcg_rate': year_row.federal_ltcg_rate,
            'state_ordinary_rate': float(year_row.state_ordinary_rate or 0.0),
            'state_cg_rate': float(
                year_row.state_cg_rate
                if year_row.state_cg_rate is not None
                else (year_row.state_ordinary_rate or 0.0)
            ),
            'use_bracket_engine': bool(
                year_row.use_bracket_engine if year_row.use_bracket_engine is not None else True
            ),
            'use_state_engine': bool(
                year_row.use_state_engine if year_row.use_state_engine is not None else True
            ),
            'other_ordinary_income': float(year_row.other_ordinary_income or 0.0),
            'ytd_wages': float(year_row.ytd_wages or 0.0),
            'other_long_term_gains': float(year_row.other_long_term_gains or 0.0),
            'other_short_term_gains': float(year_row.other_short_term_gains or 0.0),
            'include_fica': bool(year_row.include_fica if year_row.include_fica is not None else True),
            'ss_wage_base_maxed': bool(year_row.ss_wage_base_maxed),
            'include_niit': bool(year_row.include_niit if year_row.include_niit is not None else True),
            'amt_credit_carryforward': float(year_row.amt_credit_carryforward or 0.0),
            'ca_amt_credit_carryforward': float(year_row.ca_amt_credit_carryforward or 0.0),
            'tax_year': year,
            'profile_source': 'tax_year_profile',
        }
    else:
        d = main.to_engine_dict()
        d['tax_year'] = year
        d['profile_source'] = 'main_profile'
        if int(main.tax_year or 0) != year:
            # Past/future year with no saved row — don't use current-year wages
            d['other_ordinary_income'] = 0.0
            d['ytd_wages'] = 0.0
            d['other_long_term_gains'] = 0.0
            d['other_short_term_gains'] = 0.0
            d['ss_wage_base_maxed'] = False
            d['profile_source'] = 'defaults'
            # Keep filing/state/engines from main; clear flat rate overrides
            d['federal_ordinary_rate'] = None
            d['federal_ltcg_rate'] = None

    # Bracket engine on → progressive (ignore legacy flat User rate seed)
    if d.get('use_bracket_engine', True):
        d['federal_ordinary_rate'] = None
        d['federal_ltcg_rate'] = None

    cash = float(d.get('other_ordinary_income') or 0)
    d['other_ordinary_income_raw'] = cash
    d['stacking_ordinary_income'] = cash
    try:
        from app.utils.wage_year_tax import attach_computed_year_income
        attach_computed_year_income(d, user.id, year)
    except Exception:
        stacked = max(cash, float(d.get('ytd_wages') or 0))
        d['other_ordinary_income'] = stacked
        d['ytd_wages'] = stacked
        d['stacking_ordinary_income'] = stacked
    return d


def compute_vest_ordinary_tax(
    profile: dict,
    vest_ordinary: float,
    *,
    wages_include_this_vest: Optional[bool] = None,
    has_vested: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Incremental tax on RSU/cash vest ordinary income for a tax year.

    Stacks the vest on that year's Tax Profile wages (same engine as Sales & Tax).

    wages_include_this_vest:
      True  — profile wages are full W-2 box 1 already including this vest → peel it
      False — profile wages are salary/YTD without this vest → stack on top
      None  — infer: past calendar years / has_vested → peel; future vests → stack
    """
    vest = max(0.0, float(vest_ordinary or 0.0))
    year = int(profile.get('tax_year') or date.today().year)
    filing = profile.get('filing_status') or 'single'
    if filing not in ('single', 'mfj', 'mfs', 'hoh'):
        filing = 'single'

    stacked = stacking_ordinary_income(profile)
    if wages_include_this_vest is None:
        # Past years: Tax Profile W-2 is full-year (includes RSU). Future: usually not yet.
        if has_vested is True:
            wages_include_this_vest = stacked + 0.01 >= vest > 0
        elif has_vested is False:
            wages_include_this_vest = False
        else:
            wages_include_this_vest = (year < date.today().year) and (stacked + 0.01 >= vest > 0)

    if wages_include_this_vest and vest > 0 and stacked + 0.01 >= vest:
        base_ord = max(0.0, stacked - vest)
        wages_mode = 'w2_includes_vest'
    else:
        base_ord = stacked
        wages_mode = 'wages_plus_vest'

    # Profile slice BEFORE this vest (income tax stack only — FICA uses ytd=base_ord)
    base_profile = dict(profile)
    base_profile['tax_year'] = year
    base_profile['other_ordinary_income'] = base_ord
    base_profile['ytd_wages'] = base_ord

    # Pass gross ordinary — _federal_state_layer applies federal/CA standard deductions
    # on both base and full stacks so remaining std ded is absorbed correctly.
    from app.utils.wage_year_tax import FED_STD_DEDUCTION, CA_STD_DEDUCTION, _std_for
    fed_std = _std_for(FED_STD_DEDUCTION, year, filing)
    ca_std = _std_for(CA_STD_DEDUCTION, year, filing)

    base_layer = _federal_state_layer(
        base_profile,
        year=year,
        filing=filing,
        other_ord=base_ord,
        equity_ordinary=0.0,
        stcg_pos=0.0,
        ltcg_pos=0.0,
        amt_bargain=0.0,
    )
    full_layer = _federal_state_layer(
        base_profile,
        year=year,
        filing=filing,
        other_ord=base_ord,
        equity_ordinary=vest,
        stcg_pos=0.0,
        ltcg_pos=0.0,
        amt_bargain=0.0,
    )

    federal_tax = max(0.0, float(full_layer['federal_ordinary_tax']) - float(base_layer['federal_ordinary_tax']))
    federal_tax += max(0.0, float(full_layer['niit']) - float(base_layer['niit']))
    state_tax = max(0.0, float(full_layer['state_tax']) - float(base_layer['state_tax']))

    # FICA: YTD = wages before this vest (do NOT re-peel via compute_fica_components)
    include_fica = bool(profile.get('include_fica', True))
    if include_fica and vest > 0:
        from app.utils.payroll_tax import ss_wage_base_for_year
        wage_base = ss_wage_base_for_year(year)
        # Honor maxed flag only if YTD already at base, or flag set with unknown YTD
        force_maxed = False
        if bool(profile.get('ss_wage_base_maxed')):
            if base_ord >= wage_base - 1.0 or base_ord <= 0:
                force_maxed = True
        fica_r = employee_fica(
            period_wages=vest,
            ytd_wages_before=base_ord,
            tax_year=year,
            filing_status=filing,
            ss_already_maxed=force_maxed,
        )
        ss_tax = fica_r.social_security
        med_tax = fica_r.medicare
        add_med = fica_r.additional_medicare
        total_fica = fica_r.total
        ss_rate = fica_r.ss_effective_rate
        med_rate = fica_r.medicare_rate
        add_rate = fica_r.additional_medicare_effective_rate
        ss_base = fica_r.ss_wage_base
        fica_notes = list(fica_r.notes)
    else:
        ss_tax = med_tax = add_med = total_fica = 0.0
        ss_rate = med_rate = add_rate = 0.0
        from app.utils.payroll_tax import ss_wage_base_for_year
        ss_base = ss_wage_base_for_year(year)
        fica_notes = []

    total_tax = federal_tax + state_tax + total_fica
    eff = (total_tax / vest) if vest > 0 else 0.0
    fed_rate = (federal_tax / vest) if vest > 0 else 0.0
    state_rate = (state_tax / vest) if vest > 0 else 0.0

    notes = [
        f'Incremental tax on vest vs {year} Tax Profile ordinary base ${base_ord:,.0f} '
        f'({wages_mode}; year wages ${stacked:,.0f}).',
        f'Federal {"brackets" if profile.get("use_bracket_engine", True) else "flat"} · '
        f'state {"engine" if profile.get("use_state_engine", True) else "flat"} · tax year {year}.',
        f'FICA YTD before vest ${base_ord:,.0f} · SS base ${ss_base:,.0f} · '
        f'SS on this vest ${ss_tax:,.2f} (eff {ss_rate*100:.2f}%).',
    ]
    notes.extend(fica_notes[:2])
    if stacked <= 0:
        notes.append(
            f'No wages saved for {year} in Tax Profile — vest taxed from $0 stack '
            f'(enter {year} W-2 wages on Tax profile for accurate marginal rates).'
        )

    return {
        'tax_year': year,
        'gross_value': vest,
        'base_ordinary': base_ord,
        'year_wages': stacked,
        'wages_mode': wages_mode,
        'federal_tax': federal_tax,
        'state_tax': state_tax,
        'social_security_tax': ss_tax,
        'medicare_tax': med_tax,
        'additional_medicare_tax': add_med,
        'total_fica': total_fica,
        'total_tax': total_tax,
        'net_value': vest - total_tax,
        'federal_rate': fed_rate,
        'state_rate': state_rate,
        'social_security_rate': ss_rate,
        'medicare_rate': med_rate,
        'additional_medicare_rate': add_rate,
        'effective_rate': eff,
        'ordinary_marginal': float(full_layer.get('ord_marginal') or 0),
        'state_marginal': float(getattr(full_layer.get('state_result'), 'marginal_rate', 0) or 0),
        'federal_std_deduction': fed_std,
        'ca_std_deduction': ca_std,
        'ss_wage_base': ss_base,
        'include_fica': include_fica,
        'profile_source': profile.get('profile_source') or 'engine',
        'notes': notes,
    }


def compute_amt(
    amti_base: float,
    filing: str,
    year: int,
) -> float:
    """Tentative minimum tax on federal AMTI (wrapper for app.utils.amt)."""
    tmt, _ = compute_federal_tmt(amti_base, filing, year)
    return tmt


def _net_st_lt(net_st: float, net_lt: float) -> Tuple[float, float]:
    """Simplified ST/LT loss netting → positive ST and LT for tax."""
    if net_st < 0 and net_lt > 0:
        offset = min(net_lt, -net_st)
        net_lt -= offset
        net_st += offset
    elif net_lt < 0 and net_st > 0:
        offset = min(net_st, -net_lt)
        net_st -= offset
        net_lt += offset
    return max(0.0, net_st), max(0.0, net_lt)


def _federal_state_layer(
    profile: dict,
    *,
    year: int,
    filing: str,
    other_ord: float,
    equity_ordinary: float,
    stcg_pos: float,
    ltcg_pos: float,
    amt_bargain: float,
) -> Dict[str, Any]:
    """
    Compute federal + state tax for one income stack (full dollars, not incremental).
    FICA is omitted here — it is equity-only and applied once on the delta path.

    Inputs are **gross** dollars (pre standard deduction). Federal progressive tax
    and preferential LTCG stack on taxable ordinary after the federal standard
    deduction; CA PIT uses the CA standard deduction (when state is CA). AMTI is
    built from gross income + ISO bargain (no std ded). Regular tax compared to
    TMT is the post-std-ded regular federal/CA tax — Form 6251-style.
    """
    from app.utils.state_tax import compute_state_tax
    from app.utils.wage_year_tax import FED_STD_DEDUCTION, CA_STD_DEDUCTION, _std_for

    other_ord = max(0.0, float(other_ord or 0.0))
    equity_ordinary = max(0.0, float(equity_ordinary or 0.0))
    stcg_pos = max(0.0, float(stcg_pos or 0.0))
    ltcg_pos = max(0.0, float(ltcg_pos or 0.0))
    amt_bargain = max(0.0, float(amt_bargain or 0.0))

    # Gross stacks (pre standard deduction) — NIIT / AMTI / reporting
    wages_equity = other_ord + equity_ordinary
    total_ordinary = wages_equity + stcg_pos  # STCG is ordinary character
    fed_std = _std_for(FED_STD_DEDUCTION, year, filing)
    ca_std = _std_for(CA_STD_DEDUCTION, year, filing)

    # Federal taxable ordinary after standard deduction
    taxable_ordinary = max(0.0, total_ordinary - fed_std)

    ordinary_brackets = _year_table(ORDINARY_BRACKETS, year)[filing]
    # Bracket engine wins when enabled — legacy User.federal_tax_rate often seeds
    # federal_ordinary_rate (e.g. 35%) and must not force a flat rate on top.
    use_brackets = bool(profile.get('use_bracket_engine', True))

    if use_brackets:
        federal_ordinary_tax = progressive_tax(taxable_ordinary, ordinary_brackets)
        ord_marginal = marginal_rate(taxable_ordinary, ordinary_brackets)
    else:
        ord_rate = float(profile.get('federal_ordinary_rate') or 0.24)
        federal_ordinary_tax = taxable_ordinary * ord_rate
        ord_marginal = ord_rate

    if profile.get('federal_ltcg_rate') is not None:
        # Manual override: flat rate on all LTCG (power-user / stress test)
        ltcg_rate = float(profile['federal_ltcg_rate'])
        federal_ltcg_tax = ltcg_pos * ltcg_rate
    else:
        # Stack LTCG on *taxable* ordinary (after std ded) — same as W-2 full-year path
        federal_ltcg_tax, ltcg_rate = preferential_ltcg_tax(
            ltcg_pos, taxable_ordinary, filing, year
        )
    federal_stcg_tax = stcg_pos * ord_marginal  # attribution only; STCG already in ordinary tax

    # Regular tax for AMT comparison = post-std-ded ordinary + preferential LTCG (not NIIT)
    regular_federal = federal_ordinary_tax + federal_ltcg_tax

    niit = 0.0
    if profile.get('include_niit', True):
        # NIIT uses MAGI-ish base (pre standard deduction)
        magi = total_ordinary + ltcg_pos
        thr = NIIT_THRESHOLD.get(filing, 200000)
        investment = stcg_pos + ltcg_pos
        niit_base = min(investment, max(0.0, magi - thr))
        niit = niit_base * 0.038

    state_ord_rate = float(profile.get('state_ordinary_rate') or 0.0)
    state_cg_rate = float(
        profile.get('state_cg_rate') if profile.get('state_cg_rate') is not None else state_ord_rate
    )
    state_code = (profile.get('state_code') or '').upper()
    # CA: apply CA standard deduction to ordinary first; leftover reduces gains (rare)
    if state_code == 'CA':
        ca_taxable_ordinary = max(0.0, wages_equity - ca_std)
        leftover_ca_std = max(0.0, ca_std - wages_equity)
        ca_gains_taxable = max(0.0, (stcg_pos + ltcg_pos) - leftover_ca_std)
    else:
        ca_taxable_ordinary = wages_equity
        ca_gains_taxable = stcg_pos + ltcg_pos

    state_result = compute_state_tax(
        state_code=profile.get('state_code'),
        filing_status=filing,
        tax_year=year,
        ordinary_income=ca_taxable_ordinary,
        capital_gains=ca_gains_taxable,
        use_state_engine=bool(profile.get('use_state_engine', True)),
        state_ordinary_rate=state_ord_rate,
        state_cg_rate=state_cg_rate,
    )

    # AMTI ≈ gross ordinary + CG + ISO bargain (no std ded; exemption applied in TMT)
    amti_base = total_ordinary + ltcg_pos
    amt_stack = compute_amt_stack(
        filing=filing,
        year=year,
        federal_regular_tax=regular_federal,
        ca_regular_tax=float(state_result.regular_tax or 0.0),
        ordinary_and_cg_base=amti_base,
        iso_bargain_preference=amt_bargain,
        federal_credit_opening=float(profile.get('amt_credit_carryforward') or 0.0),
        ca_credit_opening=float(profile.get('ca_amt_credit_carryforward') or 0.0),
        state_code=profile.get('state_code'),
        compute_ca=bool(profile.get('use_state_engine', True)),
        long_term_gains=ltcg_pos,
    )
    fed_amt = amt_stack.federal
    amt_tax = fed_amt.tentative_minimum_tax
    amt_due = fed_amt.amt_due
    credit_used = fed_amt.credit_used
    federal_after_credit = regular_federal - credit_used + amt_due + niit

    # State tax = regular PIT/MHST + CA AMT due − CA AMT credit used
    ca_amt_due = amt_stack.ca_amt_due
    ca_credit_used = amt_stack.california.credit_used if amt_stack.california else 0.0
    state_tax_total = (
        float(state_result.total_tax)
        + ca_amt_due
        - ca_credit_used
    )
    # Credit can't reduce state below 0
    state_tax_total = max(0.0, state_tax_total)

    return {
        'total_ordinary': total_ordinary,
        'taxable_ordinary': taxable_ordinary,
        'federal_std_deduction': fed_std,
        'ca_std_deduction': ca_std if state_code == 'CA' else 0.0,
        'federal_ordinary_tax': federal_ordinary_tax,
        'federal_ltcg_tax': federal_ltcg_tax,
        'federal_stcg_tax': federal_stcg_tax,
        'regular_federal_tax': regular_federal,
        'niit': niit,
        'amt_tax': amt_tax,
        'amt_due': amt_due,
        'federal_tax_total': federal_after_credit,
        'state_tax': state_tax_total,
        'state_regular_tax': state_result.regular_tax,
        'state_surtax': state_result.surtax,
        'state_engine': state_result.engine,
        'state_taxable_income': state_result.taxable_income,
        'state_result': state_result,
        'ord_marginal': ord_marginal,
        'ltcg_rate': ltcg_rate,
        'state_ord_rate': state_ord_rate,
        'state_cg_rate': state_cg_rate,
        'ca_amt_due': ca_amt_due,
        'ca_amt_tmt': (
            amt_stack.california.tentative_minimum_tax if amt_stack.california else 0.0
        ),
        'federal_amt_credit_opening': fed_amt.credit_opening,
        'federal_amt_credit_used': fed_amt.credit_used,
        'federal_amt_credit_generated': fed_amt.credit_generated,
        'federal_amt_credit_ending': fed_amt.credit_ending,
        'ca_amt_credit_opening': (
            amt_stack.california.credit_opening if amt_stack.california else 0.0
        ),
        'ca_amt_credit_used': ca_credit_used,
        'ca_amt_credit_generated': (
            amt_stack.california.credit_generated if amt_stack.california else 0.0
        ),
        'ca_amt_credit_ending': (
            amt_stack.california.credit_ending if amt_stack.california else 0.0
        ),
        'amt_detail': amt_stack.to_dict(),
        'amt_bargain': amt_bargain,
    }


def analyze_sales(
    profile: dict,
    lots: List[LotSaleInput],
    *,
    include_exercise_amt: bool = True,
    exercises: Optional[List[ExerciseInput]] = None,
) -> TaxAnalysis:
    """
    Stacked analysis for lot sales and/or ISO exercises in profile['tax_year'].

    Reported federal/state/NIIT/AMT amounts are **incremental**: tax with the
    proposed equity events minus tax on profile income alone (wages + other CG).
    That is the right base for "effective rate on gain" and after-tax proceeds.

    exercises: ISO exercise events in this tax year that still create AMT preference
    (typically exercise-and-hold; exclude shares that are same-year DD sales).
    """
    year = int(profile.get('tax_year') or date.today().year)
    filing = profile.get('filing_status') or 'single'
    if filing not in ('single', 'mfj', 'mfs', 'hoh'):
        filing = 'single'

    missing: List[str] = []
    warnings: List[str] = []
    exercises = exercises or []

    if profile.get('other_ordinary_income') is None:
        missing.append('other_ordinary_income')
    if profile.get('state_ordinary_rate') is None and profile.get('state_cg_rate') is None:
        missing.append('state tax rates')

    # Whole shares only — normalize lots before analysis
    from app.utils.shares import whole_shares
    norm_lots: List[LotSaleInput] = []
    for lot in lots:
        sh = whole_shares(lot.shares)
        if sh <= 0:
            continue
        if sh != lot.shares:
            from dataclasses import replace
            lot = replace(lot, shares=float(sh))
        norm_lots.append(lot)
    lots = norm_lots

    lot_results = [analyze_lot(lot) for lot in lots]

    equity_ordinary = sum(r.ordinary_income for r in lot_results)
    # Equity lot capital gains only (exclude profile "other" CG until netting)
    eq_st = sum(r.capital_gain for r in lot_results if not r.is_long_term)
    eq_lt = sum(r.capital_gain for r in lot_results if r.is_long_term)

    other_ord = stacking_ordinary_income(profile)
    other_ord_field = float(profile.get('other_ordinary_income') or 0.0)
    ytd_field = float(profile.get('ytd_wages') or 0.0)
    other_lt = float(profile.get('other_long_term_gains') or 0.0)
    other_st = float(profile.get('other_short_term_gains') or 0.0)

    if profile.get('computed_ordinary') is not None:
        pass
    elif ytd_field > other_ord_field + 1.0 and other_ord_field > 0:
        warnings.append(
            f'Using ${other_ord:,.0f} ordinary for brackets (max of other ordinary '
            f'${other_ord_field:,.0f} and YTD wages ${ytd_field:,.0f}).'
        )
    elif ytd_field > other_ord_field + 1.0 and other_ord_field <= 0:
        warnings.append(
            f'YTD wages ${ytd_field:,.0f} used as ordinary stacking base '
            f'(other ordinary income was $0).'
        )

    base_st, base_lt = _net_st_lt(other_st, other_lt)
    full_st, full_lt = _net_st_lt(eq_st + other_st, eq_lt + other_lt)
    # Display ST/LT = equity lots only (netted)
    stcg_pos, ltcg_pos = _net_st_lt(eq_st, eq_lt)

    # AMT bargain: from sale rows (legacy) + standalone exercises in this year
    amt_bargain = 0.0
    if include_exercise_amt:
        amt_bargain += sum(r.amt_bargain_element for r in lot_results)
        for ex in exercises:
            if ex.exercise_date.year != year:
                continue
            if not ex.is_iso:
                continue
            amt_bargain += ex.bargain_element
            warnings.append(
                f'ISO exercise AMT preference: ${ex.bargain_element:,.0f} bargain on '
                f'{ex.shares:g} sh @ FMV ${ex.fmv_at_exercise:.2f} − strike ${ex.strike_price:.2f} '
                f'({ex.label or "exercise"}).'
            )

    base = _federal_state_layer(
        profile,
        year=year,
        filing=filing,
        other_ord=other_ord,
        equity_ordinary=0.0,
        stcg_pos=base_st,
        ltcg_pos=base_lt,
        amt_bargain=0.0,
    )
    full = _federal_state_layer(
        profile,
        year=year,
        filing=filing,
        other_ord=other_ord,
        equity_ordinary=equity_ordinary,
        stcg_pos=full_st,
        ltcg_pos=full_lt,
        amt_bargain=amt_bargain,
    )

    def _delta(key: str) -> float:
        return max(0.0, float(full[key]) - float(base[key]))

    # Incremental taxes caused by this equity activity
    federal_ordinary_tax = _delta('federal_ordinary_tax')
    federal_ltcg_tax = _delta('federal_ltcg_tax')
    federal_stcg_tax = _delta('federal_stcg_tax')
    niit = _delta('niit')
    state_tax = _delta('state_tax')
    state_regular_tax = _delta('state_regular_tax')
    state_surtax = _delta('state_surtax')
    regular_federal = _delta('regular_federal_tax')
    amt_due = _delta('amt_due')
    # AMT TMT on full stack (informational); due is incremental
    amt_tax = float(full['amt_tax'])
    federal_after_credit = _delta('federal_tax_total')

    # FICA only on equity ordinary — already incremental
    fica = compute_fica(equity_ordinary, profile)

    state_result = full['state_result']
    for n in state_result.notes:
        if 'No full bracket engine' in n or 'No state selected' in n:
            warnings.append(n)

    total_proceeds = sum(r.proceeds for r in lot_results)
    total_basis = sum(r.cost_basis for r in lot_results)
    total_tax = federal_after_credit + state_tax + fica
    # Economic gain from equity events (CG + DD ordinary; RSU vest ordinary is separate)
    gain = (total_proceeds - total_basis) + equity_ordinary
    # Exercise-and-hold: no sale gain — rate tax against AMT bargain so the metric is usable
    if gain <= 0 and amt_bargain > 0:
        gain = amt_bargain
        warnings.append(
            'No sale proceeds in this year; effective rate uses ISO bargain element (AMT preference) as the base.'
        )
    if gain <= 0:
        eff = 0.0
    else:
        eff = total_tax / gain

    if any(r.iso_disposition == 'unknown' or 'Assumed disqualifying' in ' '.join(r.notes) for r in lot_results):
        warnings.append('One or more ISO lots lack exercise_date; disposition classification may be incomplete.')
    if other_ord == 0 and (full_lt + full_st + equity_ordinary) > 0:
        warnings.append(
            'No ordinary wages in Tax Profile (other ordinary and YTD wages are $0) — '
            'brackets, NIIT, LTCG band, and AMT will be wrong. Enter full-year wages.'
        )
    warnings.append(
        'Tax amounts are incremental: extra tax from this equity activity vs your profile income alone '
        f'(stacked ordinary ${other_ord:,.0f} + other CG). Effective rate = incremental tax ÷ equity gain.'
    )
    # Surface which LTCG band the sale lands in (helps catch 15% vs 20% confusion)
    if ltcg_pos > 0:
        try:
            floor_20 = float(_year_table(LTCG_BRACKETS, year)[filing][2][0])
        except Exception:
            floor_20 = 545500.0
        # Preferential LTCG stacks on taxable ordinary (after federal std ded)
        from app.utils.wage_year_tax import FED_STD_DEDUCTION, _std_for as _std_for_warn
        _fed_std_w = _std_for_warn(FED_STD_DEDUCTION, year, filing)
        ordinary_for_ltcg = max(
            0.0, other_ord + equity_ordinary + full_st - _fed_std_w
        )
        room_in_15 = max(0.0, floor_20 - ordinary_for_ltcg)
        in_15 = min(ltcg_pos, room_in_15) if ordinary_for_ltcg < floor_20 else 0.0
        in_20 = max(0.0, ltcg_pos - in_15)
        ti_top = ordinary_for_ltcg + ltcg_pos
        if in_20 > 1 and in_15 > 1:
            warnings.append(
                f'Federal LTCG split: ${in_15:,.0f} @ 15% + ${in_20:,.0f} @ 20% '
                f'(taxable ordinary base ${ordinary_for_ltcg:,.0f}; 20% starts at ${floor_20:,.0f} TI).'
            )
        elif in_20 > 1:
            warnings.append(
                f'Federal LTCG all @ 20% (TI through gains ≈ ${ti_top:,.0f}; '
                f'taxable ordinary base ${ordinary_for_ltcg:,.0f}).'
            )
        else:
            warnings.append(
                f'Federal LTCG @ 15% (taxable ordinary base ${ordinary_for_ltcg:,.0f}; '
                f'only ${room_in_15:,.0f} of room left before 20% band at ${floor_20:,.0f}).'
            )

    # State detail: show incremental PIT/MHST but keep full-stack taxable income for context
    state_breakdown = dict(state_result.breakdown or {})
    state_breakdown['incremental_state_tax'] = state_tax
    state_breakdown['baseline_state_tax'] = float(base['state_tax'])
    state_breakdown['full_stack_state_tax'] = float(full['state_tax'])
    state_breakdown['ca_amt_due_incremental'] = _delta('ca_amt_due')
    state_notes = list(state_result.notes) + [
        f'Incremental state tax on this activity: ${state_tax:,.2f} '
        f'(full-year CA/stack ${float(full["state_tax"]):,.2f} − baseline ${float(base["state_tax"]):,.2f}).',
    ]
    # Surface AMT notes from full stack
    amt_detail = full.get('amt_detail') or {}
    for n in (amt_detail.get('notes') or []):
        if n not in warnings:
            warnings.append(n)

    ca_amt_due_inc = _delta('ca_amt_due')

    return TaxAnalysis(
        tax_year=year,
        filing_status=filing,
        lots=lot_results,
        other_ordinary=other_ord,
        equity_ordinary=equity_ordinary,
        stcg=stcg_pos,
        ltcg=ltcg_pos,
        total_ordinary=float(full['total_ordinary']),
        federal_ordinary_tax=federal_ordinary_tax,
        federal_ltcg_tax=federal_ltcg_tax,
        federal_stcg_tax=federal_stcg_tax,
        niit=niit,
        state_tax=state_tax,
        state_regular_tax=state_regular_tax,
        state_surtax=state_surtax,
        state_engine=str(full['state_engine']),
        # Full-stack CA taxable income (wages + equity) — useful for MHST proximity
        state_taxable_income=float(full['state_taxable_income']),
        fica_tax=fica,
        regular_federal_tax=regular_federal,
        amt_tax=amt_tax,
        amt_due=amt_due,
        federal_tax_total=federal_after_credit,
        total_tax=total_tax,
        total_proceeds=total_proceeds,
        total_cost_basis=total_basis,
        after_tax_proceeds=total_proceeds - total_tax,
        effective_rate_on_gain=eff,
        missing_inputs=missing,
        warnings=warnings,
        rates_used={
            'ordinary_marginal': float(full['ord_marginal']),
            'ltcg': float(full['ltcg_rate']),
            'state_ordinary': float(full['state_ord_rate']),
            'state_cg': float(full['state_cg_rate']),
            'state_marginal': state_result.marginal_rate,
            'state_effective': (state_tax / (stcg_pos + ltcg_pos + equity_ordinary))
            if (stcg_pos + ltcg_pos + equity_ordinary) > 0
            else 0.0,
            'niit': 0.038 if profile.get('include_niit', True) else 0.0,
            'baseline_federal': float(base['federal_tax_total']),
            'full_federal': float(full['federal_tax_total']),
            'baseline_state': float(base['state_tax']),
            'full_state': float(full['state_tax']),
        },
        state_breakdown=state_breakdown,
        state_notes=state_notes,
        # Credit positions from the *with-activity* stack (for multi-year handoff)
        ca_amt_due=ca_amt_due_inc,
        ca_amt_tmt=float(full.get('ca_amt_tmt') or 0.0),
        federal_amt_credit_opening=float(full.get('federal_amt_credit_opening') or 0.0),
        federal_amt_credit_used=float(full.get('federal_amt_credit_used') or 0.0),
        federal_amt_credit_generated=float(full.get('federal_amt_credit_generated') or 0.0),
        federal_amt_credit_ending=float(full.get('federal_amt_credit_ending') or 0.0),
        ca_amt_credit_opening=float(full.get('ca_amt_credit_opening') or 0.0),
        ca_amt_credit_used=float(full.get('ca_amt_credit_used') or 0.0),
        ca_amt_credit_generated=float(full.get('ca_amt_credit_generated') or 0.0),
        ca_amt_credit_ending=float(full.get('ca_amt_credit_ending') or 0.0),
        amt_detail=amt_detail,
    )
