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

    Tax Profile has two wage-ish fields that users confuse:
      - other_ordinary_income: intended full-year ordinary (wages, bonus, …)
      - ytd_wages: also often filled with annual wages (and used for FICA)

    Using only other_ordinary_income silently ignored $500k YTD wages and kept
    LTCG in the 15% band. We stack the **higher** of the two so wages count
    once for planning (they are the same economic wages, not additive).
    """
    other = float(profile.get('other_ordinary_income') or 0.0)
    ytd = float(profile.get('ytd_wages') or 0.0)
    return max(other, ytd)


def compute_fica(ordinary_equity: float, profile: dict) -> float:
    if not profile.get('include_fica', True):
        return 0.0
    year = profile.get('tax_year', 2026)
    wage_base = SS_WAGE_BASE.get(year, 184500)
    ytd = profile.get('ytd_wages', 0.0) or 0.0
    # FICA remaining wage base should also respect stacking ordinary if ytd empty
    ytd_for_fica = max(ytd, float(profile.get('other_ordinary_income') or 0.0))
    ss = 0.0
    if not profile.get('ss_wage_base_maxed'):
        remaining = max(0.0, wage_base - ytd_for_fica)
        ss = min(ordinary_equity, remaining) * SS_RATE
    medicare = ordinary_equity * MEDICARE_RATE
    filing = profile.get('filing_status', 'single')
    thr = ADD_MEDICARE_THRESHOLD.get(filing, 200000)
    add_base = max(0.0, (ytd_for_fica + ordinary_equity) - thr)
    # Additional Medicare only on wages over threshold — approximate on equity portion
    add_med = min(ordinary_equity, add_base) * ADDITIONAL_MEDICARE_RATE if add_base > 0 else 0.0
    return ss + medicare + add_med


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
    """
    from app.utils.state_tax import compute_state_tax

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

    taxable_for_ltcg = total_ordinary + ltcg_pos
    if profile.get('federal_ltcg_rate') is not None:
        # Manual override: flat rate on all LTCG (power-user / stress test)
        ltcg_rate = float(profile['federal_ltcg_rate'])
        federal_ltcg_tax = ltcg_pos * ltcg_rate
    else:
        # Stack LTCG on ordinary and fill 0% / 15% / 20% bands (not one flat rate)
        federal_ltcg_tax, ltcg_rate = preferential_ltcg_tax(
            ltcg_pos, total_ordinary, filing, year
        )
    federal_stcg_tax = stcg_pos * ord_marginal  # STCG already in ordinary progressive

    regular_federal = federal_ordinary_tax + federal_ltcg_tax

    niit = 0.0
    if profile.get('include_niit', True):
        magi = total_ordinary + ltcg_pos
        thr = NIIT_THRESHOLD.get(filing, 200000)
        investment = stcg_pos + ltcg_pos
        niit_base = min(investment, max(0.0, magi - thr))
        niit = niit_base * 0.038

    state_ord_rate = float(profile.get('state_ordinary_rate') or 0.0)
    state_cg_rate = float(
        profile.get('state_cg_rate') if profile.get('state_cg_rate') is not None else state_ord_rate
    )
    state_result = compute_state_tax(
        state_code=profile.get('state_code'),
        filing_status=filing,
        tax_year=year,
        ordinary_income=other_ord + equity_ordinary,
        capital_gains=stcg_pos + ltcg_pos,
        use_state_engine=bool(profile.get('use_state_engine', True)),
        state_ordinary_rate=state_ord_rate,
        state_cg_rate=state_cg_rate,
    )

    # Federal + CA AMT with minimum-tax credit rollforward
    amti_base = other_ord + equity_ordinary + stcg_pos + ltcg_pos
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

    if ytd_field > other_ord_field + 1.0 and other_ord_field > 0:
        warnings.append(
            f'Using ${other_ord:,.0f} ordinary for brackets (max of other ordinary '
            f'${other_ord_field:,.0f} and YTD wages ${ytd_field:,.0f}).'
        )
    elif ytd_field > other_ord_field + 1.0 and other_ord_field <= 0:
        warnings.append(
            f'YTD wages ${ytd_field:,.0f} used as ordinary stacking base '
            f'(other ordinary income was $0). Put full-year wages in either field.'
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
        # Ordinary + STCG sit under LTCG for preferential stacking
        ordinary_for_ltcg = other_ord + equity_ordinary + full_st
        room_in_15 = max(0.0, floor_20 - ordinary_for_ltcg)
        in_15 = min(ltcg_pos, room_in_15) if ordinary_for_ltcg < floor_20 else 0.0
        in_20 = max(0.0, ltcg_pos - in_15)
        ti_top = ordinary_for_ltcg + ltcg_pos
        if in_20 > 1 and in_15 > 1:
            warnings.append(
                f'Federal LTCG split: ${in_15:,.0f} @ 15% + ${in_20:,.0f} @ 20% '
                f'(wages/ordinary base ${ordinary_for_ltcg:,.0f}; 20% starts at ${floor_20:,.0f} TI).'
            )
        elif in_20 > 1:
            warnings.append(
                f'Federal LTCG all @ 20% (TI through gains ≈ ${ti_top:,.0f}; ordinary base ${ordinary_for_ltcg:,.0f}).'
            )
        else:
            warnings.append(
                f'Federal LTCG @ 15% (ordinary base ${ordinary_for_ltcg:,.0f}; '
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
