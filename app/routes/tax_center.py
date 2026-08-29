"""
Sales & Tax Center — lot inventory, sale recording, what-if tax engine.
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Optional
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, Response
from flask_login import login_required, current_user

from app import db
from app.models.tax_profile import TaxProfile
from app.models.stock_sale import StockSale, ISOExercise
from app.models.vest_event import VestEvent
from app.models.grant import Grant, ShareType
from app.utils.lot_inventory import build_lots_for_user
from app.utils.tax_engine import LotSaleInput, analyze_sales
from app.utils.equity_planner import LotSpec, run_plan
from app.utils.goal_optimizer import GoalRequest, optimize_goal, parse_goal_heuristic
from app.utils import xai_advisor
from app.utils.price_utils import get_latest_user_price
from app.utils.tax_constants import TAX_TABLE_VERSION
import json
import logging
import traceback

logger = logging.getLogger(__name__)

tax_center_bp = Blueprint('tax_center', __name__, url_prefix='/tax')

ADVISOR_API_VERSION = TAX_TABLE_VERSION


def _iso(share_type: str) -> bool:
    return share_type in (ShareType.ISO_5Y.value, ShareType.ISO_6Y.value)


def _api_json(payload: dict, status: int = 200) -> Response:
    """Always return application/json (never HTML), even if payload has odd types."""
    payload = dict(payload or {})
    payload.setdefault('api_version', ADVISOR_API_VERSION)
    try:
        body = json.dumps(payload, default=str, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        # NaN/inf or other bad values — scrub
        body = json.dumps(
            {'success': False, 'error': 'Response serialization failed', 'code': 'json_encode',
             'api_version': ADVISOR_API_VERSION},
            ensure_ascii=False,
        )
        status = 500
    return Response(body, status=status, mimetype='application/json; charset=utf-8')


def _slim_engine_plan(payload: Optional[dict]) -> Optional[dict]:
    from app.utils.advisor_service import slim_engine_plan
    return slim_engine_plan(payload)


def _cash_vs_tax_api(cvt: Optional[dict]) -> Optional[dict]:
    """JSON-safe cash-vs-tax panel for /tax/api/year-tax."""
    if not cvt:
        return None
    out = dict(cvt)
    out.pop('year_tax', None)
    qs = []
    for q in out.get('quarters') or []:
        row = dict(q)
        due = row.get('due')
        if hasattr(due, 'isoformat'):
            row['due'] = due.isoformat()
        pe = row.get('period_end')
        if hasattr(pe, 'isoformat'):
            row['period_end'] = pe.isoformat()
        qs.append(row)
    out['quarters'] = qs
    return out


def _attach_year_tax_cash_vs_tax(payload: dict, year: int) -> dict:
    try:
        from app.utils.cash_vs_tax import build_cash_vs_tax
        cvt = _cash_vs_tax_api(build_cash_vs_tax(current_user, tax_year=year))
        payload['cash_vs_tax'] = cvt
        if cvt:
            payload['withholding'] = cvt.get('withholding')
            payload['under_over'] = cvt.get('under_over')
            payload['april_balance'] = cvt.get('april_balance')
            payload['safe_harbor'] = cvt.get('safe_harbor')
            payload['safe_harbor_line'] = cvt.get('safe_harbor_line')
            payload['set_aside_recon'] = cvt.get('set_aside_recon')
            payload['withholding_prompt'] = cvt.get('withholding_prompt')
            payload['withholding_is_modeled'] = cvt.get('withholding_is_modeled')
            payload['expected_tax'] = cvt.get('expected_tax')
            payload['expected_withholding'] = cvt.get('expected_withholding')
            payload['prior_year_tax'] = cvt.get('prior_year_tax') or cvt.get('prior_tax')
            payload['prior_tax_source'] = cvt.get('prior_tax_source')
            payload['prior_year_agi'] = cvt.get('prior_year_agi')
            payload['iso_bargain'] = cvt.get('iso_bargain')
            payload['amt_due'] = (cvt.get('tax_breakdown') or {}).get('amt')
            result = payload.get('result')
            if isinstance(result, dict):
                result['iso_bargain'] = cvt.get('iso_bargain')
                result['amt_due'] = payload['amt_due']
                result['espp_purchase_gross'] = (cvt.get('year_tax') or {}).get('vest_prefills', {}).get('espp_purchase_gross')
                if result.get('espp_purchase_gross') in (None, 0):
                    hist = payload.get('history') or {}
                    result['espp_purchase_gross'] = hist.get('espp_purchase_gross')
                    result.setdefault('rsu_vest_gross', hist.get('rsu_vest_gross'))
    except Exception as e:
        logger.warning('year-tax cash_vs_tax failed: %s', e)
        payload['cash_vs_tax'] = None
    return payload


# ensure date is available in template helpers
__all__ = ['tax_center_bp']


def _tax_page_context(user, tax_year=None):
    """Shared context for Plan (forward) and Activity (journal)."""
    profile = TaxProfile.for_user(user)
    lots = build_lots_for_user(user.id)
    sales = (
        StockSale.query.filter_by(user_id=user.id)
        .order_by(StockSale.sale_date.desc())
        .all()
    )
    exercises = (
        ISOExercise.query.filter_by(user_id=user.id)
        .order_by(ISOExercise.exercise_date.desc())
        .all()
    )
    live = float(get_latest_user_price(user.id) or 0.0)
    from app.utils.portfolio_summary import summarize_held_portfolio
    held = summarize_held_portfolio(user.id, live_price=live, lots=lots)
    rsu_held = held['rsu_held']
    iso_held = held['iso_held']
    iso_unex = held['iso_unexercised']
    available = held['shares_available']
    inventory = {
        'rsu_held': rsu_held,
        'iso_held': iso_held,
        'iso_unexercised': iso_unex,
        'shares_available': available,
        'sellable_value': held['sellable_value'],
        'held_value': held['held_value'],
        'portfolio_value': held['portfolio_value'],
        'unex_intrinsic': held['iso_unexercised_value'],
        'lt_lots': sum(1 for l in lots if l.get('is_long_term') and float(l.get('shares_available') or 0) > 0),
        'st_lots': sum(1 for l in lots if not l.get('is_long_term') and float(l.get('shares_available') or 0) > 0),
        'shares_sold_market': held['shares_sold_market'],
    }
    tax_year = int(tax_year or request.args.get('sold_year') or date.today().year)
    from app.utils.sold_portfolio import build_sold_portfolio
    from app.utils.estimated_tax_calendar import build_estimated_tax_calendar
    sold = build_sold_portfolio(user, live_price=live, tax_year=tax_year, sales=sales)
    tax_calendar = build_estimated_tax_calendar(
        user, tax_year=tax_year, sales=sales, profile=profile
    )
    inventory['tax_to_save'] = float(tax_calendar.get('still_to_save') or 0)
    cash_vs_tax = tax_calendar.get('cash_vs_tax')
    profile_ready = bool(float(profile.other_ordinary_income or 0) > 0)
    return dict(
        profile=profile,
        lots=lots,
        sales=sales,
        exercises=exercises,
        live_price=live,
        shares_available=available,
        inventory=inventory,
        sold=sold,
        tax_calendar=tax_calendar,
        cash_vs_tax=cash_vs_tax,
        sold_year=tax_year,
        profile_ready=profile_ready,
        today=date.today(),
        grok_enabled=xai_advisor.is_configured(user),
    )


@tax_center_bp.route('/')
@login_required
def hub():
    """Plan: goal optimizer and advanced what-if."""
    tab = (request.args.get('tab') or '').strip().lower()
    sold_year = request.args.get('sold_year')
    if tab in ('activity', 'sold') or (sold_year and tab not in ('optimize', 'scenario')):
        kwargs = {}
        if sold_year:
            kwargs['sold_year'] = sold_year
        return redirect(url_for('tax_center.activity', **kwargs))
    return render_template('tax/hub.html', **_tax_page_context(current_user))


@tax_center_bp.route('/activity')
@login_required
def activity():
    """Journal: record sales/exercises, ledgers, tax reserve."""
    return render_template('tax/activity.html', **_tax_page_context(current_user))


def _money_float(val, default=0.0):
    """Parse money from number, or string with $ / commas. default may be None."""
    if val is None or val == '':
        return default if default is None else float(default)
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace('$', '').replace(',', '').replace(' ', '')
    if not s or s in ('-', '.', '-.'):
        return default if default is None else float(default)
    try:
        return float(s)
    except (TypeError, ValueError):
        return default if default is None else float(default)


def _engine_profile_for_request(user, data: Optional[dict] = None) -> dict:
    """
    Year-scoped tax engine profile for analyze / goal / sales / exercises.

    Prefer TaxYearProfile for the request year so past-year plans never inherit
    current-year wages from the active TaxProfile mirror.
    """
    from app.utils.tax_engine import resolve_engine_profile_for_year

    data = data or {}
    main = TaxProfile.for_user(user)
    try:
        year = int(
            data.get('tax_year')
            or data.get('year')
            or main.tax_year
            or date.today().year
        )
    except (TypeError, ValueError):
        year = int(main.tax_year or date.today().year)

    eng = resolve_engine_profile_for_year(user, year)
    # Allow what-if overrides from the request without mutating DB
    if data.get('other_ordinary_income') is not None:
        try:
            v = float(data['other_ordinary_income'])
            eng['other_ordinary_income'] = v
            eng['other_ordinary_income_raw'] = v
            from app.utils.wage_year_tax import attach_computed_year_income
            attach_computed_year_income(eng, user.id, year)
        except (TypeError, ValueError):
            pass
    if data.get('filing_status'):
        eng['filing_status'] = data['filing_status']
    eng['tax_year'] = year
    return eng


@tax_center_bp.route('/api/year-tax', methods=['GET', 'POST'])
@login_required
def api_year_tax():
    """
    Year-centric tax profile package for soft year switching + live W-2 calc.

    GET  ?year=2024
         → years, form (saved TaxYearProfile or seeds), vest history, W-2 result
    POST { year, other_ordinary_income / wages, ... }
         → recompute W-2 from current form fields (no save)
    """
    try:
        from app.models.tax_year_profile import TaxYearProfile
        from app.utils.wage_year_tax import (
            build_year_vest_prefill,
            compute_w2_year_tax,
            list_years_with_vests,
            year_income_stack,
        )
        profile = TaxProfile.for_user(current_user)
        years = list_years_with_vests(current_user.id)
        if not years:
            years = list(range(date.today().year, date.today().year - 8, -1))

        def _seed_form(year: int) -> tuple:
            """
            Money fields come ONLY from TaxYearProfile for that year.
            Never pull another year's wages/YTD from main TaxProfile — that was
            inflating past-year estimates with current-year income.
            """
            year_row = TaxYearProfile.get_for(current_user.id, year)
            if year_row:
                return year_row.to_form_dict(), 'saved'
            # Flags/filing only from main profile; money always 0 until user enters
            form = {
                'tax_year': year,
                'filing_status': profile.filing_status or 'single',
                'state_code': profile.state_code or 'CA',
                'federal_ordinary_rate': None,
                'federal_ltcg_rate': None,
                'state_ordinary_rate': 0.0,
                'state_cg_rate': 0.0,
                'use_bracket_engine': bool(
                    profile.use_bracket_engine if profile.use_bracket_engine is not None else True
                ),
                'use_state_engine': bool(
                    profile.use_state_engine if profile.use_state_engine is not None else True
                ),
                'other_ordinary_income': 0.0,
                'ytd_wages': 0.0,
                'other_long_term_gains': 0.0,
                'other_short_term_gains': 0.0,
                'include_fica': bool(
                    profile.include_fica if profile.include_fica is not None else True
                ),
                'ss_wage_base_maxed': False,  # per-year; don't inherit mid-year flag
                'include_niit': bool(
                    profile.include_niit if profile.include_niit is not None else True
                ),
                'amt_credit_carryforward': 0.0,
                'ca_amt_credit_carryforward': 0.0,
            }
            # Only for the active planning year with no year-row yet: offer main
            # profile money as a starting point (same calendar year only).
            if profile.tax_year == year:
                form['other_ordinary_income'] = float(profile.other_ordinary_income or 0)
                form['ytd_wages'] = float(profile.ytd_wages or 0)
                if form['other_ordinary_income'] <= 0 and form['ytd_wages'] > 0:
                    form['other_ordinary_income'] = form['ytd_wages']
                form['other_long_term_gains'] = float(profile.other_long_term_gains or 0)
                form['other_short_term_gains'] = float(profile.other_short_term_gains or 0)
                form['federal_ordinary_rate'] = profile.federal_ordinary_rate
                form['federal_ltcg_rate'] = profile.federal_ltcg_rate
                form['state_ordinary_rate'] = profile.state_ordinary_rate or 0.0
                form['state_cg_rate'] = (
                    profile.state_cg_rate
                    if profile.state_cg_rate is not None
                    else (profile.state_ordinary_rate or 0)
                )
                form['ss_wage_base_maxed'] = bool(profile.ss_wage_base_maxed)
                form['amt_credit_carryforward'] = float(profile.amt_credit_carryforward or 0)
                form['ca_amt_credit_carryforward'] = float(profile.ca_amt_credit_carryforward or 0)
            return form, 'new'

        def _merge_inputs(base: dict, data: dict) -> dict:
            out = dict(base)
            out['tax_year'] = int(data.get('year') or data.get('tax_year') or out.get('tax_year') or date.today().year)
            if 'filing_status' in data and data['filing_status']:
                out['filing_status'] = data['filing_status']
            if 'state_code' in data and data['state_code'] is not None:
                out['state_code'] = str(data['state_code']).upper()[:2] or out.get('state_code')

            # Accept both API names and profile form names
            wages = data.get('other_ordinary_income', data.get('wages', data.get('other_ordinary')))
            if wages is not None and wages != '':
                out['other_ordinary_income'] = _money_float(wages, 0)
            stcg = data.get('other_short_term_gains', data.get('stcg'))
            if stcg is not None and stcg != '':
                out['other_short_term_gains'] = _money_float(stcg, 0)
            ltcg = data.get('other_long_term_gains', data.get('ltcg'))
            if ltcg is not None and ltcg != '':
                out['other_long_term_gains'] = _money_float(ltcg, 0)
            for k in (
                'amt_credit_carryforward',
                'ca_amt_credit_carryforward',
            ):
                if k in data and data[k] is not None and data[k] != '':
                    out[k] = _money_float(data[k], 0)

            for bk in ('include_fica', 'ss_wage_base_maxed', 'include_niit',
                       'use_bracket_engine', 'use_state_engine'):
                if bk in data:
                    out[bk] = bool(data[bk])

            # Optional % overrides (pass through as 0–1 or percent)
            for rk in ('federal_ordinary_rate', 'federal_ltcg_rate',
                       'state_ordinary_rate', 'state_cg_rate'):
                if rk in data and data[rk] not in (None, ''):
                    v = _money_float(data[rk], 0)
                    out[rk] = (v / 100.0) if v > 1 else v
            return out

        def _wage_bases(form: dict, history: Optional[dict] = None, stack: Optional[dict] = None) -> tuple:
            """
            Full-year stack: cash wages + VestX vests + sale capital gains.
            FICA follows wages + vests only (gains are not FICA).
            """
            if stack:
                return (
                    float(stack.get('ordinary') or 0),
                    float(stack.get('fica_wages') or 0),
                    float(stack.get('cash_wages') or 0),
                    float(stack.get('equity_vested_ytd') or 0),
                    float(stack.get('equity_remaining_year') or 0),
                    float(stack.get('stcg') or 0),
                    float(stack.get('ltcg') or 0),
                )
            ordinary = float(form.get('other_ordinary_income') or 0)
            hist = history or {}
            equity_past = float(hist.get('equity_vested_ytd') or 0)
            equity_future = float(hist.get('equity_remaining_year') or 0)
            stacked = ordinary + equity_past + equity_future
            stcg = float(form.get('other_short_term_gains') or 0)
            ltcg = float(form.get('other_long_term_gains') or 0)
            return stacked, stacked, ordinary, equity_past, equity_future, stcg, ltcg

        def _package(year: int, form: dict, source: str, history: dict, *, run: bool = True, stack: Optional[dict] = None):
            if year not in years:
                years_out = sorted(set(years) | {year}, reverse=True)
            else:
                years_out = years
            wages, fica_wages, box1, eq_past, eq_fut, stcg, ltcg = _wage_bases(form, history, stack)
            result = None
            tax_base = wages + stcg + ltcg
            if run and tax_base > 0:
                result = compute_w2_year_tax(
                    tax_year=year,
                    filing_status=form.get('filing_status') or 'single',
                    state_code=form.get('state_code') or 'CA',
                    wages=wages,
                    other_ordinary=0,
                    stcg=stcg,
                    ltcg=ltcg,
                    include_fica=bool(form.get('include_fica', True)),
                    ss_wage_base_maxed=bool(form.get('ss_wage_base_maxed')),
                    use_state_engine=bool(form.get('use_state_engine', True)),
                    vest_prefills=history,
                    fica_wages=fica_wages,
                ).to_dict()
                result['box1_wages'] = box1
                result['equity_vested_ytd'] = eq_past
                result['equity_remaining_year'] = eq_fut
                result['sale_stcg'] = float((stack or {}).get('sale_stcg') or 0)
                result['sale_ltcg'] = float((stack or {}).get('sale_ltcg') or 0)
                result['stacked_wages'] = wages
                result['tax_base'] = tax_base
                result['iso_bargain'] = float((history or {}).get('iso_bargain') or 0)
                result['iso_bargain_source'] = (history or {}).get('iso_bargain_source') or 'none'
                result['espp_purchase_gross'] = float((history or {}).get('espp_purchase_gross') or 0)
                result['rsu_vest_gross'] = float((history or {}).get('rsu_vest_gross') or 0)
            # Client-friendly form: rates as percent for override fields
            form_out = dict(form)
            form_out['tax_year'] = year
            for rk in ('federal_ordinary_rate', 'federal_ltcg_rate',
                       'state_ordinary_rate', 'state_cg_rate'):
                v = form_out.get(rk)
                if v is not None and v != '':
                    try:
                        fv = float(v)
                        form_out[rk + '_pct'] = round(fv * 100.0, 4) if fv <= 1 else round(fv, 4)
                    except (TypeError, ValueError):
                        form_out[rk + '_pct'] = None
                else:
                    form_out[rk + '_pct'] = None
            return {
                'success': True,
                'year': year,
                'years': years_out,
                'source': source,
                'form': form_out,
                'history': history,
                'result': result,
                # legacy keys for any older clients
                'inputs': {
                    'wages': wages,
                    'fica_wages': fica_wages,
                    'other_ordinary': 0,
                    'stcg': stcg,
                    'ltcg': ltcg,
                    'filing_status': form.get('filing_status') or 'single',
                    'state_code': (form.get('state_code') or 'CA').upper(),
                    'include_fica': bool(form.get('include_fica', True)),
                    'ss_wage_base_maxed': bool(form.get('ss_wage_base_maxed')),
                },
            }

        def _stack_form(yr: int, frm: dict) -> dict:
            return year_income_stack(
                current_user.id,
                yr,
                cash_wages=float(frm.get('other_ordinary_income') or 0),
                other_stcg=float(frm.get('other_short_term_gains') or 0),
                other_ltcg=float(frm.get('other_long_term_gains') or 0),
            )

        if request.method == 'GET':
            year = int(request.args.get('year') or profile.tax_year or date.today().year)
            form, source = _seed_form(year)
            stack = _stack_form(year, form)
            history = dict(stack.get('vest') or {})
            history['sale_stcg'] = stack.get('sale_stcg')
            history['sale_ltcg'] = stack.get('sale_ltcg')
            history['sale_count'] = stack.get('sale_count')
            return _api_json(_attach_year_tax_cash_vs_tax(
                _package(year, form, source, history, run=True, stack=stack), year
            ))

        data = request.get_json(silent=True) or {}
        year = int(data.get('year') or data.get('tax_year') or date.today().year)
        form, source = _seed_form(year)
        form = _merge_inputs(form, data)
        stack = _stack_form(year, form)
        history = dict(stack.get('vest') or {})
        history['sale_stcg'] = stack.get('sale_stcg')
        history['sale_ltcg'] = stack.get('sale_ltcg')
        history['sale_count'] = stack.get('sale_count')
        if any(k in data for k in (
            'other_ordinary_income', 'wages',
            'other_short_term_gains', 'other_long_term_gains', 'stcg', 'ltcg',
        )):
            source = 'draft' if source == 'new' else 'saved'
        return _api_json(_attach_year_tax_cash_vs_tax(
            _package(year, form, source, history, run=True, stack=stack), year
        ))
    except Exception as e:
        logger.error('year-tax failed: %s', e, exc_info=True)
        return _api_json({
            'success': False,
            'error': str(e),
            'detail': traceback.format_exc()[-1200:],
        }, 500)


@tax_center_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def tax_profile():
    """
    Year-centric tax profile. Dropdown selects calendar year; form + W-2 estimate
    are for that year only. Save stores TaxYearProfile and activates it for planning.
    """
    from app.models.tax_year_profile import TaxYearProfile
    from app.utils.wage_year_tax import (
        build_year_vest_prefill,
        compute_w2_year_tax,
        list_years_with_vests,
        year_income_stack,
    )

    profile = TaxProfile.for_user(current_user)
    years = list_years_with_vests(current_user.id)
    if not years:
        years = list(range(date.today().year, date.today().year - 8, -1))

    def _f(name, default=None):
        raw = request.form.get(name, '') if request.method == 'POST' else ''
        if raw is None or str(raw).strip() == '':
            return default
        return _money_float(raw, default if default is not None else 0)

    if request.method == 'POST':
        try:
            ty = int(request.form.get('tax_year') or date.today().year)
            fo = request.form.get('federal_ordinary_rate')
            fl = request.form.get('federal_ltcg_rate')
            fo_n = _money_float(fo, None) if fo not in (None, '') else None
            fl_n = _money_float(fl, None) if fl not in (None, '') else None
            data = {
                'filing_status': request.form.get('filing_status') or 'single',
                'state_code': (request.form.get('state_code') or '').upper()[:2] or None,
                'use_bracket_engine': request.form.get('use_bracket_engine') == 'on',
                'use_state_engine': request.form.get('use_state_engine') == 'on',
                'federal_ordinary_rate': (fo_n / 100.0) if fo_n is not None else None,
                'federal_ltcg_rate': (fl_n / 100.0) if fl_n is not None else None,
                'state_ordinary_rate': (_f('state_ordinary_rate', 0) or 0) / 100.0,
                'state_cg_rate': (_f('state_cg_rate', 0) or 0) / 100.0,
                'other_ordinary_income': _f('other_ordinary_income', 0) or 0,
                'other_long_term_gains': _f('other_long_term_gains', 0) or 0,
                'other_short_term_gains': _f('other_short_term_gains', 0) or 0,
                'amt_credit_carryforward': _f('amt_credit_carryforward', 0) or 0,
                'ca_amt_credit_carryforward': _f('ca_amt_credit_carryforward', 0) or 0,
                'include_fica': request.form.get('include_fica') == 'on',
                'ss_wage_base_maxed': request.form.get('ss_wage_base_maxed') == 'on',
                'include_niit': request.form.get('include_niit') == 'on',
            }
            stack = year_income_stack(
                current_user.id,
                ty,
                cash_wages=data['other_ordinary_income'],
                other_stcg=data['other_short_term_gains'],
                other_ltcg=data['other_long_term_gains'],
            )
            data['ytd_wages'] = float(data['other_ordinary_income'] or 0)
            year_row = TaxYearProfile.upsert_from_form(current_user.id, ty, data)
            year_row.apply_to_main_profile(profile)

            # Estimated-tax calendar inputs live on main TaxProfile (cross-year)
            profile.prior_year_total_tax = _f('prior_year_total_tax', 0) or 0
            pagi = request.form.get('prior_year_agi', '')
            profile.prior_year_agi = _money_float(pagi, None) if pagi not in (None, '') else None
            profile.federal_withholding_ytd = _f('federal_withholding_ytd', 0) or 0
            profile.state_withholding_ytd = _f('state_withholding_ytd', 0) or 0
            profile.estimated_payments_ytd = _f('estimated_payments_ytd', 0) or 0
            profile.itemize_salt = _f('itemize_salt', 0) or 0
            profile.itemize_mortgage = _f('itemize_mortgage', 0) or 0
            profile.itemize_charity = _f('itemize_charity', 0) or 0

            if profile.federal_ordinary_rate is not None:
                current_user.federal_tax_rate = profile.federal_ordinary_rate
            current_user.state_tax_rate = profile.state_ordinary_rate or 0
            current_user.include_fica = profile.include_fica
            current_user.ss_wage_base_maxed = profile.ss_wage_base_maxed

            db.session.commit()
            flash(f'{ty} tax profile saved and set as active for planning.', 'success')
            return redirect(url_for('tax_center.tax_profile', year=ty))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving profile: {e}', 'danger')
            ty = int(request.form.get('tax_year') or date.today().year)
            return redirect(url_for('tax_center.tax_profile', year=ty))

    # GET — year dropdown drives entire page
    try:
        selected_year = int(request.args.get('year') or profile.tax_year or date.today().year)
    except (TypeError, ValueError):
        selected_year = date.today().year
    if selected_year not in years:
        years = sorted(set(years) | {selected_year}, reverse=True)

    year_row = TaxYearProfile.get_for(current_user.id, selected_year)
    if year_row:
        form = year_row.to_form_dict()
        source = 'saved'
    else:
        # Money only from this year (or main profile if same active year). Never
        # copy another year's wages into a blank past year.
        form = {
            'tax_year': selected_year,
            'filing_status': profile.filing_status or 'single',
            'state_code': profile.state_code or 'CA',
            'federal_ordinary_rate': None,
            'federal_ltcg_rate': None,
            'state_ordinary_rate': 0.0,
            'state_cg_rate': 0.0,
            'use_bracket_engine': bool(profile.use_bracket_engine if profile.use_bracket_engine is not None else True),
            'use_state_engine': bool(profile.use_state_engine if profile.use_state_engine is not None else True),
            'other_ordinary_income': 0.0,
            'ytd_wages': 0.0,
            'other_long_term_gains': 0.0,
            'other_short_term_gains': 0.0,
            'include_fica': bool(profile.include_fica if profile.include_fica is not None else True),
            'ss_wage_base_maxed': False,
            'include_niit': bool(profile.include_niit if profile.include_niit is not None else True),
            'amt_credit_carryforward': 0.0,
            'ca_amt_credit_carryforward': 0.0,
        }
        if profile.tax_year == selected_year:
            form['other_ordinary_income'] = float(profile.other_ordinary_income or 0)
            form['ytd_wages'] = float(profile.ytd_wages or 0)
            form['other_long_term_gains'] = float(profile.other_long_term_gains or 0)
            form['other_short_term_gains'] = float(profile.other_short_term_gains or 0)
            form['federal_ordinary_rate'] = profile.federal_ordinary_rate
            form['federal_ltcg_rate'] = profile.federal_ltcg_rate
            form['state_ordinary_rate'] = profile.state_ordinary_rate or 0.0
            form['state_cg_rate'] = (
                profile.state_cg_rate if profile.state_cg_rate is not None else (profile.state_ordinary_rate or 0)
            )
            form['ss_wage_base_maxed'] = bool(profile.ss_wage_base_maxed)
            form['amt_credit_carryforward'] = float(profile.amt_credit_carryforward or 0)
            form['ca_amt_credit_carryforward'] = float(profile.ca_amt_credit_carryforward or 0)
        source = 'new'

    if float(form.get('other_ordinary_income') or 0) <= 0 and float(form.get('ytd_wages') or 0) > 0:
        form['other_ordinary_income'] = float(form['ytd_wages'])

    stack = year_income_stack(
        current_user.id,
        selected_year,
        cash_wages=float(form.get('other_ordinary_income') or 0),
        other_stcg=float(form.get('other_short_term_gains') or 0),
        other_ltcg=float(form.get('other_long_term_gains') or 0),
    )
    history = dict(stack.get('vest') or {})
    history['sale_stcg'] = stack.get('sale_stcg')
    history['sale_ltcg'] = stack.get('sale_ltcg')
    history['sale_count'] = stack.get('sale_count')
    box1 = float(stack.get('cash_wages') or 0)
    eq_past = float(stack.get('equity_vested_ytd') or 0)
    eq_fut = float(stack.get('equity_remaining_year') or 0)
    stacked = float(stack.get('ordinary') or 0)
    stcg = float(stack.get('stcg') or 0)
    ltcg = float(stack.get('ltcg') or 0)
    year_tax = None
    if stacked + stcg + ltcg > 0:
        try:
            year_tax = compute_w2_year_tax(
                tax_year=selected_year,
                filing_status=form['filing_status'],
                state_code=form.get('state_code') or 'CA',
                wages=stacked,
                other_ordinary=0,
                stcg=stcg,
                ltcg=ltcg,
                include_fica=form['include_fica'],
                ss_wage_base_maxed=form['ss_wage_base_maxed'],
                use_state_engine=form['use_state_engine'],
                vest_prefills=history,
                fica_wages=float(stack.get('fica_wages') or stacked),
                itemize_salt=float(getattr(profile, 'itemize_salt', 0) or 0),
                itemize_mortgage=float(getattr(profile, 'itemize_mortgage', 0) or 0),
                itemize_charity=float(getattr(profile, 'itemize_charity', 0) or 0),
            ).to_dict()
            year_tax['box1_wages'] = box1
            year_tax['equity_vested_ytd'] = eq_past
            year_tax['equity_remaining_year'] = eq_fut
            year_tax['sale_stcg'] = float(stack.get('sale_stcg') or 0)
            year_tax['sale_ltcg'] = float(stack.get('sale_ltcg') or 0)
            year_tax['stacked_wages'] = stacked
            year_tax['tax_base'] = float(stack.get('tax_base') or 0)
        except Exception as e:
            logger.warning('year tax display failed: %s', e)

    est_tax_form = {
        'prior_year_total_tax': float(getattr(profile, 'prior_year_total_tax', 0) or 0),
        'prior_year_agi': getattr(profile, 'prior_year_agi', None),
        'federal_withholding_ytd': float(getattr(profile, 'federal_withholding_ytd', 0) or 0),
        'state_withholding_ytd': float(getattr(profile, 'state_withholding_ytd', 0) or 0),
        'estimated_payments_ytd': float(getattr(profile, 'estimated_payments_ytd', 0) or 0),
        'prior_tax_source': 'entered' if (getattr(profile, 'prior_year_total_tax', 0) or 0) else None,
    }
    form['itemize_salt'] = float(getattr(profile, 'itemize_salt', 0) or 0)
    form['itemize_mortgage'] = float(getattr(profile, 'itemize_mortgage', 0) or 0)
    form['itemize_charity'] = float(getattr(profile, 'itemize_charity', 0) or 0)

    cash_vs_tax = None
    try:
        from app.utils.cash_vs_tax import build_cash_vs_tax
        cash_vs_tax = build_cash_vs_tax(
            current_user, tax_year=selected_year, profile=profile
        )
        if year_tax is not None and cash_vs_tax:
            amt = float((cash_vs_tax.get('tax_breakdown') or {}).get('amt') or 0)
            year_tax['amt_due'] = amt
            year_tax['total_tax'] = float(year_tax.get('total_tax') or 0) + amt
        # 2026 tab: show 2025 year-tax total/AGI when those fields were never saved here
        if cash_vs_tax:
            if not (est_tax_form.get('prior_year_total_tax') or 0):
                est_tax_form['prior_year_total_tax'] = float(
                    cash_vs_tax.get('prior_year_tax') or cash_vs_tax.get('prior_tax') or 0
                )
                est_tax_form['prior_tax_source'] = cash_vs_tax.get('prior_tax_source')
            if est_tax_form.get('prior_year_agi') in (None, 0, 0.0):
                if cash_vs_tax.get('prior_year_agi'):
                    est_tax_form['prior_year_agi'] = cash_vs_tax.get('prior_year_agi')
    except Exception as e:
        logger.warning('cash vs tax panel failed: %s', e)

    return render_template(
        'tax/profile.html',
        profile=profile,
        form=form,
        est_tax_form=est_tax_form,
        cash_vs_tax=cash_vs_tax,
        selected_year=selected_year,
        years=years,
        history=history,
        year_tax=year_tax,
        source=source,
        year=date.today().year,
    )


@tax_center_bp.route('/api/lots')
@login_required
def api_lots():
    return jsonify({'lots': build_lots_for_user(current_user.id)})


def _lot_specs_from_request(data: dict, user_id: int) -> list:
    """Build LotSpec list from API lots[] payload + DB grant/vest/exercise truth."""
    from app.utils.shares import whole_shares

    items = data.get('lots') or []
    specs = []
    for item in items:
        vest_id = int(item['vest_event_id'])
        shares = float(whole_shares(item.get('shares') or 0))
        if shares <= 0:
            continue
        vest = VestEvent.query.join(Grant).filter(
            VestEvent.id == vest_id, Grant.user_id == user_id
        ).first()
        if not vest:
            raise ValueError(f'Vest {vest_id} not found')
        grant = vest.grant
        is_iso = _iso(grant.share_type)

        ex_date = item.get('exercise_date')
        fmv_ex = item.get('fmv_at_exercise')
        if is_iso:
            ex = (
                ISOExercise.query.filter_by(user_id=user_id, vest_event_id=vest_id)
                .order_by(ISOExercise.exercise_date.desc())
                .first()
            )
            if ex:
                if not ex_date:
                    ex_date = ex.exercise_date.isoformat() if ex.exercise_date else None
                if fmv_ex is None:
                    fmv_ex = ex.fmv_at_exercise

        specs.append(
            LotSpec(
                vest_event_id=vest.id,
                grant_id=grant.id,
                share_type=grant.share_type,
                grant_type=grant.grant_type,
                is_iso=is_iso,
                shares=shares,
                vest_date=vest.vest_date,
                grant_date=grant.grant_date,
                strike_price=grant.share_price_at_grant if is_iso else 0.0,
                cost_basis_per_share=(
                    grant.share_price_at_grant if is_iso else (vest.share_price_at_vest or 0)
                ),
                exercise_date=(
                    datetime.fromisoformat(ex_date).date() if ex_date else None
                ),
                fmv_at_exercise=float(fmv_ex) if fmv_ex is not None else None,
                label=f"{grant.share_type} {vest.vest_date}",
                commission=float(item.get('commission', 0) or 0),
            )
        )
    return specs


@tax_center_bp.route('/api/analyze', methods=['POST'])
@login_required
def api_analyze():
    """
    What-if planner.

    Preferred: strategy-aware planning via equity_planner (exercise ≠ sale).
    Legacy body { lots, sale_date, sale_price, assume_same_day_exercise } still works
    and maps to strategy auto / cashless.
    """
    try:
        data = request.get_json() or {}
        eng = _engine_profile_for_request(current_user, data)

        sale_date_raw = data.get('sale_date') or date.today().isoformat()
        sale_date = datetime.fromisoformat(sale_date_raw).date()
        ex_date_raw = data.get('exercise_date') or sale_date_raw
        exercise_date = datetime.fromisoformat(ex_date_raw).date()

        sale_price = float(
            data.get('sale_price')
            if data.get('sale_price') is not None
            else (get_latest_user_price(current_user.id) or 0)
        )
        exercise_fmv = float(
            data.get('exercise_fmv')
            if data.get('exercise_fmv') is not None
            else (data.get('fmv_at_exercise') if data.get('fmv_at_exercise') is not None else sale_price)
        )

        strategy = (data.get('strategy') or '').strip().lower()
        if not strategy:
            # Back-compat: old checkbox
            if data.get('assume_same_day_exercise', True):
                strategy = 'auto'
            else:
                strategy = 'auto'

        specs = _lot_specs_from_request(data, current_user.id)
        if not specs and strategy not in ('compare', 'compare_iso', 'iso_compare'):
            return jsonify({'error': 'Select lots and enter share quantities.'}), 400

        result = run_plan(
            eng,
            specs,
            strategy=strategy,
            sale_date=sale_date,
            sale_price=sale_price,
            exercise_date=exercise_date,
            exercise_fmv=exercise_fmv,
            cover_strike=data.get('cover_strike', True) is not False,
            cover_tax=data.get('cover_tax', True) is not False,
        )

        # Back-compat: surface primary analysis at top level for simple clients
        if not result.get('compare') and result.get('plan'):
            plan = result['plan']
            result['analysis'] = plan.get('analysis')
            result['scenario'] = plan

        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error('analyze failed: %s', e, exc_info=True)
        return jsonify({'error': str(e)}), 400


@tax_center_bp.route('/api/plan', methods=['POST'])
@login_required
def api_plan():
    """Alias for strategy planner (same body as /api/analyze)."""
    return api_analyze()


def _goal_from_payload(data: dict, live_price: float) -> GoalRequest:
    def _date(key, default=None):
        raw = data.get(key)
        if not raw:
            return default
        return datetime.fromisoformat(str(raw)).date()

    price = data.get('sale_price')
    if price is None:
        price = live_price
    fmv = data.get('exercise_fmv')
    if fmv is None:
        fmv = price

    return GoalRequest(
        target_net_cash=(
            float(data['target_net_cash'])
            if data.get('target_net_cash') not in (None, '')
            else None
        ),
        objective=(data.get('objective') or 'min_tax').strip().lower(),
        sale_price=float(price or 0),
        sale_date=_date('sale_date', date.today()),
        exercise_date=_date('exercise_date', _date('sale_date', date.today())),
        exercise_fmv=float(fmv or 0),
        allow_rsu=data.get('allow_rsu', True) is not False,
        allow_iso_sell_held=data.get('allow_iso_sell_held', True) is not False,
        allow_iso_cashless=data.get('allow_iso_cashless', True) is not False,
        allow_iso_exercise_hold=bool(data.get('allow_iso_exercise_hold')),
        exercise_all_iso=bool(data.get('exercise_all_iso')),
        iso_max_exercise=(
            float(data['iso_max_exercise'])
            if data.get('iso_max_exercise') not in (None, '')
            else None
        ),
        iso_prefer_hold_fraction=(
            float(data['iso_prefer_hold_fraction'])
            if data.get('iso_prefer_hold_fraction') not in (None, '')
            else None
        ),
        max_tax=(
            float(data['max_tax']) if data.get('max_tax') not in (None, '') else None
        ),
        raw_text=data.get('raw_text') or data.get('prompt') or '',
    )


@tax_center_bp.route('/api/goal', methods=['POST'])
@login_required
def api_goal():
    """
    Goal-based optimizer: e.g. net $500k after tax with min tax, SpecID lots.

    Optional natural language in `prompt` — parsed by Grok when XAI_API_KEY is set,
    else heuristic parser.
    """
    try:
        data = request.get_json() or {}
        eng = _engine_profile_for_request(current_user, data)
        live = get_latest_user_price(current_user.id) or 0.0
        try:
            from app.utils.vest_basis import recompute_user_vest_fmv
            recompute_user_vest_fmv(current_user.id)
        except Exception:
            pass
        lots = build_lots_for_user(current_user.id)

        prompt = (data.get('prompt') or data.get('raw_text') or '').strip()
        # Default: no Grok explanation (saves tokens). Opt-in via explain=true.
        explain = bool(data.get('explain', False))

        goal = _goal_from_payload(data, live)
        parse_meta = {'source': 'form', 'interpretation': None, 'clarifications': []}

        if prompt:
            goal.raw_text = prompt
            defaults = {
                'sale_price': goal.sale_price,
                'sale_date': goal.sale_date,
                'exercise_date': goal.exercise_date,
                'exercise_fmv': goal.exercise_fmv,
            }
            # Always try free heuristic first (0 tokens)
            heur = parse_goal_heuristic(prompt, defaults)
            if heur.target_net_cash is not None and goal.target_net_cash is None:
                goal.target_net_cash = heur.target_net_cash
            goal.objective = heur.objective or goal.objective
            goal.allow_iso_cashless = heur.allow_iso_cashless
            goal.allow_iso_exercise_hold = (
                goal.allow_iso_exercise_hold or heur.allow_iso_exercise_hold
            )
            # Form toggle wins when checked; prompt can still turn it on
            goal.exercise_all_iso = bool(goal.exercise_all_iso) or bool(heur.exercise_all_iso)
            if goal.exercise_all_iso:
                goal.allow_iso_exercise_hold = True
                goal.allow_iso_cashless = False
                goal.iso_prefer_hold_fraction = 1.0
            elif heur.iso_prefer_hold_fraction is not None:
                goal.iso_prefer_hold_fraction = heur.iso_prefer_hold_fraction
            parse_meta = {'source': 'heuristic', 'interpretation': None}

            # Grok parse only if still ambiguous AND user opted in
            need_grok_parse = (
                bool(data.get('use_grok_parse'))
                and xai_advisor.is_configured(current_user)
                and goal.target_net_cash is None
                and len(prompt) > 20
            )
            if need_grok_parse:
                try:
                    parsed = xai_advisor.parse_goal_with_grok(
                        prompt,
                        inventory_summary='(compact; heuristic already applied)',
                        profile_summary=xai_advisor.summarize_profile(eng),
                        defaults=defaults,
                        user=current_user,
                    )
                    parse_meta = {
                        'source': 'grok',
                        'interpretation': parsed.get('interpretation'),
                        'clarifications': parsed.get('clarifications') or [],
                    }
                    if parsed.get('target_net_cash') is not None:
                        goal.target_net_cash = float(parsed['target_net_cash'])
                    if parsed.get('objective'):
                        goal.objective = str(parsed['objective'])
                    for flag in (
                        'allow_rsu',
                        'allow_iso_sell_held',
                        'allow_iso_cashless',
                        'allow_iso_exercise_hold',
                        'exercise_all_iso',
                    ):
                        if flag in parsed and parsed[flag] is not None:
                            setattr(goal, flag, bool(parsed[flag]))
                    if parsed.get('iso_prefer_hold_fraction') is not None:
                        try:
                            goal.iso_prefer_hold_fraction = float(
                                parsed['iso_prefer_hold_fraction']
                            )
                        except (TypeError, ValueError):
                            pass
                except Exception as e:
                    logger.warning('Grok parse failed: %s', e)
                    parse_meta = {'source': 'heuristic_fallback', 'error': str(e)}

        result = optimize_goal(eng, lots, goal)
        payload = result.to_dict()
        payload['parse'] = parse_meta

        if explain and xai_advisor.is_configured(current_user) and (
            prompt or result.picks
        ):
            try:
                # Compact inventory: only picks + short profile (not full lot dump)
                inv_compact = '; '.join(
                    f"v{p.vest_event_id}:{p.action}:{p.shares:.2f}sh"
                    for p in (result.picks or [])[:15]
                ) or 'no picks'
                payload['explanation'] = xai_advisor.explain_plan_with_grok(
                    user_request=prompt or f"Net ${goal.target_net_cash or 0:,.0f} minimize tax",
                    plan=payload,
                    profile_summary=xai_advisor.summarize_profile(eng),
                    inventory_summary=f'picks: {inv_compact}',
                    user=current_user,
                )
            except Exception as e:
                logger.warning('Grok explain failed: %s', e)
                payload['explanation'] = None
                payload['explanation_error'] = str(e)
        else:
            payload['explanation'] = None
            if not xai_advisor.is_configured(current_user):
                payload['explanation_note'] = (
                    'Add your xAI API key under Settings (encrypted on your profile) '
                    'to enable Grok explanations. Goal math still works without it.'
                )

        payload['grok_enabled'] = xai_advisor.is_configured(current_user)
        return _api_json({'success': True, **payload})
    except Exception as e:
        logger.error('goal optimize failed: %s', e, exc_info=True)
        return _api_json({'success': False, 'error': str(e)}, status=400)


@tax_center_bp.route('/api/ping', methods=['GET', 'POST'])
@login_required
def api_ping():
    """Health check for chat client — proves deploy + auth + JSON path."""
    try:
        has_key = False
        try:
            has_key = xai_advisor.is_configured(current_user)
        except Exception:
            has_key = False
        return _api_json({
            'success': True,
            'pong': True,
            'api_ok': True,
            'user_id': current_user.id,
            'grok_enabled': has_key,
            'phase': 'ping',
        })
    except Exception as e:
        return _api_json({
            'success': False,
            'error': str(e),
            'api_ok': False,
            'phase': 'ping_fail',
        }, 500)


@tax_center_bp.route('/api/advisor/jobs', methods=['POST'])
@login_required
def api_advisor_enqueue():
    """
    Async advisor: enqueue a job and return immediately (HTTP 202).

    Body: { messages: [{role, content}], plan?: object, force_grok?: bool }
    Response: { job_id, status: 'queued', poll_url }

    Background thread runs engines/Grok; poll GET /api/advisor/jobs/<id>.
    This never blocks page navigation — the HTTP worker is free after enqueue.
    """
    try:
        data = request.get_json(silent=True) or {}
        messages = data.get('messages') or []
        if not messages and data.get('message'):
            messages = [{'role': 'user', 'content': str(data['message'])}]
        if not messages:
            return _api_json({
                'success': False,
                'error': 'messages required',
                'code': 'bad_request',
                'phase': 'validate',
            }, 400)

        plan = data.get('plan') if isinstance(data.get('plan'), dict) else None
        from app.utils.advisor_jobs import enqueue_advisor_job, job_public_payload
        job = enqueue_advisor_job(
            user_id=current_user.id,
            messages=messages,
            plan=plan,
            force_grok=bool(data.get('force_grok')),
        )
        payload = job_public_payload(job)
        payload.update({
            'success': True,
            'api_ok': True,
            'async': True,
            'phase': 'queued',
            'poll_url': url_for('tax_center.api_advisor_job_status', job_id=job.id),
        })
        return _api_json(payload, 202)
    except Exception as e:
        logger.error('advisor enqueue failed: %s', e, exc_info=True)
        return _api_json({
            'success': False,
            'error': str(e),
            'code': 'enqueue_error',
            'phase': 'enqueue',
            'api_ok': False,
            'detail': traceback.format_exc()[-1200:],
        }, 500)


@tax_center_bp.route('/api/advisor/jobs/<job_id>', methods=['GET'])
@login_required
def api_advisor_job_status(job_id: str):
    """Poll job status. Lightweight — safe to call every few hundred ms."""
    try:
        from app.utils.advisor_jobs import get_job_for_user, job_public_payload
        job = get_job_for_user(job_id, current_user.id)
        if not job:
            return _api_json({
                'success': False,
                'error': 'Job not found',
                'code': 'not_found',
                'phase': 'poll',
            }, 404)
        payload = job_public_payload(job)
        payload['success'] = True
        payload['api_ok'] = True
        payload['async'] = True
        # Flatten result fields for the chat client when done
        if job.status == 'done' and payload.get('result'):
            result = payload['result']
            payload['reply'] = result.get('reply')
            payload['engine_plan'] = result.get('engine_plan')
            payload['context_meta'] = result.get('context_meta')
            payload['used_grok'] = result.get('used_grok')
            payload['grok_enabled'] = result.get('grok_enabled')
            payload['phase'] = result.get('phase') or job.phase
            payload['result_success'] = result.get('success')
            if result.get('success') is False and not result.get('reply'):
                payload['error'] = result.get('error') or job.error
        elif job.status == 'error':
            payload['error'] = job.error or 'Job failed'
            if payload.get('result'):
                payload['reply'] = payload['result'].get('reply')
                payload['engine_plan'] = payload['result'].get('engine_plan')
        elif job.status == 'cancelled':
            payload['error'] = job.error or 'Cancelled by user'
            payload['cancelled'] = True
            payload['phase'] = 'cancelled'
        return _api_json(payload)
    except Exception as e:
        logger.error('advisor poll failed: %s', e, exc_info=True)
        return _api_json({
            'success': False,
            'error': str(e),
            'code': 'poll_error',
            'phase': 'poll',
            'api_ok': False,
        }, 500)


@tax_center_bp.route('/api/advisor/jobs/<job_id>/cancel', methods=['POST'])
@login_required
def api_advisor_job_cancel(job_id: str):
    """Cancel a queued/running advisor job so the chat UI can free itself."""
    try:
        from app.utils.advisor_jobs import cancel_job_for_user, job_public_payload
        job = cancel_job_for_user(job_id, current_user.id)
        if not job:
            return _api_json({
                'success': False,
                'error': 'Job not found',
                'code': 'not_found',
                'phase': 'cancel',
            }, 404)
        payload = job_public_payload(job)
        payload.update({
            'success': True,
            'api_ok': True,
            'cancelled': job.status == 'cancelled' or job.status in ('done', 'error'),
            'phase': job.phase or job.status,
        })
        return _api_json(payload)
    except Exception as e:
        logger.error('advisor cancel failed: %s', e, exc_info=True)
        return _api_json({
            'success': False,
            'error': str(e),
            'code': 'cancel_error',
            'phase': 'cancel',
            'api_ok': False,
        }, 500)


@tax_center_bp.route('/api/advisor', methods=['POST'])
@login_required
def api_advisor():
    """
    Sync advisor (compat). Prefer POST /api/advisor/jobs for non-blocking UX.

    Body: { messages, plan?, force_grok?, async?: true }
    If async is true (default for new clients via jobs endpoint), enqueue instead.
    """
    try:
        data = request.get_json(silent=True) or {}
        # Opt-in async on this path too (default false for backward compat)
        if data.get('async') is True or data.get('background') is True:
            return api_advisor_enqueue()

        messages = data.get('messages') or []
        if not messages and data.get('message'):
            messages = [{'role': 'user', 'content': str(data['message'])}]
        if not messages:
            return _api_json({
                'success': False,
                'error': 'messages required',
                'code': 'bad_request',
                'phase': 'validate',
            }, 400)

        plan = data.get('plan') if isinstance(data.get('plan'), dict) else None
        from app.utils.advisor_service import run_advisor_turn
        result = run_advisor_turn(
            user_id=current_user.id,
            messages=messages,
            plan=plan,
            force_grok=bool(data.get('force_grok')),
        )
        status = 200
        if result.get('code') == 'no_api_key':
            status = 503
        elif result.get('success') is False and result.get('code') in (
            'engine_error', 'advisor_error',
        ):
            status = 500
        elif result.get('code') == 'grok_api_error':
            status = 502
        return _api_json(result, status)
    except Exception as e:
        logger.error('advisor unhandled: %s', e, exc_info=True)
        return _api_json({
            'success': False,
            'error': str(e),
            'code': 'advisor_error',
            'phase': 'unhandled',
            'api_ok': False,
            'detail': traceback.format_exc()[-1500:],
        }, 500)


@tax_center_bp.route('/api/context', methods=['GET'])
@login_required
def api_context():
    """Debug/helper: account snapshot size (no secrets)."""
    try:
        from app.utils.account_context import build_account_context
        ctx = build_account_context(current_user.id)
        return _api_json({
            'success': True,
            'summary': ctx.get('portfolio_summary'),
            'live_price': ctx.get('live_price'),
            'grok_enabled': xai_advisor.is_configured(current_user),
        })
    except Exception as e:
        return _api_json({'success': False, 'error': str(e)}, 500)


@tax_center_bp.route('/api/sales', methods=['POST'])
@login_required
def api_record_sale():
    """Record a real stock sale against a vest lot + return tax analysis for that sale."""
    try:
        from app.utils.shares import whole_shares

        data = request.get_json() or {}
        vest_id = int(data['vest_event_id'])
        shares = float(whole_shares(data['shares_sold']))
        sale_price = float(data['sale_price'])
        sale_date = datetime.fromisoformat(data['sale_date']).date()
        commission = float(data.get('commission_fees', 0) or 0)

        vest = VestEvent.query.join(Grant).filter(
            VestEvent.id == vest_id, Grant.user_id == current_user.id
        ).first_or_404()
        grant = vest.grant
        is_iso = _iso(grant.share_type)
        if shares <= 0:
            return jsonify({'error': 'Shares must be a whole number ≥ 1'}), 400

        from app.utils.ledger import record_sale, LedgerError, ensure_lots_for_user
        ensure_lots_for_user(current_user.id)
        try:
            sale = record_sale(
                user_id=current_user.id,
                vest_event_id=vest_id,
                qty=shares,
                price=sale_price,
                sale_date=sale_date,
                fees=commission,
                notes=data.get('notes') or '',
            )
        except LedgerError as e:
            return jsonify({'error': str(e)}), 400

        basis = float(sale.cost_basis_per_share or 0)
        is_qd = None
        dd_ord = None
        if is_iso:
            ex = (
                ISOExercise.query.filter_by(user_id=current_user.id, vest_event_id=vest_id)
                .order_by(ISOExercise.exercise_date.desc())
                .first()
            )
            if ex and ex.exercise_date:
                from app.utils.tax_engine import classify_iso_disposition
                disp = classify_iso_disposition(grant.grant_date, ex.exercise_date, sale_date)
                is_qd = disp == 'qualifying'
                if not is_qd:
                    fmv_ex = ex.fmv_at_exercise or sale_price
                    dd_ord = max(0.0, min(sale_price, fmv_ex) - grant.share_price_at_grant) * shares
                sale.is_long_term = True if is_qd else sale.is_long_term
                sale.is_qualifying_disposition = is_qd
                sale.disqualifying_ordinary_income = dd_ord
                db.session.commit()

        # Tax analysis for this sale alone (year-scoped profile)
        eng = _engine_profile_for_request(current_user, {'tax_year': sale_date.year})
        lot_in = LotSaleInput(
            vest_event_id=vest.id,
            grant_id=grant.id,
            share_type=grant.share_type,
            grant_type=grant.grant_type,
            shares=shares,
            sale_price=sale_price,
            sale_date=sale_date,
            vest_date=vest.vest_date,
            grant_date=grant.grant_date,
            cost_basis_per_share=basis,
            is_iso=is_iso,
            strike_price=grant.share_price_at_grant if is_iso else 0.0,
            exercise_date=(
                ISOExercise.query.filter_by(user_id=current_user.id, vest_event_id=vest_id)
                .order_by(ISOExercise.exercise_date.desc())
                .first()
                or type('E', (), {'exercise_date': None})()
            ).exercise_date,
            fmv_at_exercise=(
                (ISOExercise.query.filter_by(user_id=current_user.id, vest_event_id=vest_id)
                 .order_by(ISOExercise.exercise_date.desc()).first() or type('E', (), {'fmv_at_exercise': None})())
                .fmv_at_exercise
            ),
            commission=commission,
            label=f'sale {sale.id}',
        )
        analysis = analyze_sales(eng, [lot_in])

        return jsonify({
            'success': True,
            'sale_id': sale.id,
            'analysis': analysis.to_dict(),
        }), 201
    except Exception as e:
        db.session.rollback()
        logger.error('record sale failed: %s', e, exc_info=True)
        return jsonify({'error': str(e)}), 400


def _iso_reduce_still_held(user_id: int, vest_id: int, shares: float) -> None:
    """FIFO: peel sold shares off earliest exercises' shares_still_held."""
    remaining = float(shares or 0)
    if remaining <= 0:
        return
    exs = (
        ISOExercise.query.filter_by(user_id=user_id, vest_event_id=vest_id)
        .order_by(ISOExercise.exercise_date.asc())
        .all()
    )
    for ex in exs:
        if remaining <= 0:
            break
        held = ex.shares_still_held if ex.shares_still_held is not None else ex.shares_exercised
        take = min(held, remaining)
        ex.shares_still_held = held - take
        remaining -= take


def _iso_restore_still_held(user_id: int, vest_id: int, shares: float) -> None:
    """Reverse of reduce: put shares back onto latest exercises first (LIFO undo)."""
    remaining = float(shares or 0)
    if remaining <= 0:
        return
    exs = (
        ISOExercise.query.filter_by(user_id=user_id, vest_event_id=vest_id)
        .order_by(ISOExercise.exercise_date.desc())
        .all()
    )
    for ex in exs:
        if remaining <= 0:
            break
        held = ex.shares_still_held if ex.shares_still_held is not None else ex.shares_exercised
        room = max(0.0, float(ex.shares_exercised or 0) - float(held or 0))
        take = min(room, remaining)
        ex.shares_still_held = held + take
        remaining -= take


@tax_center_bp.route('/api/sales/<int:sale_id>', methods=['PUT'])
@login_required
def api_update_sale(sale_id):
    """Update an existing sale (date / shares / price / commission / notes). Lot is fixed."""
    try:
        from app.utils.shares import whole_shares

        sale = StockSale.query.filter_by(id=sale_id, user_id=current_user.id).first_or_404()
        data = request.get_json() or {}

        vest = VestEvent.query.join(Grant).filter(
            VestEvent.id == sale.vest_event_id, Grant.user_id == current_user.id
        ).first_or_404()
        grant = vest.grant
        is_iso = _iso(grant.share_type)

        old_shares = float(sale.shares_sold or 0)
        shares = float(whole_shares(data['shares_sold'])) if 'shares_sold' in data else old_shares
        sale_price = float(data['sale_price']) if 'sale_price' in data else float(sale.sale_price)
        if 'sale_date' in data and data['sale_date']:
            sale_date = datetime.fromisoformat(data['sale_date']).date()
        else:
            sale_date = sale.sale_date
        commission = (
            float(data['commission_fees'])
            if 'commission_fees' in data
            else float(sale.commission_fees or 0)
        )
        notes = data['notes'] if 'notes' in data else (sale.notes or '')

        if shares <= 0:
            return jsonify({'error': 'Shares must be a whole number ≥ 1'}), 400

        from app.utils.ledger import apply_sale_qty_delta, LedgerError, ensure_lots_for_user
        ensure_lots_for_user(current_user.id)
        delta = shares - old_shares
        try:
            apply_sale_qty_delta(
                user_id=current_user.id,
                vest_event_id=sale.vest_event_id,
                delta=delta,
            )
        except LedgerError as e:
            return jsonify({'error': str(e)}), 400

        if is_iso:
            basis = grant.share_price_at_grant
        else:
            basis = vest.share_price_at_vest or float(sale.cost_basis_per_share or 0)

        proceeds = shares * sale_price
        total_basis = shares * basis
        gain = proceeds - total_basis - commission
        holding = (sale_date - vest.vest_date).days
        is_lt = holding >= 365

        is_qd = None
        dd_ord = None
        if is_iso:
            ex = (
                ISOExercise.query.filter_by(user_id=current_user.id, vest_event_id=sale.vest_event_id)
                .order_by(ISOExercise.exercise_date.desc())
                .first()
            )
            if ex and ex.exercise_date:
                from app.utils.tax_engine import classify_iso_disposition
                disp = classify_iso_disposition(grant.grant_date, ex.exercise_date, sale_date)
                is_qd = disp == 'qualifying'
                if not is_qd:
                    fmv_ex = ex.fmv_at_exercise or sale_price
                    dd_ord = max(0.0, min(sale_price, fmv_ex) - grant.share_price_at_grant) * shares

        sale.sale_date = sale_date
        sale.shares_sold = shares
        sale.sale_price = sale_price
        sale.total_proceeds = proceeds
        sale.cost_basis_per_share = basis
        sale.total_cost_basis = total_basis
        sale.capital_gain = gain
        sale.is_long_term = is_lt if not is_iso or not is_qd else True
        sale.commission_fees = commission
        sale.is_qualifying_disposition = is_qd
        sale.disqualifying_ordinary_income = dd_ord
        sale.notes = notes or ''

        db.session.commit()

        eng = _engine_profile_for_request(current_user, {'tax_year': sale_date.year})
        lot_in = LotSaleInput(
            vest_event_id=vest.id,
            grant_id=grant.id,
            share_type=grant.share_type,
            grant_type=grant.grant_type,
            shares=shares,
            sale_price=sale_price,
            sale_date=sale_date,
            vest_date=vest.vest_date,
            grant_date=grant.grant_date,
            cost_basis_per_share=basis,
            is_iso=is_iso,
            strike_price=grant.share_price_at_grant if is_iso else 0.0,
            exercise_date=(
                ISOExercise.query.filter_by(user_id=current_user.id, vest_event_id=sale.vest_event_id)
                .order_by(ISOExercise.exercise_date.desc())
                .first()
                or type('E', (), {'exercise_date': None})()
            ).exercise_date,
            fmv_at_exercise=(
                (ISOExercise.query.filter_by(user_id=current_user.id, vest_event_id=sale.vest_event_id)
                 .order_by(ISOExercise.exercise_date.desc()).first()
                 or type('E', (), {'fmv_at_exercise': None})())
                .fmv_at_exercise
            ),
            commission=commission,
            label=f'sale {sale.id}',
        )
        analysis = analyze_sales(eng, [lot_in])

        return jsonify({
            'success': True,
            'sale_id': sale.id,
            'analysis': analysis.to_dict(),
        }), 200
    except Exception as e:
        db.session.rollback()
        logger.error('update sale failed: %s', e, exc_info=True)
        return jsonify({'error': str(e)}), 400


@tax_center_bp.route('/api/sales/<int:sale_id>', methods=['DELETE'])
@login_required
def api_delete_sale(sale_id):
    sale = StockSale.query.filter_by(id=sale_id, user_id=current_user.id).first_or_404()
    from app.utils.ledger import reverse_sale, ensure_lots_for_user
    ensure_lots_for_user(current_user.id)
    reverse_sale(user_id=current_user.id, sale=sale)
    return jsonify({'success': True})


@tax_center_bp.route('/api/exercises', methods=['POST'])
@login_required
def api_record_exercise():
    """Record ISO exercise (critical for AMT + later sale basis)."""
    try:
        from app.utils.shares import whole_shares

        data = request.get_json() or {}
        vest_id = int(data['vest_event_id'])
        shares = float(whole_shares(data['shares_exercised']))
        exercise_date = datetime.fromisoformat(data['exercise_date']).date()
        fmv = float(data['fmv_at_exercise'])
        vest = VestEvent.query.join(Grant).filter(
            VestEvent.id == vest_id, Grant.user_id == current_user.id
        ).first_or_404()
        grant = vest.grant
        if not _iso(grant.share_type):
            return jsonify({'error': 'Lot is not an ISO'}), 400
        if shares <= 0:
            return jsonify({'error': 'Shares must be a whole number ≥ 1'}), 400

        strike = grant.share_price_at_grant
        bargain = max(0.0, fmv - strike)
        ex = ISOExercise(
            user_id=current_user.id,
            vest_event_id=vest_id,
            exercise_date=exercise_date,
            shares_exercised=shares,
            strike_price=strike,
            fmv_at_exercise=fmv,
            bargain_element_per_share=bargain,
            total_bargain_element=bargain * shares,
            shares_still_held=shares,
            grant_date=grant.grant_date,
            cash_paid=data.get('cash_paid'),
            notes=data.get('notes') or '',
        )
        db.session.add(ex)
        db.session.flush()
        from app.utils.ledger import record_exercise, LedgerError, ensure_lots_for_user
        ensure_lots_for_user(current_user.id)
        try:
            record_exercise(
                user_id=current_user.id,
                vest_event_id=vest_id,
                qty=shares,
                exercise_date=exercise_date,
                fmv=fmv,
                strike=strike,
                exercise_id=ex.id,
                commit=False,
            )
        except LedgerError as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 400
        db.session.commit()

        eng = _engine_profile_for_request(current_user, {'tax_year': exercise_date.year})
        # AMT-only scenario: zero sale, just exercise preference via dummy zero-share? 
        # Use analyze with empty sales but we need AMT from bargain — call analyze_sales with empty
        # and inject via a synthetic approach: create lot sale 0 shares won't work.
        # Return bargain for UI; full AMT on empty sales + note
        from app.utils.tax_engine import compute_amt
        other = eng['other_ordinary_income']
        amti = other + bargain * shares
        amt = compute_amt(amti, eng['filing_status'], eng['tax_year'])
        from app.utils.tax_engine import progressive_tax, ORDINARY_BRACKETS, _year_table
        brackets = _year_table(ORDINARY_BRACKETS, eng['tax_year'])[eng['filing_status']]
        regular = progressive_tax(other, brackets)
        amt_due = max(0.0, amt - regular)

        return jsonify({
            'success': True,
            'exercise_id': ex.id,
            'bargain_element': bargain * shares,
            'amt_due_estimate': amt_due,
            'warnings': [
                'AMT estimate uses other_ordinary_income from Tax Profile plus this bargain element only.',
            ],
        }), 201
    except Exception as e:
        db.session.rollback()
        logger.error('exercise failed: %s', e, exc_info=True)
        return jsonify({'error': str(e)}), 400
