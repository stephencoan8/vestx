"""
Grant management routes - view, add, edit, delete grants.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models.grant import Grant, GrantType, ShareType
from app.models.vest_event import VestEvent
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
    """Legacy URL → lots."""
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
    """Legacy Finance URL -> Grants."""
    return redirect(url_for('grants.list_grants'))


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
    """Legacy sale planner -> Plan."""
    return redirect(url_for('tax_center.hub'))


@grants_bp.route('/api/sale-planning/save', methods=['POST'])
@login_required
def save_sale_plan():
    return jsonify({'success': False, 'error': 'Moved to Plan', 'redirect': '/tax/'}), 410


@grants_bp.route('/api/sale-planning/calculate-taxes', methods=['POST'])
@login_required
def calculate_sale_taxes():
    return jsonify({'success': False, 'error': 'Moved to Plan', 'redirect': '/tax/'}), 410
