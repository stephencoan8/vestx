"""
Grant management routes - view, add, edit, delete grants.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models.grant import Grant, GrantType, ShareType
from app.models.vest_event import VestEvent
from app.models.sale_plan import SalePlan
from app.utils.vest_calculator import (
    calculate_vest_schedule,
    get_grant_configuration,
    normalize_vest_frequency,
)
from app.utils.price_utils import get_latest_user_price
from datetime import datetime, date, timedelta
import logging

grants_bp = Blueprint('grants', __name__, url_prefix='/grants')

logger = logging.getLogger(__name__)


@grants_bp.route('/')
@login_required
def list_grants():
    """Holdings: grants + open lots + upcoming vests."""
    from datetime import date
    from collections import defaultdict
    from app.utils.lot_inventory import build_lots_for_user
    from app.utils.price_utils import get_latest_user_price
    from app.utils.ledger import ensure_lots_for_user

    ensure_lots_for_user(current_user.id)
    grants = Grant.query.filter_by(user_id=current_user.id).order_by(Grant.grant_date.desc()).all()
    live = get_latest_user_price(current_user.id) or 0.0
    lots = build_lots_for_user(current_user.id) or []
    held_by_grant = defaultdict(lambda: {'shares': 0.0, 'value': 0.0, 'unex': 0.0})
    for lot in lots:
        gid = lot.get('grant_id')
        sh = float(lot.get('shares_available') or 0)
        unex = float(lot.get('shares_unexercised') or 0)
        held_by_grant[gid]['shares'] += sh
        held_by_grant[gid]['unex'] += unex
        if lot.get('is_iso') and unex:
            strike = float(lot.get('strike_price') or 0)
            held_by_grant[gid]['value'] += sh * live + unex * max(0.0, live - strike)
        else:
            held_by_grant[gid]['value'] += sh * live
    today = date.today()
    upcoming = (
        VestEvent.query.join(Grant)
        .filter(Grant.user_id == current_user.id, VestEvent.vest_date > today)
        .order_by(VestEvent.vest_date.asc())
        .limit(24)
        .all()
    )
    return render_template(
        'grants/list.html',
        grants=grants,
        lots=lots,
        live_price=live,
        held_by_grant=held_by_grant,
        upcoming=upcoming,
        tab=({'upcoming': 'schedule'}.get(request.args.get('tab')) or request.args.get('tab') or 'grants'),
    )


@grants_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add_grant():
    """Add a new grant."""
    if request.method == 'POST':
        try:
            # Parse form data
            grant_date = datetime.strptime(request.form.get('grant_date'), '%Y-%m-%d').date()
            grant_type = request.form.get('grant_type')
            share_type = request.form.get('share_type')
            if (grant_type or '').lower() in ('espp', 'nqespp'):
                share_type = 'espp'
            from app.utils.shares import whole_shares
            share_quantity = float(whole_shares(request.form.get('share_quantity')))
            bonus_type = request.form.get('bonus_type')
            vest_years = request.form.get('vest_years')
            vest_frequency = normalize_vest_frequency(request.form.get('vest_frequency'))
            notes = request.form.get('notes', '')
            
            # ESPP discount (typically 15% = 0.15)
            espp_discount = request.form.get('espp_discount')
            if espp_discount:
                espp_discount = float(espp_discount)
            else:
                # Default 15% for ESPP, 0% for others
                espp_discount = 0.15 if grant_type == 'espp' else 0.0
            
            share_price = get_latest_user_price(current_user.id, as_of_date=grant_date) or 0.0

            # Get vesting configuration
            if vest_years:
                vest_years = int(vest_years)
                cliff_years = 1.0  # Default
            else:
                vest_years, cliff_years = get_grant_configuration(grant_type, share_type, bonus_type)
            
            # Create grant
            grant = Grant(
                user_id=current_user.id,
                grant_date=grant_date,
                grant_type=grant_type,
                share_type=share_type,
                share_quantity=share_quantity,
                share_price_at_grant=share_price,
                vest_years=vest_years,
                cliff_years=cliff_years,
                vest_frequency=vest_frequency,
                bonus_type=bonus_type,
                espp_discount=espp_discount,
                notes=notes
            )
            
            db.session.add(grant)
            db.session.flush()  # Get grant ID
            
            # Calculate and create vest events
            vest_schedule = calculate_vest_schedule(grant)
            for vest in vest_schedule:
                vest_event = VestEvent(
                    grant_id=grant.id,
                    vest_date=vest['vest_date'],
                    shares_vested=vest['shares'],
                    tax_year=vest['vest_date'].year
                )
                db.session.add(vest_event)
            db.session.flush()
            try:
                from app.utils.vest_basis import ensure_vest_fmv_snapshot
                for ve in VestEvent.query.filter_by(grant_id=grant.id).all():
                    if ve.has_vested:
                        ensure_vest_fmv_snapshot(ve, user_id=current_user.id)
            except Exception:
                pass

            db.session.commit()
            flash('Grant added successfully!', 'success')
            return redirect(url_for('grants.list_grants'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding grant: {str(e)}', 'error')
    
    return render_template('grants/add.html',
                         grant_types=GrantType,
                         share_types=ShareType)


@grants_bp.route('/<int:grant_id>')
@login_required
def view_grant(grant_id):
    """View grant details and vest schedule."""
    grant = Grant.query.get_or_404(grant_id)
    
    # Security check
    if grant.user_id != current_user.id:
        flash('Access denied', 'error')
        return redirect(url_for('grants.list_grants'))
    
    vest_events = VestEvent.query.filter_by(grant_id=grant.id).order_by(VestEvent.vest_date).all()
    return render_template('grants/view.html', grant=grant, vest_events=vest_events)


@grants_bp.route('/<int:grant_id>/rebuild-schedule', methods=['POST'])
@login_required
def rebuild_grant_schedule(grant_id):
    """Rebuild vest rows from current grant rules (preserves tax/sale-linked events)."""
    grant = Grant.query.get_or_404(grant_id)
    if grant.user_id != current_user.id:
        flash('Access denied', 'error')
        return redirect(url_for('grants.list_grants'))
    try:
        from app.utils.sync_vest_schedule import sync_vest_events_for_grant
        if not grant.vest_frequency:
            grant.vest_frequency = 'semiannual'
        schedule = calculate_vest_schedule(grant)
        stats = sync_vest_events_for_grant(grant, schedule)
        db.session.commit()
        msg = (
            f"Schedule rebuilt: {stats.get('created', 0)} added, "
            f"{stats.get('updated', 0)} updated, {stats.get('deleted', 0)} removed."
        )
        if stats.get('preserved'):
            msg += (
                f" {stats['preserved']} event(s) with tax/sale data kept even though "
                "they fell off the new schedule — edit or delete those vest rows manually."
            )
        flash(msg, 'success')
    except Exception as e:
        db.session.rollback()
        logger.error('rebuild schedule failed for grant %s: %s', grant_id, e, exc_info=True)
        flash(f'Could not rebuild schedule: {e}', 'danger')
    return redirect(url_for('grants.view_grant', grant_id=grant_id))


@grants_bp.route('/<int:grant_id>/delete', methods=['POST'])
@login_required
def delete_grant(grant_id):
    """Delete a grant."""
    grant = Grant.query.get_or_404(grant_id)
    
    # Security check
    if grant.user_id != current_user.id:
        flash('Access denied', 'error')
        return redirect(url_for('grants.list_grants'))
    
    db.session.delete(grant)
    db.session.commit()
    flash('Grant deleted successfully', 'success')
    return redirect(url_for('grants.list_grants'))


@grants_bp.route('/<int:grant_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_grant(grant_id):
    """Edit an existing grant."""
    grant = Grant.query.get_or_404(grant_id)
    
    # Security check
    if grant.user_id != current_user.id:
        flash('Access denied', 'error')
        return redirect(url_for('grants.list_grants'))
    
    if request.method == 'POST':
        try:
            # Parse form data
            grant_date = datetime.strptime(request.form.get('grant_date'), '%Y-%m-%d').date()
            grant_type = request.form.get('grant_type')
            share_type = request.form.get('share_type') or grant.share_type  # Keep existing if disabled
            if (grant_type or '').lower() in ('espp', 'nqespp'):
                share_type = 'espp'
            from app.utils.shares import whole_shares
            share_quantity = float(whole_shares(request.form.get('share_quantity')))
            bonus_type = request.form.get('bonus_type') or None
            vest_years = request.form.get('vest_years') or None
            vest_frequency = normalize_vest_frequency(request.form.get('vest_frequency'))
            notes = request.form.get('notes', '')
            
            # ESPP discount (typically 15% = 0.15)
            espp_discount = request.form.get('espp_discount')
            if espp_discount:
                espp_discount = float(espp_discount)
            else:
                # Default 15% for ESPP, 0% for others
                espp_discount = 0.15 if grant_type == 'espp' else 0.0
            
            # Get stock price at grant date using centralized per-user price helper
            try:
                share_price = get_latest_user_price(current_user.id, as_of_date=grant_date) or 0.0
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Failed to retrieve user price for edit_grant; defaulting to 0.0")
                share_price = 0.0
            
            # Get vesting configuration
            if vest_years:
                vest_years = int(vest_years)
                cliff_years = grant.cliff_years or 1.0
            else:
                vest_years, cliff_years = get_grant_configuration(grant_type, share_type, bonus_type)
            
            # Update grant
            grant.grant_date = grant_date
            grant.grant_type = grant_type
            grant.share_type = share_type
            grant.share_quantity = share_quantity
            grant.share_price_at_grant = share_price
            grant.vest_years = vest_years
            grant.cliff_years = cliff_years
            grant.vest_frequency = vest_frequency
            grant.bonus_type = bonus_type
            grant.espp_discount = espp_discount
            grant.notes = notes

            # Recalculate schedule and sync vest rows WITHOUT wiping tax/sale data
            from app.utils.sync_vest_schedule import sync_vest_events_for_grant
            vest_schedule = calculate_vest_schedule(grant)
            sync_stats = sync_vest_events_for_grant(grant, vest_schedule)

            db.session.commit()

            msg = 'Grant updated successfully!'
            if sync_stats.get('preserved'):
                msg += (
                    f" {sync_stats['preserved']} prior vest event(s) with tax or sale data "
                    "were kept even though they are no longer on the new schedule."
                )
            flash(msg, 'success')
            return redirect(url_for('grants.view_grant', grant_id=grant.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Error updating grant: {str(e)}', 'error')

    return render_template('grants/edit.html',
                         grant=grant,
                         grant_types=GrantType,
                         share_types=ShareType)


@grants_bp.route('/vest-event/<int:event_id>/update', methods=['POST'])
@login_required
def update_vest_event(event_id):
    """Update vest event with tax information."""
    vest_event = VestEvent.query.get_or_404(event_id)
    
    # Security check
    if vest_event.grant.user_id != current_user.id:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        import re

        def _parse_numeric(val):
            """Parse numeric form input tolerant of formats like "$1,234.56"."""
            if val is None:
                return 0.0
            if isinstance(val, (int, float)):
                return float(val)
            s = str(val).strip()
            if s == '':
                return 0.0
            s_clean = s.replace(',', '').replace('$', '').replace('(', '-').replace(')', '')
            m = re.search(r"[-+]?[0-9]*\.?[0-9]+", s_clean)
            if m:
                try:
                    return float(m.group(0))
                except ValueError:
                    pass
            logger.warning("Could not parse numeric value %r - coercing to 0.0", s)
            return 0.0

        grant = vest_event.grant
        is_espp_type = grant.grant_type in ['espp', 'nqespp']

        if is_espp_type:
            cash_paid = 0.0
            cash_covered_all = True
            shares_sold = 0.0
        else:
            cash_paid = _parse_numeric(request.form.get('cash_paid', 0) or 0)
            cash_covered_all = str(request.form.get('cash_covered_all', 'true')).lower() == 'true'
            shares_sold = _parse_numeric(request.form.get('shares_sold', 0) or 0)

            if cash_paid < 0 or shares_sold < 0:
                return jsonify({'error': 'Values must be non-negative'}), 400

        if shares_sold > vest_event.shares_vested:
            shares_sold = vest_event.shares_vested

        vest_event.cash_paid = cash_paid
        vest_event.cash_covered_all = cash_covered_all
        vest_event.shares_sold = 0.0 if cash_covered_all else shares_sold

        db.session.add(vest_event)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Vest event updated',
            'cash_paid': vest_event.cash_paid,
            'cash_covered_all': vest_event.cash_covered_all,
            'shares_sold': vest_event.shares_sold,
            'shares_received': vest_event.shares_received,
            'tax_withheld': vest_event.tax_withheld,
            'net_value': vest_event.net_value,
        })

    except Exception as e:
        db.session.rollback()
        logger.error("Failed to update vest event %s: %s", event_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


@grants_bp.route('/vest-event/<int:event_id>/details', methods=['POST', 'PUT'])
@login_required
def update_vest_details(event_id):
    """Update core vest fields: date, shares vested, FMV at vest (basis)."""
    from app.models.stock_sale import StockSale, ISOExercise
    from app.utils.shares import whole_shares

    vest_event = VestEvent.query.get_or_404(event_id)
    if vest_event.grant.user_id != current_user.id:
        return jsonify({'error': 'Access denied'}), 403

    try:
        data = request.get_json(silent=True) or {}
        # Also accept form posts
        if not data and request.form:
            data = {k: request.form.get(k) for k in (
                'vest_date', 'shares_vested', 'fmv_at_vest', 'price_at_vest', 'notes'
            ) if request.form.get(k) is not None}

        grant = vest_event.grant
        is_iso = grant.share_type in (ShareType.ISO_5Y.value, ShareType.ISO_6Y.value)
        is_cash = grant.share_type == ShareType.CASH.value

        old_date = vest_event.vest_date
        old_shares = float(vest_event.shares_vested or 0)
        old_fmv = float(vest_event.fmv_at_vest or 0) if vest_event.fmv_at_vest else None

        # --- vest date ---
        if 'vest_date' in data and data['vest_date']:
            new_date = datetime.strptime(str(data['vest_date'])[:10], '%Y-%m-%d').date()
            if grant.grant_date and new_date < grant.grant_date:
                return jsonify({
                    'error': f'Vest date cannot be before grant date ({grant.grant_date.isoformat()})'
                }), 400
            vest_event.vest_date = new_date
            vest_event.tax_year = new_date.year

        # --- shares vested ---
        shares_key = 'shares_vested' if 'shares_vested' in data else None
        if shares_key is not None and data.get(shares_key) not in (None, ''):
            if is_cash:
                new_shares = float(data[shares_key])
            else:
                new_shares = float(whole_shares(data[shares_key]))
            if new_shares <= 0:
                return jsonify({'error': 'Shares vested must be ≥ 1'}), 400

            tax_sold = float(vest_event.shares_sold or 0)
            sales = StockSale.query.filter_by(
                user_id=current_user.id, vest_event_id=event_id
            ).all()
            sold_total = sum(float(s.shares_sold or 0) for s in sales)
            exercises = ISOExercise.query.filter_by(
                user_id=current_user.id, vest_event_id=event_id
            ).all()
            exercised_total = sum(float(e.shares_exercised or 0) for e in exercises)

            min_needed = max(tax_sold, tax_sold + sold_total)
            if is_iso:
                min_needed = max(min_needed, exercised_total, sold_total)
            if new_shares + 1e-9 < min_needed:
                return jsonify({
                    'error': (
                        f'Cannot set shares to {int(new_shares)} — this lot already has '
                        f'{int(min_needed)} committed (tax withholding / sales / exercises). '
                        f'Delete or reduce those first.'
                    )
                }), 400
            vest_event.shares_vested = new_shares

        # --- FMV / price at vest (RSU basis snapshot) ---
        fmv_raw = data.get('fmv_at_vest', data.get('price_at_vest', None))
        fmv_changed = False
        if fmv_raw is not None and fmv_raw != '':
            new_fmv = float(fmv_raw)
            if new_fmv < 0:
                return jsonify({'error': 'FMV / price at vest cannot be negative'}), 400
            vest_event.fmv_at_vest = new_fmv
            fmv_changed = (old_fmv is None) or (abs(new_fmv - old_fmv) > 1e-9)

        if 'notes' in data and data['notes'] is not None:
            vest_event.notes = str(data['notes']).strip()

        # Propagate to recorded sales when FMV or date changes (RSU basis / holding period)
        sales = StockSale.query.filter_by(
            user_id=current_user.id, vest_event_id=event_id
        ).all()
        date_changed = vest_event.vest_date != old_date
        if sales and (fmv_changed or date_changed):
            for sale in sales:
                if fmv_changed and not is_iso and not is_cash:
                    # RSU/RSA: cost basis is FMV at vest
                    sale.cost_basis_per_share = float(vest_event.fmv_at_vest)
                    sale.total_cost_basis = sale.shares_sold * sale.cost_basis_per_share
                    sale.capital_gain = (
                        float(sale.total_proceeds or 0)
                        - float(sale.total_cost_basis or 0)
                        - float(sale.commission_fees or 0)
                    )
                if date_changed and sale.sale_date and vest_event.vest_date:
                    holding = (sale.sale_date - vest_event.vest_date).days
                    sale.is_long_term = holding >= 365

        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Vest details updated',
            'vest_id': vest_event.id,
            'vest_date': vest_event.vest_date.isoformat() if vest_event.vest_date else None,
            'shares_vested': vest_event.shares_vested,
            'fmv_at_vest': vest_event.fmv_at_vest,
            'price_at_vest': vest_event.share_price_at_vest,
            'sales_updated': len(sales) if (fmv_changed or date_changed) else 0,
        })
    except Exception as e:
        db.session.rollback()
        logger.error("Failed to update vest details %s: %s", event_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


@grants_bp.route('/schedule')
@login_required
def vest_schedule():
    """Legacy schedule URL → Holdings calendar tab."""
    return redirect(url_for('grants.list_grants', tab='schedule'))


@grants_bp.route('/schedule-full')
@login_required
def vest_schedule_full():
    """View complete vesting schedule."""
    from app.utils.price_utils import get_latest_user_price
    from datetime import date
    from sqlalchemy.orm import joinedload
    from app.utils.price_utils import warm_user_price_history
    from app.utils.portfolio_summary import sold_shares_by_vest
    from app.utils.lot_inventory import build_lots_for_user

    vest_events = VestEvent.query.options(
        joinedload(VestEvent.grant)
    ).join(Grant).filter(
        Grant.user_id == current_user.id
    ).order_by(VestEvent.vest_date).all()

    warm_user_price_history(current_user.id)
    latest_stock_price = get_latest_user_price(current_user.id) or 0.0
    today = date.today()
    sold_map = sold_shares_by_vest(current_user.id)
    held_by_vest = {}
    for lot in build_lots_for_user(current_user.id) or []:
        held_by_vest[int(lot['vest_event_id'])] = float(lot.get('shares_available') or 0)

    enriched_events = []
    for ve in vest_events:
        if ve.vest_date > today:
            tax_info = ve.estimate_tax_withholding(latest_stock_price, user=current_user)
            ve.estimated_tax = tax_info['tax_amount']
        else:
            ve.estimated_tax = None
        market_sold = float(sold_map.get(ve.id, 0) or 0)
        ve.market_shares_sold = market_sold
        ve.shares_held_now = float(held_by_vest.get(ve.id, 0) or 0)
        if ve.has_vested and ve.shares_held_now <= 0 and market_sold <= 0:
            # Future ISO unexercised etc. — fall back to received if no lot yet
            ve.shares_held_now = max(0.0, float(ve.shares_received or 0) - market_sold)
        ve.held_value_now = ve.shares_held_now * latest_stock_price
        enriched_events.append(ve)

    return render_template(
        'grants/schedule.html',
        vest_events=enriched_events,
        live_price=latest_stock_price,
    )


@grants_bp.route('/needs-tax-info')
@login_required
def needs_tax_info():
    """Show vests that need tax information."""
    from sqlalchemy.orm import joinedload
    
    # Get all vested events that need tax info
    all_vest_events = VestEvent.query.options(
        joinedload(VestEvent.grant)
    ).join(Grant).filter(
        Grant.user_id == current_user.id
    ).order_by(VestEvent.vest_date.desc()).all()
    
    # Filter to only vested events that need info
    vests_needing_info = [v for v in all_vest_events if v.has_vested and v.needs_tax_info]
    
    return redirect(url_for('grants.list_grants', tab='lots'))


@grants_bp.route('/rules')
@login_required
def rules():
    """Legacy rules URL → add grant."""
    return redirect(url_for('grants.add_grant'))


@grants_bp.route('/finance-deep-dive')
@login_required
def finance_deep_dive():
    return redirect(url_for('grants.list_grants'))


@grants_bp.route('/finance-deep-dive-legacy')
@login_required
def finance_deep_dive_legacy():
    """Comprehensive tax and capital gains analysis."""
    from sqlalchemy.orm import joinedload

    grants = Grant.query.options(
        joinedload(Grant.vest_events)
    ).filter_by(user_id=current_user.id).all()

    all_vest_events = VestEvent.query.options(
        joinedload(VestEvent.grant)
    ).join(Grant).filter(
        Grant.user_id == current_user.id
    ).order_by(VestEvent.vest_date).all()

    # Index vests by grant once (avoid O(grants * vests) filtering)
    vests_by_grant = {}
    for ve in all_vest_events:
        vests_by_grant.setdefault(ve.grant_id, []).append(ve)

    # One decrypt pass for all as-of price lookups in this request
    from app.utils.price_utils import warm_user_price_history
    from app.utils.tax_engine import resolve_engine_profile_for_year
    from app.utils.sale_tax_estimate import (
        estimate_lots_sale_tax,
        lot_input_from_vest,
    )
    warm_user_price_history(current_user.id)
    latest_stock_price = get_latest_user_price(current_user.id) or 0.0
    today = date.today()

    # Market sales + ISO exercises so held value drops after selling
    from app.models.stock_sale import StockSale, ISOExercise
    from app.utils.portfolio_summary import sold_shares_by_vest
    sold_by_vest = sold_shares_by_vest(current_user.id)
    exercised_by_vest = {}
    for ex in ISOExercise.query.filter_by(user_id=current_user.id).all():
        exercised_by_vest[ex.vest_event_id] = (
            exercised_by_vest.get(ex.vest_event_id, 0.0) + float(ex.shares_exercised or 0)
        )

    # Cache year Tax Profiles — one engine surface for the whole page
    profile_by_year = {}

    def _profile(year: int):
        year = int(year)
        if year not in profile_by_year:
            profile_by_year[year] = resolve_engine_profile_for_year(current_user, year)
        return profile_by_year[year]

    total_shares_held_vested = 0.0
    total_shares_held_all = 0.0
    total_cost_basis_vested = 0.0
    total_cost_basis_all = 0.0
    total_current_value_vested = 0.0
    total_current_value_all = 0.0
    total_unrealized_gain_vested = 0.0
    total_unrealized_gain_all = 0.0

    analysis_data = []
    portfolio_lots_vested = []
    portfolio_lots_all = []

    for grant in grants:
        vest_events = vests_by_grant.get(grant.id, [])

        grant_shares_held_vested = 0.0
        grant_shares_held_all = 0.0
        grant_cost_basis_vested = 0.0
        grant_cost_basis_all = 0.0
        grant_current_value_vested = 0.0
        grant_current_value_all = 0.0
        grant_unrealized_gain_vested = 0.0
        grant_unrealized_gain_all = 0.0
        grant_estimated_tax_on_sale = 0.0
        enriched_vest_events = []

        for ve in vest_events:
            has_vested = ve.vest_date <= today
            # Sale-today estimates use *today's* tax year for stacking
            sale_year = today.year
            prof = _profile(sale_year)
            vest_prof = _profile(ve.vest_date.year if ve.vest_date else sale_year)

            tax_info = ve.estimate_tax_withholding(
                latest_stock_price, user=current_user, _tax_profile=vest_prof
            )
            tax_breakdown = ve.get_comprehensive_tax_breakdown(
                user=current_user, _tax_profile=vest_prof
            )
            sale_tax_data = ve.get_estimated_sale_tax(
                current_stock_price=latest_stock_price,
                total_sold=float(sold_by_vest.get(ve.id, 0) or 0),
                total_exercised=float(exercised_by_vest.get(ve.id, 0) or 0),
                user=current_user,
                _tax_profile=prof,
            )

            shares_held = sale_tax_data['shares_held']
            cost_basis = sale_tax_data['cost_basis']
            current_value = sale_tax_data['current_value']
            unrealized_gain = sale_tax_data['unrealized_gain']
            estimated_tax = sale_tax_data['estimated_tax']

            # Portfolio stack lots (correct multi-lot tax) for summary totals
            if shares_held > 0 and float(unrealized_gain or 0) > 0:
                lot = lot_input_from_vest(
                    ve,
                    shares=shares_held,
                    sale_price=latest_stock_price,
                    sale_date=today,
                    cost_basis_per_share=float(sale_tax_data.get('cost_basis_per_share') or 0),
                    user_id=current_user.id,
                )
                if lot:
                    portfolio_lots_all.append(lot)
                    if has_vested:
                        portfolio_lots_vested.append(lot)

            enriched_vest_events.append({
                'vest_event': ve,
                'has_vested': has_vested,
                'shares_held': shares_held,
                'cost_basis_per_share': sale_tax_data['cost_basis_per_share'],
                'cost_basis': cost_basis,
                'current_value': current_value,
                'unrealized_gain': unrealized_gain,
                'days_held': sale_tax_data['days_held'],
                'is_long_term': sale_tax_data['is_long_term'],
                'holding_period': sale_tax_data['holding_period'],
                'tax_amount': tax_info['tax_amount'],
                'tax_is_estimated': tax_info['is_estimated'],
                'tax_rate': tax_info['tax_rate'],
                'estimated_tax': estimated_tax,
                'tax_breakdown': tax_breakdown,
                'sale_method': sale_tax_data.get('method') or 'engine',
                'sale_federal': sale_tax_data.get('federal_tax') or 0,
                'sale_state': sale_tax_data.get('state_tax') or 0,
                'sale_niit': sale_tax_data.get('niit_tax') or 0,
            })

            grant_estimated_tax_on_sale += estimated_tax
            grant_shares_held_all += shares_held
            grant_cost_basis_all += cost_basis
            grant_current_value_all += current_value
            grant_unrealized_gain_all += unrealized_gain

            if has_vested:
                grant_shares_held_vested += shares_held
                grant_cost_basis_vested += cost_basis
                grant_current_value_vested += current_value
                grant_unrealized_gain_vested += unrealized_gain

        analysis_data.append({
            'grant': grant,
            'vest_events': enriched_vest_events,
            'shares_held_vested': grant_shares_held_vested,
            'shares_held_all': grant_shares_held_all,
            'cost_basis_vested': grant_cost_basis_vested,
            'cost_basis_all': grant_cost_basis_all,
            'current_value_vested': grant_current_value_vested,
            'current_value_all': grant_current_value_all,
            'unrealized_gain_vested': grant_unrealized_gain_vested,
            'unrealized_gain_all': grant_unrealized_gain_all,
            # Per-grant sum is standalone-lot estimates (row display). Portfolio KPIs use stacked total.
            'estimated_tax': grant_estimated_tax_on_sale,
        })

        total_shares_held_vested += grant_shares_held_vested
        total_shares_held_all += grant_shares_held_all
        total_cost_basis_vested += grant_cost_basis_vested
        total_cost_basis_all += grant_cost_basis_all
        total_current_value_vested += grant_current_value_vested
        total_current_value_all += grant_current_value_all
        total_unrealized_gain_vested += grant_unrealized_gain_vested
        total_unrealized_gain_all += grant_unrealized_gain_all

    # Stacked portfolio tax (one analyze_sales) — the number that matches Sales & Tax
    prof_today = _profile(today.year)
    stacked_vested = estimate_lots_sale_tax(
        current_user, portfolio_lots_vested, profile=prof_today, tax_year=today.year
    )
    stacked_all = estimate_lots_sale_tax(
        current_user, portfolio_lots_all, profile=prof_today, tax_year=today.year
    )
    total_estimated_tax = float(stacked_vested.get('estimated_tax') or 0)
    total_estimated_tax_all = float(stacked_all.get('estimated_tax') or 0)

    # Display rates from engine (not legacy User flat rates)
    rates_used = stacked_vested.get('rates_used') or {}
    tax_rates = {
        'federal': float(rates_used.get('ordinary_marginal') or 0),
        'state': float(
            rates_used.get('state_effective')
            or rates_used.get('state_marginal')
            or rates_used.get('state_ordinary')
            or 0
        ),
        'ltcg': float(rates_used.get('ltcg') or 0),
        'fica': 0.0,
        'total': float(stacked_vested.get('effective_rate') or 0),
        'method': 'engine',
        'tax_year': today.year,
    }

    return render_template(
        'grants/finance_deep_dive.html',
        analysis_data=analysis_data,
        latest_stock_price=latest_stock_price,
        total_shares_held_vested=total_shares_held_vested,
        total_shares_held_all=total_shares_held_all,
        total_cost_basis_vested=total_cost_basis_vested,
        total_cost_basis_all=total_cost_basis_all,
        total_current_value_vested=total_current_value_vested,
        total_current_value_all=total_current_value_all,
        total_unrealized_gain_vested=total_unrealized_gain_vested,
        total_unrealized_gain_all=total_unrealized_gain_all,
        total_estimated_tax=total_estimated_tax,
        total_estimated_tax_all=total_estimated_tax_all,
        tax_rates=tax_rates,
        engine_note=True,
    )


@grants_bp.route('/vest/<int:vest_id>', methods=['GET', 'POST'])
@login_required
def vest_detail(vest_id):
    """View and edit details for a specific vest event."""
    from app.models.stock_sale import StockSale, ISOExercise
    from sqlalchemy.orm import joinedload

    try:
        vest_event = VestEvent.query.options(
            joinedload(VestEvent.grant)
        ).get_or_404(vest_id)

        if vest_event.grant.user_id != current_user.id:
            flash('Access denied.', 'danger')
            return redirect(url_for('grants.list_grants'))

        if request.method == 'POST':
            vest_event.notes = request.form.get('notes', '').strip()
            db.session.commit()
            flash('Vest notes updated successfully!', 'success')
            return redirect(url_for('grants.vest_detail', vest_id=vest_id))

        try:
            user_key = current_user.get_decrypted_user_key() or b''
        except Exception as e:
            logger.error("Error getting user key: %s", e, exc_info=True)
            user_key = b''

        sales = StockSale.query.filter_by(vest_event_id=vest_id).order_by(
            StockSale.sale_date.desc()
        ).all()

        for sale in sales:
            if sale.capital_gain > 0:
                try:
                    sale.estimated_tax = sale.get_estimated_tax(user=current_user)
                except Exception:
                    sale.estimated_tax = None

        exercises = ISOExercise.query.filter_by(vest_event_id=vest_id).order_by(
            ISOExercise.exercise_date.desc()
        ).all()

        current_price = get_latest_user_price(current_user.id) or 0.0
        try:
            vest_data = vest_event.get_complete_data(
                user_key=user_key,
                current_price=current_price,
                sales_data=sales,
                exercises_data=exercises,
                user=current_user,
            )
            if 'error' in vest_data:
                flash(f"Warning: Some calculations unavailable: {vest_data['error']}", 'warning')
        except Exception as e:
            logger.error("get_complete_data failed for vest %s: %s", vest_id, e, exc_info=True)
            is_iso = vest_event.grant.share_type in ['iso_5y', 'iso_6y']
            vest_data = {
                'vest_id': vest_event.id,
                'has_vested': vest_event.has_vested,
                'is_iso': is_iso,
                'is_cash': vest_event.grant.share_type == 'cash',
                'shares_vested': vest_event.shares_vested,
                'price_at_vest': 0.0,
                'gross_value': 0.0,
                'shares_received': vest_event.shares_received,
                'net_value': 0.0,
                'current_price': current_price,
                'strike_price': vest_event.grant.share_price_at_grant if is_iso else None,
                'cost_basis_per_share': 0.0,
                'shares_sold': 0.0,
                'shares_exercised': 0.0,
                'shares_remaining': vest_event.shares_received,
                'tax_breakdown': None,
                'sale_tax_projection': None,
                'error': str(e),
            }
            flash(f'Warning: Some calculations unavailable: {str(e)}', 'warning')

        return render_template(
            'grants/vest_detail.html',
            vest_event=vest_event,
            grant=vest_event.grant,
            vest_data=vest_data,
            sales=sales,
            exercises=exercises,
        )

    except Exception as e:
        logger.error("Error in vest_detail route: %s", e, exc_info=True)
        db.session.rollback()
        flash(f'Error loading vest details: {str(e)}', 'danger')
        return redirect(url_for('grants.list_grants'))

@grants_bp.route('/sale-planning')
@login_required
def sale_planning():
    """Legacy URL — redirect to Plan."""
    from flask import redirect, url_for
    return redirect(url_for('tax_center.hub'))


@grants_bp.route('/sale-planning-legacy')
@login_required
def sale_planning_legacy():
    """Old sale planning UI (kept for reference)."""
    # Get all vest events (vested and unvested)
    vest_events = VestEvent.query.join(Grant).filter(
        Grant.user_id == current_user.id
    ).order_by(VestEvent.vest_date).all()
    
    # Get current stock price
    latest_stock_price = get_latest_user_price(current_user.id) or 0.0
    
    # Get user's tax rates from simple preferences
    tax_rates = current_user.get_tax_rates()
    
    # Get existing sale plans
    existing_plans = {}
    for plan in SalePlan.query.filter_by(user_id=current_user.id).all():
        existing_plans[plan.vest_event_id] = plan.planned_sale_year
    
    # Prepare vest data for frontend
    vest_data = []
    for vest in vest_events:
        vest_info = {
            'id': vest.id,
            'grant_id': vest.grant_id,
            'grant_type': vest.grant.grant_type,
            'share_type': vest.grant.share_type,
            'vest_date': vest.vest_date.isoformat(),
            'shares_vested': vest.shares_vested,
            'shares_received': vest.shares_received,
            'has_vested': vest.has_vested,
            'value_at_vest': float(vest.value_at_vest or 0),
            'current_value': float(vest.shares_received * latest_stock_price),
            'planned_year': existing_plans.get(vest.id),
            'strike_price': float(vest.grant.share_price_at_grant or 0)
        }
        vest_data.append(vest_info)
    
    # Years to display (2026-2035)
    current_year = date.today().year
    years = list(range(current_year, 2036))  # 2026-2035
    
    return render_template('grants/sale_planning_v2.html',
                         vests=vest_events,
                         vest_data=vest_data,
                         years=years,
                         tax_rates=tax_rates,
                         latest_stock_price=latest_stock_price)


@grants_bp.route('/api/sale-planning/save', methods=['POST'])
@login_required
def save_sale_plan():
    """Save user's sale plan (which vests to sell in which year)"""
    try:
        data = request.get_json()
        plans = data.get('plans', {})  # {vest_event_id: year}
        
        # Delete existing plans
        SalePlan.query.filter_by(user_id=current_user.id).delete()
        
        # Create new plans
        for vest_id_str, year in plans.items():
            vest_id = int(vest_id_str)
            if year:  # Only save if assigned to a year
                plan = SalePlan(
                    user_id=current_user.id,
                    vest_event_id=vest_id,
                    planned_sale_year=int(year)
                )
                db.session.add(plan)
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400


@grants_bp.route('/api/sale-planning/calculate-taxes', methods=['POST'])
@login_required
def calculate_sale_taxes():
    """Tax impact of selling specific vests — Tax Center engine only (one stack)."""
    try:
        from app.models.grant import Grant, ShareType
        from app.utils.sale_tax_estimate import estimate_lots_sale_tax, lot_input_from_vest
        from app.utils.tax_engine import resolve_engine_profile_for_year
        from sqlalchemy.orm import joinedload

        data = request.get_json() or {}
        year = int(data.get('year') or date.today().year)
        vest_ids = data.get('vest_ids', [])

        empty = {
            'success': True,
            'method': 'engine',
            'total_proceeds': 0.0,
            'total_ltcg': 0.0,
            'total_stcg': 0.0,
            'federal_tax_ltcg': 0.0,
            'federal_tax_stcg': 0.0,
            'state_tax': 0.0,
            'niit': 0.0,
            'total_tax': 0.0,
            'net_proceeds': 0.0,
            'ltcg_rate': 0.0,
            'stcg_rate': 0.0,
        }
        if not vest_ids:
            return jsonify(empty)

        vests = (
            VestEvent.query.options(joinedload(VestEvent.grant))
            .join(Grant)
            .filter(VestEvent.id.in_(vest_ids), Grant.user_id == current_user.id)
            .all()
        )
        if not vests:
            return jsonify({'success': False, 'error': 'No vests found'}), 400

        current_price = float(
            data.get('sale_price')
            if data.get('sale_price') is not None
            else (get_latest_user_price(current_user.id) or 0)
        )
        sale_date = date(year, 12, 31) if year < date.today().year else date.today()
        if data.get('sale_date'):
            try:
                sale_date = datetime.strptime(str(data['sale_date'])[:10], '%Y-%m-%d').date()
            except ValueError:
                pass

        lots = []
        for vest in vests:
            if not vest.grant or vest.grant.share_type == ShareType.CASH.value:
                continue
            is_iso = vest.grant.share_type in (ShareType.ISO_5Y.value, ShareType.ISO_6Y.value)
            from app.utils.shares import whole_shares
            shares = float(whole_shares(vest.shares_received or 0))
            if shares <= 0:
                continue
            if is_iso:
                basis = float(vest.grant.share_price_at_grant or 0)
            else:
                basis = float(vest.share_price_at_vest or 0) if vest.has_vested else current_price
            lot = lot_input_from_vest(
                vest,
                shares=shares,
                sale_price=current_price,
                sale_date=sale_date,
                cost_basis_per_share=basis,
                user_id=current_user.id,
            )
            if lot:
                lots.append(lot)

        profile = resolve_engine_profile_for_year(current_user, sale_date.year)
        result = estimate_lots_sale_tax(
            current_user, lots, profile=profile, tax_year=sale_date.year
        )
        rates = result.get('rates_used') or {}
        return jsonify({
            'success': True,
            'method': 'engine',
            'total_proceeds': float(result.get('total_proceeds') or 0),
            'total_ltcg': float(result.get('ltcg') or 0),
            'total_stcg': float(result.get('stcg') or 0),
            'federal_tax_ltcg': float(result.get('federal_ltcg_tax') or 0),
            'federal_tax_stcg': float(result.get('federal_ordinary_tax') or 0),
            'state_tax': float(result.get('state_tax') or 0),
            'niit': float(result.get('niit_tax') or 0),
            'total_tax': float(result.get('estimated_tax') or 0),
            'net_proceeds': float(result.get('after_tax_proceeds') or 0),
            'ltcg_rate': float(rates.get('ltcg') or 0) * 100,
            'stcg_rate': float(rates.get('ordinary_marginal') or 0) * 100,
            'warnings': result.get('warnings') or [],
        })

    except Exception as e:
        logger.error("Error in calculate_sale_taxes: %s", e, exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 400
