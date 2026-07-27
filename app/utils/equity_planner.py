"""
Equity compensation scenario planner (CPA-oriented).

Models exercise and sale as separate economic events — the core mistake in
naive "sell qty" tools for ISOs.

Strategies (inspired by standard ISO planning practice / Secfi-style paths):
  - rsu_sell              Sell vested RSUs (CG vs vest FMV basis)
  - iso_cashless_dd       Exercise + same-day sale → disqualifying (ordinary + residual CG)
  - iso_exercise_hold     Exercise only → cash outlay + AMT; project QD calendar
  - iso_exercise_sell_qd  Exercise now, sell on earliest qualifying date at assumed price
  - iso_sell_held         Sell already-exercised shares (QD or DD from dates)
  - compare_iso           Cashless DD vs exercise-hold vs hold-to-QD side-by-side

Disclaimer: planning model, not a tax return or legal advice.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from app.utils.tax_engine import (
    ExerciseInput,
    LotSaleInput,
    TaxAnalysis,
    analyze_sales,
    classify_iso_disposition,
    earliest_qualifying_sale_date,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class TimelineEvent:
    event_date: str
    kind: str  # exercise | sale | qd_eligible | note
    title: str
    detail: str
    amount: float = 0.0


@dataclass
class CashSummary:
    exercise_cash_outlay: float = 0.0
    sale_gross_proceeds: float = 0.0
    incremental_tax: float = 0.0  # sum across years modeled
    net_cash: float = 0.0  # proceeds - outlay - tax
    amt_due_total: float = 0.0
    notes: List[str] = field(default_factory=list)


@dataclass
class YearSlice:
    tax_year: int
    role: str  # exercise | sale | both
    analysis: Dict[str, Any]
    exercises: List[Dict[str, Any]] = field(default_factory=list)
    sales_summary: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ScenarioPlan:
    strategy: str
    name: str
    description: str
    years: List[YearSlice]
    cash: CashSummary
    timeline: List[TimelineEvent]
    recommendations: List[str]
    iso_meta: Dict[str, Any] = field(default_factory=dict)
    total_incremental_tax: float = 0.0
    total_net_cash: float = 0.0
    effective_rate_on_economic_gain: float = 0.0
    economic_gain: float = 0.0
    warnings: List[str] = field(default_factory=list)
    amt_credit_ledger: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'strategy': self.strategy,
            'name': self.name,
            'description': self.description,
            'years': [
                {
                    'tax_year': y.tax_year,
                    'role': y.role,
                    'analysis': y.analysis,
                    'exercises': y.exercises,
                    'sales_summary': y.sales_summary,
                }
                for y in self.years
            ],
            'cash': asdict(self.cash),
            'timeline': [asdict(t) for t in self.timeline],
            'recommendations': self.recommendations,
            'iso_meta': self.iso_meta,
            'total_incremental_tax': self.total_incremental_tax,
            'total_net_cash': self.total_net_cash,
            'effective_rate_on_economic_gain': self.effective_rate_on_economic_gain,
            'economic_gain': self.economic_gain,
            'warnings': self.warnings,
            'amt_credit_ledger': self.amt_credit_ledger,
            # Convenience: primary year analysis (first) for simple UI
            'analysis': self.years[0].analysis if self.years else None,
        }


@dataclass
class LotSpec:
    """Normalized lot inputs from API / inventory."""
    vest_event_id: int
    grant_id: int
    share_type: str
    grant_type: str
    is_iso: bool
    shares: float  # shares involved in this plan line
    vest_date: date
    grant_date: date
    strike_price: float
    cost_basis_per_share: float  # RSU vest FMV or ISO strike
    # Existing exercise (if already exercised)
    exercise_date: Optional[date] = None
    fmv_at_exercise: Optional[float] = None
    shares_available: float = 0.0  # currently exercisable held stock
    shares_unexercised: float = 0.0
    label: str = ''
    commission: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _profile_for_year(profile: dict, year: int) -> dict:
    p = deepcopy(profile)
    p['tax_year'] = year
    return p


def _analysis_dict(a: TaxAnalysis) -> dict:
    return a.to_dict()


def _build_sale(
    lot: LotSpec,
    *,
    shares: float,
    sale_date: date,
    sale_price: float,
    exercise_date: Optional[date],
    fmv_at_exercise: Optional[float],
    commission: float = 0.0,
) -> LotSaleInput:
    return LotSaleInput(
        vest_event_id=lot.vest_event_id,
        grant_id=lot.grant_id,
        share_type=lot.share_type,
        grant_type=lot.grant_type,
        shares=shares,
        sale_price=sale_price,
        sale_date=sale_date,
        vest_date=lot.vest_date,
        grant_date=lot.grant_date,
        cost_basis_per_share=lot.cost_basis_per_share,
        is_iso=lot.is_iso,
        strike_price=lot.strike_price,
        exercise_date=exercise_date,
        fmv_at_exercise=fmv_at_exercise,
        commission=commission or lot.commission,
        label=lot.label or f'{lot.share_type} {lot.vest_date}',
    )


def _build_exercise(
    lot: LotSpec,
    *,
    shares: float,
    exercise_date: date,
    fmv: float,
) -> ExerciseInput:
    return ExerciseInput(
        vest_event_id=lot.vest_event_id,
        shares=shares,
        exercise_date=exercise_date,
        strike_price=lot.strike_price,
        fmv_at_exercise=fmv,
        grant_date=lot.grant_date,
        label=lot.label or f'Exercise {lot.share_type}',
        is_iso=lot.is_iso,
    )


def _year_role(has_ex: bool, has_sale: bool) -> str:
    if has_ex and has_sale:
        return 'both'
    if has_ex:
        return 'exercise'
    return 'sale'


def _assemble(
    *,
    strategy: str,
    name: str,
    description: str,
    year_payloads: List[tuple],  # (year, role, TaxAnalysis, exercises_meta, sales_meta)
    cash_outlay: float,
    sale_proceeds: float,
    timeline: List[TimelineEvent],
    recommendations: List[str],
    iso_meta: Dict[str, Any],
    economic_gain: float,
    extra_warnings: Optional[List[str]] = None,
) -> ScenarioPlan:
    years: List[YearSlice] = []
    total_tax = 0.0
    amt_total = 0.0
    ca_amt_total = 0.0
    warnings: List[str] = list(extra_warnings or [])
    ledger: List[Dict[str, Any]] = []

    for year, role, analysis, ex_meta, sale_meta in year_payloads:
        years.append(
            YearSlice(
                tax_year=year,
                role=role,
                analysis=_analysis_dict(analysis),
                exercises=ex_meta,
                sales_summary=sale_meta,
            )
        )
        total_tax += analysis.total_tax
        amt_total += analysis.amt_due
        ca_amt_total += getattr(analysis, 'ca_amt_due', 0.0) or 0.0
        warnings.extend(analysis.warnings or [])
        ledger.append({
            'tax_year': year,
            'role': role,
            'federal_amt_due': analysis.amt_due,
            'ca_amt_due': getattr(analysis, 'ca_amt_due', 0.0) or 0.0,
            'federal_credit_opening': getattr(analysis, 'federal_amt_credit_opening', 0.0) or 0.0,
            'federal_credit_generated': getattr(analysis, 'federal_amt_credit_generated', 0.0) or 0.0,
            'federal_credit_used': getattr(analysis, 'federal_amt_credit_used', 0.0) or 0.0,
            'federal_credit_ending': getattr(analysis, 'federal_amt_credit_ending', 0.0) or 0.0,
            'ca_credit_opening': getattr(analysis, 'ca_amt_credit_opening', 0.0) or 0.0,
            'ca_credit_generated': getattr(analysis, 'ca_amt_credit_generated', 0.0) or 0.0,
            'ca_credit_used': getattr(analysis, 'ca_amt_credit_used', 0.0) or 0.0,
            'ca_credit_ending': getattr(analysis, 'ca_amt_credit_ending', 0.0) or 0.0,
        })

    net = sale_proceeds - cash_outlay - total_tax
    eff = (total_tax / economic_gain) if economic_gain > 0 else 0.0

    cash = CashSummary(
        exercise_cash_outlay=cash_outlay,
        sale_gross_proceeds=sale_proceeds,
        incremental_tax=total_tax,
        net_cash=net,
        amt_due_total=amt_total + ca_amt_total,
        notes=[
            'Net cash = sale proceeds − exercise strike outlay − incremental tax across modeled years.',
            f'Federal AMT due (sum of years): ${amt_total:,.0f}; CA AMT due (sum): ${ca_amt_total:,.0f}.',
            'AMT credit ledger shows opening → generated → used → ending by year.',
        ],
    )

    return ScenarioPlan(
        strategy=strategy,
        name=name,
        description=description,
        years=years,
        cash=cash,
        timeline=timeline,
        recommendations=recommendations,
        iso_meta=iso_meta,
        total_incremental_tax=total_tax,
        total_net_cash=net,
        effective_rate_on_economic_gain=eff,
        economic_gain=economic_gain,
        warnings=_dedupe(warnings),
        amt_credit_ledger=ledger,
    )


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------

def plan_rsu_sell(
    profile: dict,
    lots: Sequence[LotSpec],
    *,
    sale_date: date,
    sale_price: float,
) -> ScenarioPlan:
    sales = [
        _build_sale(
            lot,
            shares=lot.shares,
            sale_date=sale_date,
            sale_price=sale_price,
            exercise_date=None,
            fmv_at_exercise=None,
        )
        for lot in lots
        if not lot.is_iso and lot.shares > 0
    ]
    if not sales:
        raise ValueError('No RSU lots selected for rsu_sell')

    year = sale_date.year
    analysis = analyze_sales(_profile_for_year(profile, year), sales)
    proceeds = sum(s.shares * sale_price - s.commission for s in sales)
    basis = sum(s.shares * s.cost_basis_per_share for s in sales)
    gain = proceeds - basis

    timeline = [
        TimelineEvent(sale_date.isoformat(), 'sale', 'RSU sale', f'Sell at ${sale_price:.2f}', proceeds),
    ]
    recs = [
        'RSU ordinary income was (or will be) recognized at vest — this plan only models the sale vs vest FMV basis.',
        'Federal: ST vs LT depends on hold from vest (≥1 year for LTCG). CA taxes gains as ordinary.',
    ]
    return _assemble(
        strategy='rsu_sell',
        name='Sell RSUs',
        description='Sale of vested RSUs; capital gain/loss vs FMV at vest (basis).',
        year_payloads=[(year, 'sale', analysis, [], [{'shares': s.shares, 'price': sale_price} for s in sales])],
        cash_outlay=0.0,
        sale_proceeds=proceeds,
        timeline=timeline,
        recommendations=recs,
        iso_meta={},
        economic_gain=max(gain, 0.0),
    )


def plan_iso_cashless_dd(
    profile: dict,
    lots: Sequence[LotSpec],
    *,
    event_date: date,
    price: float,
) -> ScenarioPlan:
    """Exercise + same-day sale → always DD; ordinary income on spread; no AMT preference."""
    iso_lots = [l for l in lots if l.is_iso and l.shares > 0]
    if not iso_lots:
        raise ValueError('No ISO lots for cashless DD')

    sales = []
    outlay = 0.0
    for lot in iso_lots:
        sales.append(
            _build_sale(
                lot,
                shares=lot.shares,
                sale_date=event_date,
                sale_price=price,
                exercise_date=event_date,
                fmv_at_exercise=price,
            )
        )
        outlay += lot.strike_price * lot.shares

    year = event_date.year
    # Same-year DD: no ExerciseInput AMT (analyze_lot zeros AMT)
    analysis = analyze_sales(_profile_for_year(profile, year), sales, exercises=[])
    proceeds = sum(s.shares * price - s.commission for s in sales)
    # Economic gain ≈ spread to sale (ordinary + residual CG)
    economic = sum(
        max(0.0, price - lot.strike_price) * lot.shares for lot in iso_lots
    )

    timeline = [
        TimelineEvent(
            event_date.isoformat(),
            'exercise',
            'Exercise (cashless)',
            f'Pay strike (often netted from proceeds). Outlay ${outlay:,.0f}.',
            outlay,
        ),
        TimelineEvent(
            event_date.isoformat(),
            'sale',
            'Same-day sale (DD)',
            'Disqualifying disposition: spread as ordinary income + FICA; residual CG if sale ≠ FMV.',
            proceeds,
        ),
    ]
    recs = [
        'Cashless / same-day sale is simple cash-flow-wise but usually the highest ordinary-rate outcome.',
        'No ISO AMT preference when you disqualify in the exercise year — the spread is regular income instead.',
        'Compare to exercise-and-hold if you can fund the strike and potential AMT and believe in upside.',
    ]
    return _assemble(
        strategy='iso_cashless_dd',
        name='ISO cashless (exercise + sell same day)',
        description='Disqualifying disposition: ordinary income on bargain; no AMT preference.',
        year_payloads=[(year, 'both', analysis, [], [{'kind': 'dd', 'shares': l.shares} for l in iso_lots])],
        cash_outlay=outlay,
        sale_proceeds=proceeds,
        timeline=timeline,
        recommendations=recs,
        iso_meta={'disposition': 'disqualifying', 'same_day': True},
        economic_gain=economic,
    )


def plan_iso_exercise_hold(
    profile: dict,
    lots: Sequence[LotSpec],
    *,
    exercise_date: date,
    fmv: float,
) -> ScenarioPlan:
    """Exercise only: strike cash outlay + AMT preference; map QD calendar."""
    iso_lots = [l for l in lots if l.is_iso and l.shares > 0]
    if not iso_lots:
        raise ValueError('No ISO lots for exercise-hold')

    exercises = [
        _build_exercise(lot, shares=lot.shares, exercise_date=exercise_date, fmv=fmv)
        for lot in iso_lots
    ]
    year = exercise_date.year
    analysis = analyze_sales(
        _profile_for_year(profile, year),
        lots=[],
        exercises=exercises,
    )
    outlay = sum(e.cash_outlay for e in exercises)
    bargain = sum(e.bargain_element for e in exercises)

    timeline: List[TimelineEvent] = [
        TimelineEvent(
            exercise_date.isoformat(),
            'exercise',
            'Exercise & hold',
            f'Cash outlay ${outlay:,.0f}. AMT bargain element ${bargain:,.0f}. No regular income (ISO).',
            outlay,
        ),
    ]
    qd_dates = []
    for lot, ex in zip(iso_lots, exercises):
        qd = earliest_qualifying_sale_date(lot.grant_date, exercise_date)
        qd_dates.append(qd.isoformat())
        timeline.append(
            TimelineEvent(
                qd.isoformat(),
                'qd_eligible',
                f'QD eligible · {lot.label or lot.vest_event_id}',
                'First date a sale can be qualifying (2y from grant and 1y from exercise).',
                0.0,
            )
        )

    recs = [
        'You must fund the strike (and often a cash reserve for AMT) with no sale proceeds.',
        f'Total AMT bargain element this year: ${bargain:,.0f}. AMT due is incremental vs regular tax on wages alone.',
        'If you later sell in a QD, federal gain over strike is LTCG; CA still taxes as ordinary.',
        'Track AMT credit carryforward after an AMT year — set it on Tax Profile for later sale years.',
        'Risk: price decline after exercise leaves you with AMT paid on a bargain that evaporated.',
    ]
    return _assemble(
        strategy='iso_exercise_hold',
        name='ISO exercise & hold (AMT year)',
        description='Exercise only this year: cash outlay + AMT preference; no sale.',
        year_payloads=[
            (
                year,
                'exercise',
                analysis,
                [
                    {
                        'shares': e.shares,
                        'strike': e.strike_price,
                        'fmv': e.fmv_at_exercise,
                        'bargain': e.bargain_element,
                        'cash_outlay': e.cash_outlay,
                    }
                    for e in exercises
                ],
                [],
            )
        ],
        cash_outlay=outlay,
        sale_proceeds=0.0,
        timeline=timeline,
        recommendations=recs,
        iso_meta={
            'bargain_element': bargain,
            'earliest_qd_dates': qd_dates,
            'fmv_at_exercise': fmv,
        },
        economic_gain=bargain,
    )


def plan_iso_exercise_sell_qd(
    profile: dict,
    lots: Sequence[LotSpec],
    *,
    exercise_date: date,
    fmv_at_exercise: float,
    sale_price: float,
    sale_date: Optional[date] = None,
) -> ScenarioPlan:
    """
    Exercise now, sell on earliest QD date at an assumed future price.
    Multi-year when sale year > exercise year.
    """
    iso_lots = [l for l in lots if l.is_iso and l.shares > 0]
    if not iso_lots:
        raise ValueError('No ISO lots for exercise→QD sale')

    # Per-lot QD date; use max so all lots can qualify on the planned sale date
    qd_dates = [earliest_qualifying_sale_date(l.grant_date, exercise_date) for l in iso_lots]
    planned_sale = sale_date or max(qd_dates)
    # Ensure not before earliest
    if planned_sale < max(qd_dates):
        planned_sale = max(qd_dates)

    exercises = [
        _build_exercise(lot, shares=lot.shares, exercise_date=exercise_date, fmv=fmv_at_exercise)
        for lot in iso_lots
    ]
    sales = [
        _build_sale(
            lot,
            shares=lot.shares,
            sale_date=planned_sale,
            sale_price=sale_price,
            exercise_date=exercise_date,
            fmv_at_exercise=fmv_at_exercise,
        )
        for lot in iso_lots
    ]

    ex_year = exercise_date.year
    sale_year = planned_sale.year
    outlay = sum(e.cash_outlay for e in exercises)
    proceeds = sum(s.shares * sale_price - s.commission for s in sales)
    # Economic: (sale - strike) * shares across path (ignores time value)
    economic = sum(max(0.0, sale_price - lot.strike_price) * lot.shares for lot in iso_lots)

    year_payloads = []
    if ex_year == sale_year:
        # Cannot actually be QD if sale same year as exercise (need 1 year hold)
        # Still run as one year for completeness — classification will be DD
        analysis = analyze_sales(
            _profile_for_year(profile, ex_year),
            sales,
            exercises=[],  # same-year sale path
        )
        year_payloads.append((ex_year, 'both', analysis, [], [{'disposition': 'check'}]))
        extra_w = [
            'Sale year equals exercise year — ISO holding period for QD cannot be met; treated as DD.'
        ]
    else:
        # Year 1: exercise AMT only — generates federal (+ CA) AMT credit
        prof_ex = _profile_for_year(profile, ex_year)
        a_ex = analyze_sales(prof_ex, lots=[], exercises=exercises)
        year_payloads.append(
            (
                ex_year,
                'exercise',
                a_ex,
                [{'bargain': e.bargain_element, 'outlay': e.cash_outlay} for e in exercises],
                [],
            )
        )
        # Year 2+: hand off ending AMT credits into sale-year profile
        prof_sale = _profile_for_year(profile, sale_year)
        prof_sale['amt_credit_carryforward'] = float(
            a_ex.federal_amt_credit_ending or profile.get('amt_credit_carryforward') or 0.0
        )
        prof_sale['ca_amt_credit_carryforward'] = float(
            a_ex.ca_amt_credit_ending or profile.get('ca_amt_credit_carryforward') or 0.0
        )
        a_sale = analyze_sales(prof_sale, sales, exercises=[])
        year_payloads.append(
            (
                sale_year,
                'sale',
                a_sale,
                [],
                [
                    {
                        'disposition': classify_iso_disposition(
                            lot.grant_date, exercise_date, planned_sale
                        ),
                        'shares': lot.shares,
                    }
                    for lot in iso_lots
                ],
            )
        )
        extra_w = [
            f'Assumes other ordinary income in {sale_year} matches Tax Profile '
            f'(wages may change — re-run with updated profile).',
            f'AMT credit handoff: federal ending ${a_ex.federal_amt_credit_ending:,.0f} → '
            f'sale-year opening; CA ending ${a_ex.ca_amt_credit_ending:,.0f} → sale-year opening.',
            f'Sale year federal credit used ${a_sale.federal_amt_credit_used:,.0f}; '
            f'ending ${a_sale.federal_amt_credit_ending:,.0f}.',
        ]

    timeline = [
        TimelineEvent(
            exercise_date.isoformat(),
            'exercise',
            'Exercise',
            f'Outlay ${outlay:,.0f}; FMV ${fmv_at_exercise:.2f}; AMT bargain starts.',
            outlay,
        ),
        TimelineEvent(
            max(qd_dates).isoformat(),
            'qd_eligible',
            'Earliest QD date',
            'First date all selected lots can qualify.',
            0.0,
        ),
        TimelineEvent(
            planned_sale.isoformat(),
            'sale',
            'Planned QD sale',
            f'Assumed sale price ${sale_price:.2f}.',
            proceeds,
        ),
    ]
    recs = [
        'This path optimizes for federal LTCG on post-grant appreciation if price holds and you meet both holding periods.',
        'You still face CA ordinary rates on the gain and possible NIIT federally.',
        'Liquidity risk: AMT and strike cash before any sale proceeds.',
        'If price falls between exercise and sale, you may have paid AMT on a bargain larger than eventual economic gain.',
    ]
    return _assemble(
        strategy='iso_exercise_sell_qd',
        name='ISO exercise → hold for QD → sell',
        description='Two-event path: AMT at exercise, preferential federal LTCG if QD met at sale.',
        year_payloads=year_payloads,
        cash_outlay=outlay,
        sale_proceeds=proceeds,
        timeline=timeline,
        recommendations=recs,
        iso_meta={
            'exercise_date': exercise_date.isoformat(),
            'planned_sale_date': planned_sale.isoformat(),
            'earliest_qd': max(qd_dates).isoformat(),
            'fmv_at_exercise': fmv_at_exercise,
            'assumed_sale_price': sale_price,
        },
        economic_gain=economic,
        extra_warnings=extra_w,
    )


def plan_iso_sell_held(
    profile: dict,
    lots: Sequence[LotSpec],
    *,
    sale_date: date,
    sale_price: float,
) -> ScenarioPlan:
    """Sell shares already exercised (use exercise_date / fmv on each lot)."""
    iso_lots = [l for l in lots if l.is_iso and l.shares > 0]
    if not iso_lots:
        raise ValueError('No ISO lots for sell-held')

    missing = [l for l in iso_lots if not l.exercise_date]
    if missing:
        raise ValueError(
            'ISO sell-held requires exercise_date on each lot. '
            'Record an exercise first, or use cashless / exercise-hold strategies.'
        )

    sales = [
        _build_sale(
            lot,
            shares=lot.shares,
            sale_date=sale_date,
            sale_price=sale_price,
            exercise_date=lot.exercise_date,
            fmv_at_exercise=lot.fmv_at_exercise if lot.fmv_at_exercise is not None else sale_price,
        )
        for lot in iso_lots
    ]
    year = sale_date.year
    analysis = analyze_sales(_profile_for_year(profile, year), sales)
    proceeds = sum(s.shares * sale_price - s.commission for s in sales)
    economic = 0.0
    disps = []
    for lot in iso_lots:
        disp = classify_iso_disposition(lot.grant_date, lot.exercise_date, sale_date)
        disps.append(disp)
        economic += max(0.0, sale_price - lot.strike_price) * lot.shares

    timeline = [
        TimelineEvent(
            sale_date.isoformat(),
            'sale',
            'Sell exercised ISO shares',
            f'Dispositions: {", ".join(disps)}. Price ${sale_price:.2f}.',
            proceeds,
        )
    ]
    recs = [
        'QD → federal LTCG on (sale − strike). DD → ordinary on bargain + residual CG from exercise FMV.',
        'AMT from a prior exercise year is not re-taxed; use AMT credit carryforward on Tax Profile if you have one.',
    ]
    return _assemble(
        strategy='iso_sell_held',
        name='Sell already-exercised ISOs',
        description='Disposition of held ISO stock; QD vs DD from grant/exercise/sale dates.',
        year_payloads=[
            (
                year,
                'sale',
                analysis,
                [],
                [{'disposition': d, 'shares': l.shares} for d, l in zip(disps, iso_lots)],
            )
        ],
        cash_outlay=0.0,
        sale_proceeds=proceeds,
        timeline=timeline,
        recommendations=recs,
        iso_meta={'dispositions': disps},
        economic_gain=economic,
    )


def plan_mixed_default(
    profile: dict,
    lots: Sequence[LotSpec],
    *,
    sale_date: date,
    sale_price: float,
    exercise_date: Optional[date] = None,
    exercise_fmv: Optional[float] = None,
) -> ScenarioPlan:
    """
    Smart default: RSUs sell; already-exercised ISOs sell; unexercised ISOs cashless DD
    unless exercise_date provided without same-day (then exercise-hold for those).
    """
    rsu = [l for l in lots if not l.is_iso and l.shares > 0]
    iso_held = [l for l in lots if l.is_iso and l.shares > 0 and l.exercise_date]
    iso_unex = [l for l in lots if l.is_iso and l.shares > 0 and not l.exercise_date]

    plans: List[ScenarioPlan] = []
    if rsu:
        plans.append(plan_rsu_sell(profile, rsu, sale_date=sale_date, sale_price=sale_price))
    if iso_held:
        plans.append(plan_iso_sell_held(profile, iso_held, sale_date=sale_date, sale_price=sale_price))
    if iso_unex:
        # Default unexercised → cashless DD at sale date/price
        plans.append(
            plan_iso_cashless_dd(
                profile,
                iso_unex,
                event_date=sale_date,
                price=sale_price,
            )
        )

    if not plans:
        raise ValueError('No lots with share quantities to plan')

    if len(plans) == 1:
        return plans[0]

    # Merge multi-asset plan into one ScenarioPlan
    total_tax = sum(p.total_incremental_tax for p in plans)
    proceeds = sum(p.cash.sale_gross_proceeds for p in plans)
    outlay = sum(p.cash.exercise_cash_outlay for p in plans)
    economic = sum(p.economic_gain for p in plans)
    timeline: List[TimelineEvent] = []
    recs: List[str] = ['Combined plan across RSU and ISO lots:']
    warnings: List[str] = []
    years_map: Dict[int, List] = {}

    for p in plans:
        timeline.extend(p.timeline)
        recs.extend([f'[{p.strategy}] {r}' for r in p.recommendations[:2]])
        warnings.extend(p.warnings)
        for y in p.years:
            years_map.setdefault(y.tax_year, []).append(y)

    # Prefer single-year merged analysis if all same year
    year_payloads = []
    for yr, slices in sorted(years_map.items()):
        # Use first analysis as display; tax already summed in cash
        year_payloads.append(
            (
                yr,
                slices[0].role,
                # Rebuild a synthetic TaxAnalysis-like dict path: re-use first slice analysis
                # but override totals — easier to re-run combined analyze
                None,
                [],
                [],
            )
        )

    # Re-run one combined analysis for the sale year for accurate stacked tax
    all_sales: List[LotSaleInput] = []
    all_ex: List[ExerciseInput] = []
    for lot in rsu:
        all_sales.append(
            _build_sale(
                lot,
                shares=lot.shares,
                sale_date=sale_date,
                sale_price=sale_price,
                exercise_date=None,
                fmv_at_exercise=None,
            )
        )
    for lot in iso_held:
        all_sales.append(
            _build_sale(
                lot,
                shares=lot.shares,
                sale_date=sale_date,
                sale_price=sale_price,
                exercise_date=lot.exercise_date,
                fmv_at_exercise=lot.fmv_at_exercise or sale_price,
            )
        )
    for lot in iso_unex:
        all_sales.append(
            _build_sale(
                lot,
                shares=lot.shares,
                sale_date=sale_date,
                sale_price=sale_price,
                exercise_date=sale_date,
                fmv_at_exercise=sale_price,
            )
        )

    year = sale_date.year
    analysis = analyze_sales(_profile_for_year(profile, year), all_sales, exercises=all_ex)
    total_tax = analysis.total_tax
    economic = max(
        economic,
        (analysis.total_proceeds - analysis.total_cost_basis) + analysis.equity_ordinary,
    )

    return _assemble(
        strategy='mixed',
        name='Combined RSU + ISO plan',
        description='RSUs sold; exercised ISOs sold; unexercised ISOs treated as cashless DD.',
        year_payloads=[(year, 'both', analysis, [], [])],
        cash_outlay=outlay,
        sale_proceeds=proceeds if proceeds else analysis.total_proceeds,
        timeline=timeline,
        recommendations=recs,
        iso_meta={'components': [p.strategy for p in plans]},
        economic_gain=economic,
        extra_warnings=warnings,
    )


def compare_iso_strategies(
    profile: dict,
    lots: Sequence[LotSpec],
    *,
    exercise_date: date,
    fmv_at_exercise: float,
    sale_price: float,
    sale_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Side-by-side: cashless DD vs exercise-hold vs exercise→QD sale."""
    iso_lots = [l for l in lots if l.is_iso and l.shares > 0]
    if not iso_lots:
        raise ValueError('Compare requires ISO lots with share quantities')

    cashless = plan_iso_cashless_dd(
        profile, iso_lots, event_date=exercise_date, price=fmv_at_exercise
    )
    hold = plan_iso_exercise_hold(
        profile, iso_lots, exercise_date=exercise_date, fmv=fmv_at_exercise
    )
    qd = plan_iso_exercise_sell_qd(
        profile,
        iso_lots,
        exercise_date=exercise_date,
        fmv_at_exercise=fmv_at_exercise,
        sale_price=sale_price,
        sale_date=sale_date,
    )

    rows = []
    for p in (cashless, hold, qd):
        rows.append(
            {
                'strategy': p.strategy,
                'name': p.name,
                'net_cash': p.total_net_cash,
                'total_tax': p.total_incremental_tax,
                'amt_due': p.cash.amt_due_total,
                'exercise_outlay': p.cash.exercise_cash_outlay,
                'sale_proceeds': p.cash.sale_gross_proceeds,
                'effective_rate': p.effective_rate_on_economic_gain,
                'economic_gain': p.economic_gain,
            }
        )

    # Rank by net cash (hold may be negative — still informative)
    ranked = sorted(rows, key=lambda r: r['net_cash'], reverse=True)
    winner = ranked[0]['strategy'] if ranked else None

    return {
        'success': True,
        'compare': True,
        'winner_by_net_cash': winner,
        'summary_rows': ranked,
        'scenarios': {
            'iso_cashless_dd': cashless.to_dict(),
            'iso_exercise_hold': hold.to_dict(),
            'iso_exercise_sell_qd': qd.to_dict(),
        },
        'guidance': [
            'Cashless maximizes simplicity and liquidity; usually highest ordinary tax rate.',
            'Exercise-and-hold minimizes ordinary income now but creates AMT + cash need with no proceeds.',
            'Hold-to-QD can unlock federal LTCG if price holds and you meet both clocks — model CA ordinary rates honestly.',
            'Winner-by-net-cash ignores concentration risk, liquidity needs, and future wage changes.',
        ],
    }


def run_plan(
    profile: dict,
    lots: Sequence[LotSpec],
    *,
    strategy: str,
    sale_date: Optional[date] = None,
    sale_price: Optional[float] = None,
    exercise_date: Optional[date] = None,
    exercise_fmv: Optional[float] = None,
    cover_strike: bool = True,
    cover_tax: bool = True,
) -> Dict[str, Any]:
    """Dispatch strategy → ScenarioPlan, compare, or liquidity cover payload."""
    strategy = (strategy or 'auto').strip().lower()
    today = date.today()
    sale_date = sale_date or today
    exercise_date = exercise_date or sale_date
    sale_price = float(sale_price if sale_price is not None else 0)
    exercise_fmv = float(exercise_fmv if exercise_fmv is not None else sale_price)

    if strategy in ('compare', 'compare_iso', 'iso_compare'):
        return compare_iso_strategies(
            profile,
            lots,
            exercise_date=exercise_date,
            fmv_at_exercise=exercise_fmv,
            sale_price=sale_price or exercise_fmv,
            sale_date=sale_date,
        )

    if strategy in (
        'iso_sell_to_cover', 'sell_to_cover', 'cover',
        'rsu_fund_iso', 'rsu_cover',
    ):
        from app.utils.liquidity import run_liquidity
        mode = 'rsu_fund_iso' if strategy in ('rsu_fund_iso', 'rsu_cover') else 'iso_sell_to_cover'
        liq = run_liquidity(
            profile,
            lots,
            mode=mode,
            sale_date=sale_date,
            sale_price=sale_price or exercise_fmv,
            exercise_date=exercise_date,
            exercise_fmv=exercise_fmv,
            cover_strike=cover_strike,
            cover_tax=cover_tax,
        )
        # Also attach a cashless plan on the recommended sell qty for tax detail
        cover = liq.get('cover') or {}
        plan_dict = None
        if cover.get('shares_to_sell', 0) > 0 and mode == 'iso_sell_to_cover':
            sell_lots = []
            remaining = cover['shares_to_sell']
            for lot in lots:
                if not lot.is_iso or remaining <= 0:
                    continue
                take = min(lot.shares, remaining)
                if take > 0:
                    nl = LotSpec(**{**lot.__dict__, 'shares': take})
                    sell_lots.append(nl)
                    remaining -= take
            if sell_lots:
                plan_dict = plan_iso_cashless_dd(
                    profile, sell_lots, event_date=exercise_date, price=exercise_fmv
                ).to_dict()
        return {
            'success': True,
            'compare': False,
            'liquidity': True,
            'cover': cover,
            'plan': plan_dict,
            'analysis': plan_dict.get('analysis') if plan_dict else None,
        }

    if strategy in ('auto', 'mixed', 'default'):
        plan = plan_mixed_default(
            profile,
            lots,
            sale_date=sale_date,
            sale_price=sale_price,
            exercise_date=exercise_date,
            exercise_fmv=exercise_fmv,
        )
    elif strategy in ('rsu_sell', 'sell_rsu'):
        plan = plan_rsu_sell(profile, lots, sale_date=sale_date, sale_price=sale_price)
    elif strategy in ('iso_cashless_dd', 'cashless', 'same_day'):
        plan = plan_iso_cashless_dd(
            profile, lots, event_date=exercise_date, price=exercise_fmv or sale_price
        )
    elif strategy in ('iso_exercise_hold', 'exercise_hold', 'hold'):
        plan = plan_iso_exercise_hold(
            profile, lots, exercise_date=exercise_date, fmv=exercise_fmv
        )
    elif strategy in ('iso_exercise_sell_qd', 'exercise_qd', 'qd'):
        plan = plan_iso_exercise_sell_qd(
            profile,
            lots,
            exercise_date=exercise_date,
            fmv_at_exercise=exercise_fmv,
            sale_price=sale_price or exercise_fmv,
            sale_date=sale_date,
        )
    elif strategy in ('iso_sell_held', 'sell_held'):
        plan = plan_iso_sell_held(profile, lots, sale_date=sale_date, sale_price=sale_price)
    else:
        raise ValueError(f'Unknown strategy: {strategy}')

    return {'success': True, 'compare': False, 'plan': plan.to_dict()}
