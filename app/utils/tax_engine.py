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

# LTCG 0/15/20 breakpoints (taxable income)
LTCG_BRACKETS = {
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

# AMT exemption (approx 2025/2026)
AMT_EXEMPTION = {
    2025: {'single': 88100, 'mfs': 44050, 'mfj': 137000, 'hoh': 88100},
    2026: {'single': 90100, 'mfs': 45050, 'mfj': 140200, 'hoh': 90100},
}
AMT_PHASEOUT_START = {
    2025: {'single': 626350, 'mfs': 313175, 'mfj': 1_268_500, 'hoh': 626350},
    2026: {'single': 640600, 'mfs': 320300, 'mfj': 1_281_200, 'hoh': 640600},
}
AMT_RATE_LOW = 0.26
AMT_RATE_HIGH = 0.28
AMT_HIGH_THRESHOLD = {
    'single': 220700,
    'mfs': 110350,
    'mfj': 220700,
    'hoh': 220700,
}

SS_RATE = 0.062
MEDICARE_RATE = 0.0145
ADDITIONAL_MEDICARE_RATE = 0.009
SS_WAGE_BASE = {2025: 176100, 2026: 184500}
ADD_MEDICARE_THRESHOLD = {'single': 200000, 'hoh': 200000, 'mfs': 125000, 'mfj': 250000}


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
    brackets = _year_table(LTCG_BRACKETS, year)[filing]
    rate = 0.0
    for floor, r in brackets:
        if taxable_income >= floor:
            rate = r
    return rate


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
    # Commission allocated to this lot
    commission: float = 0.0
    label: str = ''


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
    fica_tax: float
    regular_federal_tax: float
    amt_tax: float
    amt_due: float  # max(0, amt - regular)
    federal_tax_total: float
    total_tax: float
    total_proceeds: float
    total_cost_basis: float
    after_tax_proceeds: float
    effective_rate_on_gain: float
    missing_inputs: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    rates_used: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['lots'] = [asdict(x) for x in self.lots]
        return d


def classify_iso_disposition(
    grant_date: date,
    exercise_date: Optional[date],
    sale_date: date,
) -> str:
    if exercise_date is None:
        return 'unknown'
    years_from_grant = (sale_date - grant_date).days / 365.25
    years_from_exercise = (sale_date - exercise_date).days / 365.25
    if years_from_grant >= 2 and years_from_exercise >= 1:
        return 'qualifying'
    return 'disqualifying'


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
        amt_bargain = bargain_per * lot.shares

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
            # Holding period for residual CG runs from exercise
            if lot.exercise_date:
                holding_days = (lot.sale_date - lot.exercise_date).days
                is_lt = holding_days >= 365
        else:
            # Qualifying: entire appreciation over strike is LTCG (preferential); AMT paid at exercise
            cost_basis_total = strike * lot.shares
            capital_gain = proceeds - cost_basis_total
            is_lt = True
            notes.append('ISO qualifying disposition: gain over strike as long-term capital gain.')
            # AMT bargain was at exercise (may be prior year)
            if lot.exercise_date and lot.exercise_date.year == lot.sale_date.year:
                notes.append('Exercise and sale in same year — AMT bargain included in this analysis year.')
            else:
                amt_bargain = 0.0  # prior-year exercise AMT already settled
                notes.append('Exercise in a prior year — AMT bargain not re-included; set AMT credit if applicable.')
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


def compute_fica(ordinary_equity: float, profile: dict) -> float:
    if not profile.get('include_fica', True):
        return 0.0
    year = profile.get('tax_year', 2026)
    wage_base = SS_WAGE_BASE.get(year, 184500)
    ytd = profile.get('ytd_wages', 0.0) or 0.0
    ss = 0.0
    if not profile.get('ss_wage_base_maxed'):
        remaining = max(0.0, wage_base - ytd)
        ss = min(ordinary_equity, remaining) * SS_RATE
    medicare = ordinary_equity * MEDICARE_RATE
    filing = profile.get('filing_status', 'single')
    thr = ADD_MEDICARE_THRESHOLD.get(filing, 200000)
    add_base = max(0.0, (ytd + ordinary_equity) - thr)
    # Additional Medicare only on wages over threshold — approximate on equity portion
    add_med = min(ordinary_equity, add_base) * ADDITIONAL_MEDICARE_RATE if add_base > 0 else 0.0
    return ss + medicare + add_med


def compute_amt(
    amti_base: float,
    filing: str,
    year: int,
) -> float:
    """Tentative minimum tax on AMTI."""
    if amti_base <= 0:
        return 0.0
    exempt = _year_table(AMT_EXEMPTION, year).get(filing, 90100)
    phase_start = _year_table(AMT_PHASEOUT_START, year).get(filing, 640600)
    # Phase out exemption 25 cents per dollar over start
    if amti_base > phase_start:
        exempt = max(0.0, exempt - 0.25 * (amti_base - phase_start))
    taxable_amt = max(0.0, amti_base - exempt)
    high_thr = AMT_HIGH_THRESHOLD.get(filing, 220700)
    if taxable_amt <= high_thr:
        return taxable_amt * AMT_RATE_LOW
    return high_thr * AMT_RATE_LOW + (taxable_amt - high_thr) * AMT_RATE_HIGH


def analyze_sales(
    profile: dict,
    lots: List[LotSaleInput],
    *,
    include_exercise_amt: bool = True,
) -> TaxAnalysis:
    """
    Full stacked analysis for a set of lot sales in profile['tax_year'].
    """
    year = int(profile.get('tax_year') or date.today().year)
    filing = profile.get('filing_status') or 'single'
    if filing not in ('single', 'mfj', 'mfs', 'hoh'):
        filing = 'single'

    missing: List[str] = []
    warnings: List[str] = []

    if profile.get('other_ordinary_income') is None:
        missing.append('other_ordinary_income')
    if profile.get('state_ordinary_rate') is None and profile.get('state_cg_rate') is None:
        missing.append('state tax rates')

    lot_results = [analyze_lot(lot) for lot in lots]

    equity_ordinary = sum(r.ordinary_income for r in lot_results)
    stcg = sum(r.capital_gain for r in lot_results if not r.is_long_term and r.capital_gain)
    ltcg = sum(r.capital_gain for r in lot_results if r.is_long_term and r.capital_gain)
    # losses
    st_loss = sum(r.capital_gain for r in lot_results if not r.is_long_term and r.capital_gain < 0)
    lt_loss = sum(r.capital_gain for r in lot_results if r.is_long_term and r.capital_gain < 0)
    stcg = max(0.0, stcg)  # net later
    # Net ST and LT
    net_st = sum(r.capital_gain for r in lot_results if not r.is_long_term)
    net_lt = sum(r.capital_gain for r in lot_results if r.is_long_term)

    other_ord = float(profile.get('other_ordinary_income') or 0.0)
    other_lt = float(profile.get('other_long_term_gains') or 0.0)
    other_st = float(profile.get('other_short_term_gains') or 0.0)

    net_st += other_st
    net_lt += other_lt

    # Netting: ST and LT nets, then combined if opposite signs (simplified)
    if net_st < 0 and net_lt > 0:
        offset = min(net_lt, -net_st)
        net_lt -= offset
        net_st += offset
    elif net_lt < 0 and net_st > 0:
        offset = min(net_st, -net_lt)
        net_st -= offset
        net_lt += offset

    stcg_pos = max(0.0, net_st)
    ltcg_pos = max(0.0, net_lt)

    # Ordinary stack: other + equity ordinary + STCG (taxed as ordinary)
    total_ordinary = other_ord + equity_ordinary + stcg_pos

    ordinary_brackets = _year_table(ORDINARY_BRACKETS, year)[filing]
    use_brackets = profile.get('use_bracket_engine', True)

    if use_brackets and profile.get('federal_ordinary_rate') is None:
        federal_ordinary_tax = progressive_tax(total_ordinary, ordinary_brackets)
        ord_marginal = marginal_rate(total_ordinary, ordinary_brackets)
    else:
        ord_rate = float(profile.get('federal_ordinary_rate') or 0.24)
        federal_ordinary_tax = total_ordinary * ord_rate
        ord_marginal = ord_rate

    # LTCG tax: use override or bracket based on ordinary + LTCG stack
    taxable_for_ltcg = total_ordinary + ltcg_pos
    if profile.get('federal_ltcg_rate') is not None:
        ltcg_rate = float(profile['federal_ltcg_rate'])
    else:
        ltcg_rate = ltcg_rate_for_income(taxable_for_ltcg, filing, year)
    federal_ltcg_tax = ltcg_pos * ltcg_rate

    # STCG already in ordinary progressive; report slice for display
    federal_stcg_tax = stcg_pos * ord_marginal  # illustrative marginal

    regular_federal = federal_ordinary_tax + federal_ltcg_tax

    # NIIT on investment income
    niit = 0.0
    if profile.get('include_niit', True):
        magi = total_ordinary + ltcg_pos  # simplified MAGI
        thr = NIIT_THRESHOLD.get(filing, 200000)
        investment = stcg_pos + ltcg_pos
        niit_base = min(investment, max(0.0, magi - thr))
        niit = niit_base * 0.038

    # State
    state_ord_rate = float(profile.get('state_ordinary_rate') or 0.0)
    state_cg_rate = float(profile.get('state_cg_rate') if profile.get('state_cg_rate') is not None else state_ord_rate)
    state_tax = (other_ord + equity_ordinary) * state_ord_rate + (stcg_pos + ltcg_pos) * state_cg_rate

    # FICA on equity ordinary only (vest/DD)
    fica = compute_fica(equity_ordinary, profile)

    # AMT: AMTI ≈ ordinary + LTCG preferred + ISO bargain (preference)
    amt_bargain = sum(r.amt_bargain_element for r in lot_results) if include_exercise_amt else 0.0
    # Regular taxable-like base for AMT comparison
    amti = other_ord + equity_ordinary + stcg_pos + ltcg_pos + amt_bargain
    amt_tax = compute_amt(amti, filing, year)
    credit = float(profile.get('amt_credit_carryforward') or 0.0)
    # AMT due is excess over regular federal (excluding NIIT)
    amt_due = max(0.0, amt_tax - regular_federal)
    # Credit can offset regular tax (not below AMT) — simplified: reduce federal by min(credit, regular)
    credit_used = min(credit, regular_federal) if amt_due == 0 else 0.0
    federal_after_credit = regular_federal - credit_used + amt_due + niit

    total_proceeds = sum(r.proceeds for r in lot_results)
    total_basis = sum(r.cost_basis for r in lot_results)
    total_tax = federal_after_credit + state_tax + fica
    gain = (total_proceeds - total_basis) + equity_ordinary
    eff = (total_tax / gain) if gain > 0 else 0.0

    if any(r.iso_disposition == 'unknown' or 'Assumed disqualifying' in ' '.join(r.notes) for r in lot_results):
        warnings.append('One or more ISO lots lack exercise_date; disposition classification may be incomplete.')
    if other_ord == 0 and (ltcg_pos + stcg_pos + equity_ordinary) > 0:
        warnings.append(
            'other_ordinary_income is $0 — brackets, NIIT, and AMT are highly sensitive to total income. '
            'Enter wages/other income in Tax Profile.'
        )

    return TaxAnalysis(
        tax_year=year,
        filing_status=filing,
        lots=lot_results,
        other_ordinary=other_ord,
        equity_ordinary=equity_ordinary,
        stcg=stcg_pos,
        ltcg=ltcg_pos,
        total_ordinary=total_ordinary,
        federal_ordinary_tax=federal_ordinary_tax,
        federal_ltcg_tax=federal_ltcg_tax,
        federal_stcg_tax=federal_stcg_tax,
        niit=niit,
        state_tax=state_tax,
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
            'ordinary_marginal': ord_marginal,
            'ltcg': ltcg_rate,
            'state_ordinary': state_ord_rate,
            'state_cg': state_cg_rate,
            'niit': 0.038 if profile.get('include_niit', True) else 0.0,
        },
    )
