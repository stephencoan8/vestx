"""
Sales & Tax Center — lot inventory, sale recording, what-if tax engine.
"""

from __future__ import annotations

from datetime import datetime, date
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
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
import logging

logger = logging.getLogger(__name__)

tax_center_bp = Blueprint('tax_center', __name__, url_prefix='/tax')


def _iso(share_type: str) -> bool:
    return share_type in (ShareType.ISO_5Y.value, ShareType.ISO_6Y.value)


# ensure date is available in template helpers
__all__ = ['tax_center_bp']


@tax_center_bp.route('/')
@login_required
def hub():
    """Sales & tax hub: lots, ledger, planner entry."""
    profile = TaxProfile.for_user(current_user)
    lots = build_lots_for_user(current_user.id)
    sales = (
        StockSale.query.filter_by(user_id=current_user.id)
        .order_by(StockSale.sale_date.desc())
        .all()
    )
    exercises = (
        ISOExercise.query.filter_by(user_id=current_user.id)
        .order_by(ISOExercise.exercise_date.desc())
        .all()
    )
    live = get_latest_user_price(current_user.id) or 0.0
    available = sum(l['shares_available'] for l in lots)
    return render_template(
        'tax/hub.html',
        profile=profile,
        lots=lots,
        sales=sales,
        exercises=exercises,
        live_price=live,
        shares_available=available,
        profile_ready=profile.other_ordinary_income is not None,
        grok_enabled=xai_advisor.is_configured(),
    )


@tax_center_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def tax_profile():
    profile = TaxProfile.for_user(current_user)
    if request.method == 'POST':
        try:
            profile.filing_status = request.form.get('filing_status') or 'single'
            profile.state_code = (request.form.get('state_code') or '').upper()[:2] or None
            profile.use_bracket_engine = request.form.get('use_bracket_engine') == 'on'
            profile.use_state_engine = request.form.get('use_state_engine') == 'on'

            def _f(name, default=None):
                raw = request.form.get(name, '')
                if raw is None or str(raw).strip() == '':
                    return default
                return float(raw)

            fo = request.form.get('federal_ordinary_rate')
            profile.federal_ordinary_rate = float(fo) / 100.0 if fo not in (None, '') else None
            fl = request.form.get('federal_ltcg_rate')
            profile.federal_ltcg_rate = float(fl) / 100.0 if fl not in (None, '') else None

            profile.state_ordinary_rate = (_f('state_ordinary_rate', 0) or 0) / 100.0
            profile.state_cg_rate = (_f('state_cg_rate', profile.state_ordinary_rate * 100) or 0) / 100.0

            profile.other_ordinary_income = _f('other_ordinary_income', 0) or 0
            profile.other_long_term_gains = _f('other_long_term_gains', 0) or 0
            profile.other_short_term_gains = _f('other_short_term_gains', 0) or 0
            profile.ytd_wages = _f('ytd_wages', 0) or 0
            profile.amt_credit_carryforward = _f('amt_credit_carryforward', 0) or 0
            profile.ca_amt_credit_carryforward = _f('ca_amt_credit_carryforward', 0) or 0
            profile.include_fica = request.form.get('include_fica') == 'on'
            profile.ss_wage_base_maxed = request.form.get('ss_wage_base_maxed') == 'on'
            profile.include_niit = request.form.get('include_niit') == 'on'
            ty = request.form.get('tax_year')
            profile.tax_year = int(ty) if ty else date.today().year

            # Keep legacy user fields in sync for rest of app
            if profile.federal_ordinary_rate is not None:
                current_user.federal_tax_rate = profile.federal_ordinary_rate
            current_user.state_tax_rate = profile.state_ordinary_rate
            current_user.include_fica = profile.include_fica
            current_user.ss_wage_base_maxed = profile.ss_wage_base_maxed

            db.session.commit()
            flash('Tax profile saved.', 'success')
            return redirect(url_for('tax_center.hub'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving profile: {e}', 'danger')

    return render_template('tax/profile.html', profile=profile, year=date.today().year)


@tax_center_bp.route('/api/lots')
@login_required
def api_lots():
    return jsonify({'lots': build_lots_for_user(current_user.id)})


def _lot_specs_from_request(data: dict, user_id: int) -> list:
    """Build LotSpec list from API lots[] payload + DB grant/vest/exercise truth."""
    items = data.get('lots') or []
    specs = []
    for item in items:
        vest_id = int(item['vest_event_id'])
        shares = float(item.get('shares') or 0)
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
        profile = TaxProfile.for_user(current_user)
        eng = profile.to_engine_dict()
        if data.get('tax_year'):
            eng['tax_year'] = int(data['tax_year'])
        if data.get('other_ordinary_income') is not None:
            eng['other_ordinary_income'] = float(data['other_ordinary_income'])

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
        profile = TaxProfile.for_user(current_user)
        eng = profile.to_engine_dict()
        live = get_latest_user_price(current_user.id) or 0.0
        lots = build_lots_for_user(current_user.id)

        prompt = (data.get('prompt') or data.get('raw_text') or '').strip()
        explain = bool(data.get('explain', True))

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
            if data.get('use_grok_parse', True) and xai_advisor.is_configured():
                try:
                    parsed = xai_advisor.parse_goal_with_grok(
                        prompt,
                        inventory_summary=xai_advisor.summarize_inventory(lots),
                        profile_summary=xai_advisor.summarize_profile(eng),
                        defaults=defaults,
                    )
                    parse_meta = {
                        'source': 'grok',
                        'interpretation': parsed.get('interpretation'),
                        'clarifications': parsed.get('clarifications') or [],
                        'raw_parse': parsed,
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
                    ):
                        if flag in parsed and parsed[flag] is not None:
                            setattr(goal, flag, bool(parsed[flag]))
                    if parsed.get('iso_prefer_hold_fraction') is not None:
                        goal.iso_prefer_hold_fraction = float(parsed['iso_prefer_hold_fraction'])
                    if parsed.get('iso_max_exercise') is not None:
                        goal.iso_max_exercise = float(parsed['iso_max_exercise'])
                    if parsed.get('max_tax') is not None:
                        goal.max_tax = float(parsed['max_tax'])
                except Exception as e:
                    logger.warning('Grok parse failed, using heuristic: %s', e)
                    goal = parse_goal_heuristic(prompt, defaults)
                    # Keep form price/dates
                    goal.sale_price = float(data.get('sale_price') or live or 0)
                    goal.sale_date = goal.sale_date or date.today()
                    parse_meta = {'source': 'heuristic_fallback', 'error': str(e)}
            else:
                heur = parse_goal_heuristic(prompt, defaults)
                if heur.target_net_cash is not None:
                    goal.target_net_cash = heur.target_net_cash
                goal.objective = heur.objective or goal.objective
                goal.allow_iso_cashless = heur.allow_iso_cashless
                goal.allow_iso_exercise_hold = (
                    goal.allow_iso_exercise_hold or heur.allow_iso_exercise_hold
                )
                parse_meta = {'source': 'heuristic', 'interpretation': None}

        result = optimize_goal(eng, lots, goal)
        payload = result.to_dict()
        payload['parse'] = parse_meta

        if explain and xai_advisor.is_configured() and (
            prompt or result.picks
        ):
            try:
                payload['explanation'] = xai_advisor.explain_plan_with_grok(
                    user_request=prompt or f"Net ${goal.target_net_cash or 0:,.0f} minimize tax",
                    plan=payload,
                    profile_summary=xai_advisor.summarize_profile(eng),
                    inventory_summary=xai_advisor.summarize_inventory(lots),
                )
            except Exception as e:
                logger.warning('Grok explain failed: %s', e)
                payload['explanation'] = None
                payload['explanation_error'] = str(e)
        else:
            payload['explanation'] = None
            if not xai_advisor.is_configured():
                payload['explanation_note'] = (
                    'Set XAI_API_KEY for Grok narrative explanations of this plan.'
                )

        payload['grok_enabled'] = xai_advisor.is_configured()
        return jsonify({'success': True, **payload})
    except Exception as e:
        logger.error('goal optimize failed: %s', e, exc_info=True)
        return jsonify({'error': str(e)}), 400


@tax_center_bp.route('/api/advisor', methods=['POST'])
@login_required
def api_advisor():
    """
    Free-form Grok chat with plan + inventory context.
    Body: { messages: [{role, content}], plan?: object }
    """
    if not xai_advisor.is_configured():
        return jsonify({
            'error': 'Grok is not configured. Set XAI_API_KEY on the server.',
            'grok_enabled': False,
        }), 503
    try:
        data = request.get_json() or {}
        messages = data.get('messages') or []
        if not messages:
            return jsonify({'error': 'messages required'}), 400
        profile = TaxProfile.for_user(current_user)
        eng = profile.to_engine_dict()
        lots = build_lots_for_user(current_user.id)
        reply = xai_advisor.advisor_chat(
            messages=messages,
            plan=data.get('plan'),
            profile_summary=xai_advisor.summarize_profile(eng),
            inventory_summary=xai_advisor.summarize_inventory(lots),
        )
        return jsonify({'success': True, 'reply': reply, 'grok_enabled': True})
    except Exception as e:
        logger.error('advisor failed: %s', e, exc_info=True)
        return jsonify({'error': str(e)}), 400


@tax_center_bp.route('/api/sales', methods=['POST'])
@login_required
def api_record_sale():
    """Record a real stock sale against a vest lot + return tax analysis for that sale."""
    try:
        data = request.get_json() or {}
        vest_id = int(data['vest_event_id'])
        shares = float(data['shares_sold'])
        sale_price = float(data['sale_price'])
        sale_date = datetime.fromisoformat(data['sale_date']).date()
        commission = float(data.get('commission_fees', 0) or 0)

        vest = VestEvent.query.join(Grant).filter(
            VestEvent.id == vest_id, Grant.user_id == current_user.id
        ).first_or_404()
        grant = vest.grant
        is_iso = _iso(grant.share_type)

        # Availability check
        lots = {l['vest_event_id']: l for l in build_lots_for_user(current_user.id)}
        lot = lots.get(vest_id)
        if not lot:
            return jsonify({'error': 'Lot not found or not sellable'}), 400
        if shares > lot['shares_available'] + 1e-6:
            return jsonify({
                'error': f'Only {lot["shares_available"]:.4f} shares available on this lot'
            }), 400

        if is_iso:
            basis = grant.share_price_at_grant
        else:
            basis = vest.share_price_at_vest or 0.0

        proceeds = shares * sale_price
        total_basis = shares * basis
        gain = proceeds - total_basis - commission
        holding = (sale_date - vest.vest_date).days
        is_lt = holding >= 365

        # ISO QD flag
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

        sale = StockSale(
            user_id=current_user.id,
            vest_event_id=vest_id,
            sale_date=sale_date,
            shares_sold=shares,
            sale_price=sale_price,
            total_proceeds=proceeds,
            cost_basis_per_share=basis,
            total_cost_basis=total_basis,
            capital_gain=gain,
            is_long_term=is_lt if not is_iso or not is_qd else True,
            commission_fees=commission,
            is_qualifying_disposition=is_qd,
            disqualifying_ordinary_income=dd_ord,
            notes=data.get('notes') or '',
            lot_selection_method=data.get('lot_selection_method') or 'SpecID',
        )
        db.session.add(sale)

        # Reduce ISO shares_still_held if applicable
        if is_iso:
            remaining = shares
            exs = (
                ISOExercise.query.filter_by(user_id=current_user.id, vest_event_id=vest_id)
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

        db.session.commit()

        # Tax analysis for this sale alone
        profile = TaxProfile.for_user(current_user)
        eng = profile.to_engine_dict()
        eng['tax_year'] = sale_date.year
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


@tax_center_bp.route('/api/sales/<int:sale_id>', methods=['DELETE'])
@login_required
def api_delete_sale(sale_id):
    sale = StockSale.query.filter_by(id=sale_id, user_id=current_user.id).first_or_404()
    db.session.delete(sale)
    db.session.commit()
    return jsonify({'success': True})


@tax_center_bp.route('/api/exercises', methods=['POST'])
@login_required
def api_record_exercise():
    """Record ISO exercise (critical for AMT + later sale basis)."""
    try:
        data = request.get_json() or {}
        vest_id = int(data['vest_event_id'])
        shares = float(data['shares_exercised'])
        exercise_date = datetime.fromisoformat(data['exercise_date']).date()
        fmv = float(data['fmv_at_exercise'])
        vest = VestEvent.query.join(Grant).filter(
            VestEvent.id == vest_id, Grant.user_id == current_user.id
        ).first_or_404()
        grant = vest.grant
        if not _iso(grant.share_type):
            return jsonify({'error': 'Lot is not an ISO'}), 400

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
        db.session.commit()

        profile = TaxProfile.for_user(current_user)
        eng = profile.to_engine_dict()
        eng['tax_year'] = exercise_date.year
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
