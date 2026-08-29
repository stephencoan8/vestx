"""
Withholding vs liability — the under/over-payment counter.

Expected tax (income tax + AMT, not FICA) vs expected withholding
(paycheck + estimates). Splits locked-in (vests/sales to date) vs still coming
(remaining year vests @ live, supplemental rates).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from app.utils.tax_constants import CA_ES_FRACTIONS, CA_ES_SOURCE, CITATIONS
from app.utils.withholding import (
    entered_amount,
    vest_paycheck_withholding,
    wages_only_income_tax,
)
from app.utils.estimated_tax_calendar import (
    federal_estimated_due_dates,
    safe_harbor_targets,
)
from app.utils.wage_year_tax import (
    year_income_stack,
    compute_w2_year_tax,
    build_year_vest_prefill,
)


def _prior_year_income_tax(user, tax_year: int, profile, entered: Optional[float]):
    """Prior-year fed+CA income tax for §6654. Entered stub wins; else compute from that year."""
    if entered is not None:
        return float(entered), 'entered'
    prev = int(tax_year) - 1
    try:
        from app.models.tax_year_profile import TaxYearProfile
        row = TaxYearProfile.get_for(user.id, prev)
        cash = float(row.other_ordinary_income or 0) if row else 0.0
        filing = (row.filing_status if row else None) or getattr(profile, 'filing_status', None) or 'single'
        state = ((row.state_code if row else None) or getattr(profile, 'state_code', None) or 'CA')
        stack = year_income_stack(user.id, prev, cash_wages=cash)
        y = compute_w2_year_tax(
            tax_year=prev,
            filing_status=filing,
            state_code=state,
            wages=float(stack.get('ordinary') or 0),
            stcg=float(stack.get('stcg') or 0),
            ltcg=float(stack.get('ltcg') or 0),
            include_fica=False,
            use_state_engine=True,
            fica_wages=0.0,
        )
        t = float(y.income_tax_total or 0)
        if t > 0:
            return t, 'computed'
    except Exception:
        pass
    return 0.0, 'missing'


def _safe_harbor_line(
    *,
    tax_year: int,
    prior_tax: float,
    prior_source: str,
    harbor: dict,
    ytd_credits: float,
    no_penalty: bool,
    april_balance: float,
) -> str:
    prior_y = tax_year - 1
    h110 = float(harbor.get('prior_year_safe_harbor') or 0)
    c90 = float(harbor.get('current_year_90pct') or 0)
    required = float(harbor.get('required_annual') or 0)
    src = 'entered' if prior_source == 'entered' else ('computed from %s return' % prior_y if prior_source == 'computed' else 'enter prior-year tax')
    if prior_tax <= 0:
        return (
            f'Enter {prior_y} total tax to test 110% safe harbor. '
            f'90% of {tax_year} ≈ ${c90:,.0f}. YTD credits ${ytd_credits:,.0f}. '
            f'April bill ${april_balance:,.0f}.'
        )
    penalty = 'No penalty' if no_penalty else 'Penalty risk — YTD credits are under the safe harbor'
    return (
        f'110% of {prior_y} tax ({src}) = ${h110:,.0f} vs 90% of {tax_year} = ${c90:,.0f}. '
        f'Need ${required:,.0f}. YTD credits ${ytd_credits:,.0f}. '
        f'{penalty}. April bill ${april_balance:,.0f}.'
    )


def _iso_bargain_for_year(user_id: int, tax_year: int) -> float:
    try:
        from app.models.stock_sale import ISOExercise
        rows = ISOExercise.query.filter_by(user_id=user_id).all()
    except Exception:
        return 0.0
    total = 0.0
    for e in rows:
        if not e.exercise_date or e.exercise_date.year != tax_year:
            continue
        barg = e.total_bargain_element
        if barg is None:
            sh = float(e.shares_exercised or 0)
            barg = sh * max(0.0, float(e.fmv_at_exercise or 0) - float(e.strike_price or 0))
        total += float(barg or 0)
    return total


def _year_amt_due(profile: dict, bargain: float) -> float:
    """ISO AMT on the annual stack (Form 6251 / CA Schedule P planning)."""
    if bargain <= 0:
        return 0.0
    try:
        from app.utils.tax_engine import analyze_sales, ExerciseInput
        from datetime import date as d
        ex = ExerciseInput(
            vest_event_id=0,
            shares=1.0,
            exercise_date=d(int(profile.get('tax_year') or d.today().year), 6, 30),
            strike_price=0.0,
            fmv_at_exercise=float(bargain),
            label='ISO exercises (year total)',
        )
        # 1 share at FMV=bargain, strike=0 → bargain element = bargain
        a = analyze_sales(profile, [], exercises=[ex])
        return float(a.amt_due or 0) + float(getattr(a, 'ca_amt_due', 0) or 0)
    except Exception:
        return 0.0


def build_cash_vs_tax(
    user,
    *,
    tax_year: Optional[int] = None,
    profile=None,
    as_of: Optional[date] = None,
) -> Dict[str, Any]:
    """
    First-class panel payload for Tax profile + Activity.
    """
    as_of = as_of or date.today()
    tax_year = int(tax_year or as_of.year)

    from app.models.tax_profile import TaxProfile
    from app.utils.tax_engine import resolve_engine_profile_for_year

    if profile is None:
        profile = TaxProfile.for_user(user)

    eng = resolve_engine_profile_for_year(user, tax_year)
    filing = eng.get('filing_status') or 'single'
    state = (eng.get('state_code') or 'CA').upper()
    cash = float(eng.get('other_ordinary_income_raw') or 0)
    stack = year_income_stack(user.id, tax_year, cash_wages=cash)
    vest = stack.get('vest') or build_year_vest_prefill(user.id, tax_year)

    eq_past = float(stack.get('equity_vested_ytd') or 0)
    eq_fut = float(stack.get('equity_remaining_year') or 0)
    stcg = float(stack.get('stcg') or 0)
    ltcg = float(stack.get('ltcg') or 0)
    ordinary = float(stack.get('ordinary') or 0)

    year_tax = compute_w2_year_tax(
        tax_year=tax_year,
        filing_status=filing,
        state_code=state,
        wages=ordinary,
        stcg=stcg,
        ltcg=ltcg,
        include_fica=True,
        ss_wage_base_maxed=False,
        use_state_engine=True,
        vest_prefills=vest,
        fica_wages=ordinary,
    )
    bargain = _iso_bargain_for_year(user.id, tax_year)
    amt_due = _year_amt_due(eng, bargain) if bargain > 0 else 0.0

    # Income tax + AMT (FICA/SDI are paycheck, not 1040 ES)
    fed_tax = float(year_tax.federal_income_tax or 0)
    state_tax = float(year_tax.state_tax or 0)
    expected_tax = fed_tax + state_tax + amt_due
    payroll = float(year_tax.total_fica or 0) + float(getattr(year_tax, 'sdi', 0) or 0)

    fed_wh_entered = entered_amount(getattr(profile, 'federal_withholding_ytd', None))
    state_wh_entered = entered_amount(getattr(profile, 'state_withholding_ytd', None))
    est_paid_entered = entered_amount(getattr(profile, 'estimated_payments_ytd', None))
    prior_tax_entered = entered_amount(getattr(profile, 'prior_year_total_tax', None))
    prior_agi = getattr(profile, 'prior_year_agi', None)
    try:
        prior_agi = float(prior_agi) if prior_agi is not None else None
    except (TypeError, ValueError):
        prior_agi = None

    wage_wh = wages_only_income_tax(
        cash_wages=cash, tax_year=tax_year, filing_status=filing, state_code=state
    )

    # Modeled supplemental on past vs remaining vests
    past_vest_wh = vest_paycheck_withholding(
        eq_past,
        ytd_supplemental_before=0.0,
        ytd_fica_wages_before=cash,
        tax_year=tax_year,
        filing_status=filing,
        state_code=state,
        ss_already_maxed=False,
        include_fica=True,
    )
    fut_vest_wh = vest_paycheck_withholding(
        eq_fut,
        ytd_supplemental_before=eq_past,
        ytd_fica_wages_before=cash + eq_past,
        tax_year=tax_year,
        filing_status=filing,
        state_code=state,
        ss_already_maxed=bool(eng.get('ss_wage_base_maxed')),
        include_fica=True,
    )

    # Locked-in withholding: paystub if entered, else wages-to-date model + past vest supp
    if fed_wh_entered is not None:
        fed_locked = fed_wh_entered
        fed_source = 'paystub'
    else:
        # Wages-only federal is full-year; attribute cash portion as locked if year is current
        fed_locked = float(wage_wh['federal']) + float(past_vest_wh['federal'])
        fed_source = 'modeled'
    if state_wh_entered is not None:
        state_locked = state_wh_entered
        state_source = 'paystub'
    else:
        state_locked = float(wage_wh['state']) + float(past_vest_wh['state'])
        state_source = 'modeled'

    # Still coming: remaining vest supplemental (not already in YTD stub)
    fed_coming = float(fut_vest_wh['federal'])
    state_coming = float(fut_vest_wh['state'])
    if fed_wh_entered is None:
        # modeled wage WH already full-year — don't double remaining cash
        pass
    else:
        # Remaining cash WH ≈ max(0, full-year wage federal − entered)
        fed_coming += max(0.0, float(wage_wh['federal']) - fed_wh_entered)
        state_coming += max(0.0, float(wage_wh['state']) - (state_wh_entered or 0))

    est_paid = est_paid_entered or 0.0
    expected_wh = fed_locked + state_locked + fed_coming + state_coming + est_paid

    under_over = expected_tax - expected_wh  # + underpaid, − overpaid
    still_to_pay_es = max(0.0, under_over)

    # Safe harbor vs April balance — 110% of prior-year tax (high AGI) vs 90% of current
    prior_tax, prior_tax_source = _prior_year_income_tax(
        user, tax_year, profile, prior_tax_entered
    )
    if prior_agi is None and prior_tax > 150_000:
        prior_agi = prior_tax  # conservative: treat as high-AGI if last year's tax was huge
    harbor = safe_harbor_targets(
        prior_year_total_tax=prior_tax,
        prior_year_agi=prior_agi if prior_agi is not None else (200_000 if prior_tax > 0 else None),
        current_year_estimated_tax=expected_tax,
    )
    ytd_for_harbor = fed_locked + state_locked + est_paid
    required = float(harbor.get('required_annual') or 0)
    no_penalty = required > 0 and ytd_for_harbor + 0.5 >= required
    april_balance = max(0.0, expected_tax - expected_wh)

    # Extra withholding per remaining vest $ of gross
    extra_per_vest = 0.0
    if eq_fut > 0 and still_to_pay_es > 0:
        extra_per_vest = still_to_pay_es  # dollars to add on remaining vests (W-4 extra)

    # --- Quarters: federal remaining equal among remaining due dates;
    # CA 30/40/0/30, skip Q3, roll unpaid past CA to next CA-positive quarter. ---
    fed_remain = max(0.0, fed_tax - fed_locked - fed_coming - est_paid * 0.7)
    ca_remain = max(0.0, state_tax - state_locked - state_coming - est_paid * 0.3)
    # If we can't split estimates, put them all against combined remain
    if est_paid_entered:
        combined_remain = max(0.0, expected_tax - expected_wh)
        fed_remain = combined_remain * (fed_tax / expected_tax) if expected_tax else 0.0
        ca_remain = combined_remain - fed_remain

    dues = federal_estimated_due_dates(tax_year)
    remaining_fed_idx = [i for i, q in enumerate(dues) if q['due'] > as_of]
    n_fed = max(1, len(remaining_fed_idx))
    fed_each = fed_remain / n_fed if remaining_fed_idx else 0.0

    ca_annual = max(0.0, state_tax)
    ca_targets = [ca_annual * f for f in CA_ES_FRACTIONS]
    # Amount already "due" in past CA-positive quarters — if unpaid, roll forward
    ca_paid_or_locked = state_locked + (est_paid * 0.3 if est_paid_entered else 0)
    ca_allocated = 0.0
    ca_payments = [0.0, 0.0, 0.0, 0.0]
    leftover = max(0.0, ca_annual - ca_paid_or_locked)
    for i, q in enumerate(dues):
        frac = CA_ES_FRACTIONS[i]
        if frac <= 0:
            ca_payments[i] = 0.0
            continue
        if q['due'] <= as_of:
            # past — don't bill again; leftover still rolls
            continue
        take = leftover  # dump remaining CA onto next positive installment
        ca_payments[i] = take
        leftover = 0.0
        break
    if leftover > 0:
        # last CA-positive slot
        for i in (3, 1, 0):
            if CA_ES_FRACTIONS[i] > 0:
                ca_payments[i] += leftover
                leftover = 0.0
                break

    quarters: List[Dict[str, Any]] = []
    marked_next = False
    for i, q in enumerate(dues):
        due = q['due']
        past = due <= as_of
        fed_pay = fed_each if (not past and i in remaining_fed_idx) else 0.0
        ca_pay = 0.0 if past else ca_payments[i]
        total_pay = fed_pay + ca_pay
        is_next = (not past) and (not marked_next)
        if is_next:
            marked_next = True
        if past:
            why = 'Due date passed. Remaining federal ES is on Sep 15 and Jan 15.'
        elif CA_ES_FRACTIONS[i] <= 0:
            why = 'CA 540-ES is 0% this quarter. Federal remainder only.'
        elif total_pay <= 0:
            why = 'Nothing left on this installment.'
        else:
            why = 'Federal remainder split across remaining due dates. CA is 30/40/0/30.'
        quarters.append({
            **q,
            'due_iso': due.isoformat(),
            'due_label': due.strftime('%b %d, %Y').replace(' 0', ' '),
            'is_past': past,
            'is_next': is_next,
            'federal_payment': round(fed_pay, 2),
            'ca_payment': round(ca_pay, 2),
            'ca_fraction': CA_ES_FRACTIONS[i],
            'suggested_payment': round(total_pay, 2),
            'why': why,
        })

    remaining_events = [
        e for e in (vest.get('events') or [])
        if e.get('is_future')
    ]

    sales_tax = 0.0
    try:
        from app.utils.estimated_tax_calendar import stacked_tax_on_sales
        stacked = stacked_tax_on_sales(user, None, tax_year)
        # Income tax on sales only (not FICA) — this is the per-lot EST. TAX total
        sales_tax = (
            float(stacked.get('federal_tax') or 0)
            + float(stacked.get('state_tax') or 0)
            + float(stacked.get('niit') or 0)
            + float(stacked.get('amt_due') or 0)
        )
    except Exception:
        sales_tax = 0.0
    vest_true_up = still_to_pay_es - sales_tax

    return {
        'tax_year': tax_year,
        'as_of': as_of.isoformat(),
        'expected_tax': round(expected_tax, 2),
        'tax_breakdown': {
            'federal': round(fed_tax, 2),
            'state': round(state_tax, 2),
            'amt': round(amt_due, 2),
            'payroll_fica_sdi': round(payroll, 2),
        },
        'iso_bargain': round(bargain, 2),
        'expected_withholding': round(expected_wh, 2),
        'withholding': {
            'federal_locked': round(fed_locked, 2),
            'state_locked': round(state_locked, 2),
            'federal_coming': round(fed_coming, 2),
            'state_coming': round(state_coming, 2),
            'estimated_payments': round(est_paid, 2),
            'federal_source': fed_source,
            'state_source': state_source,
            'federal_entered': fed_wh_entered is not None,
            'state_entered': state_wh_entered is not None,
            'estimates_entered': est_paid_entered is not None,
            'prior_tax_entered': prior_tax_entered is not None,
        },
        'locked_in': {
            'vest_gross': round(eq_past, 2),
            'sale_gain': round(stcg + ltcg, 2),
            'withholding': round(fed_locked + state_locked + est_paid, 2),
        },
        'still_coming': {
            'vest_gross': round(eq_fut, 2),
            'withholding': round(fed_coming + state_coming, 2),
            'events': remaining_events[:12],
            'note': 'Remaining vests at live price, supplemental withholding. Not cash due today.',
        },
        'under_over': round(under_over, 2),
        'still_to_save': round(still_to_pay_es, 2),
        'april_balance': round(april_balance, 2),
        'no_penalty': bool(no_penalty),
        'safe_harbor': harbor,
        'safe_harbor_line': _safe_harbor_line(
            tax_year=tax_year,
            prior_tax=prior_tax,
            prior_source=prior_tax_source,
            harbor=harbor,
            ytd_credits=ytd_for_harbor,
            no_penalty=no_penalty,
            april_balance=april_balance,
        ),
        'ytd_credits_for_harbor': round(ytd_for_harbor, 2),
        'sales_tax': round(sales_tax, 2),
        'vest_true_up': round(vest_true_up, 2),
        'set_aside_recon': {
            'sales': round(sales_tax, 2),
            'vest_true_up': round(vest_true_up, 2),
            'total': round(still_to_pay_es, 2),
        },
        'prior_tax': round(prior_tax, 2),
        'prior_tax_source': prior_tax_source,
        'extra_withholding_on_remaining_vests': round(extra_per_vest, 2),
        'quarters': quarters,
        'ca_es_note': CA_ES_SOURCE,
        'citations': CITATIONS,
        'cash_wages': round(cash, 2),
        'equity_vested_ytd': round(eq_past, 2),
        'equity_remaining_year': round(eq_fut, 2),
        'year_tax': year_tax.to_dict() if hasattr(year_tax, 'to_dict') else {},
    }
