"""
Grant management routes - view, add, edit, delete grants.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models.grant import Grant, GrantType, ShareType
from app.models.vest_event import VestEvent
from app.models.stock_price import StockPrice
from app.utils.vest_calculator import calculate_vest_schedule, get_grant_configuration
from app.utils.init_db import get_stock_price_at_date, get_latest_stock_price
from app.models.tax_rate import UserTaxProfile
from datetime import datetime, date, timedelta
import logging

logging.basicConfig(level=logging.DEBUG)

grants_bp = Blueprint('grants', __name__, url_prefix='/grants')


@grants_bp.route('/')
@login_required
def list_grants():
    """List all user grants."""
    grants = Grant.query.filter_by(user_id=current_user.id).order_by(Grant.grant_date.desc()).all()
    return render_template('grants/list.html', grants=grants)


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
            share_quantity = float(request.form.get('share_quantity'))
            bonus_type = request.form.get('bonus_type')
            vest_years = request.form.get('vest_years')
            notes = request.form.get('notes', '')
            
            # ESPP discount (typically 15% = 0.15)
            espp_discount = request.form.get('espp_discount')
            if espp_discount:
                espp_discount = float(espp_discount)
            else:
                # Default 15% for ESPP, 0% for others
                espp_discount = 0.15 if grant_type == 'espp' else 0.0
            
            # Get stock price at grant date
            share_price = get_stock_price_at_date(grant_date)
            
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
                    shares_vested=vest['shares']
                )
                db.session.add(vest_event)
            
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
            share_quantity = float(request.form.get('share_quantity'))
            bonus_type = request.form.get('bonus_type') or None
            vest_years = request.form.get('vest_years') or None
            notes = request.form.get('notes', '')
            
            # ESPP discount (typically 15% = 0.15)
            espp_discount = request.form.get('espp_discount')
            if espp_discount:
                espp_discount = float(espp_discount)
            else:
                # Default 15% for ESPP, 0% for others
                espp_discount = 0.15 if grant_type == 'espp' else 0.0
            
            # Get stock price at grant date
            share_price = get_stock_price_at_date(grant_date)
            
            # Get vesting configuration
            if vest_years:
                vest_years = int(vest_years)
                cliff_years = 1.0  # Default
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
            grant.bonus_type = bonus_type
            grant.espp_discount = espp_discount
            grant.notes = notes
            
            # Delete old vest events and recalculate
            VestEvent.query.filter_by(grant_id=grant.id).delete()
            
            # Recalculate and create new vest events
            vest_schedule = calculate_vest_schedule(grant)
            
            for vest in vest_schedule:
                vest_event = VestEvent(
                    grant_id=grant.id,
                    vest_date=vest['vest_date'],
                    shares_vested=vest['shares']
                )
                db.session.add(vest_event)
            
            db.session.commit()
            
            flash('Grant updated successfully!', 'success')
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
        # New simplified tax fields
        cash_paid = float(request.form.get('cash_paid', 0) or 0)
        cash_covered_all = request.form.get('cash_covered_all', 'true').lower() == 'true'
        shares_sold = float(request.form.get('shares_sold', 0) or 0)
        
        # Update vest event with new fields
        vest_event.cash_paid = cash_paid
        vest_event.cash_covered_all = cash_covered_all
        vest_event.shares_sold = shares_sold if not cash_covered_all else 0
        
        # Commit to database
        db.session.commit()
        
        # Return calculated values
        return jsonify({
            'success': True, 
            'message': 'Vest event updated',
            'cash_paid': vest_event.cash_paid,
            'cash_covered_all': vest_event.cash_covered_all,
            'shares_sold': vest_event.shares_sold,
            'shares_received': vest_event.shares_received,
            'net_value': vest_event.net_value
        })
    
    except Exception as e:
        db.session.rollback()
        print(f"ERROR: Failed to update vest event: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@grants_bp.route('/schedule')
@login_required
def vest_schedule():
    """View complete vesting schedule."""
    vest_events = VestEvent.query.join(Grant).filter(
        Grant.user_id == current_user.id
    ).order_by(VestEvent.vest_date).all()
    
    return render_template('grants/schedule.html', vest_events=vest_events)


@grants_bp.route('/rules')
@login_required
def rules():
    """View vesting rules and configurations."""
    return render_template('grants/rules.html')


@grants_bp.route('/finance-deep-dive')
@login_required
def finance_deep_dive():
    """Comprehensive tax and capital gains analysis."""
    # Get all grants and vest events for the user
    grants = Grant.query.filter_by(user_id=current_user.id).all()
    all_vest_events = VestEvent.query.join(Grant).filter(
        Grant.user_id == current_user.id
    ).order_by(VestEvent.vest_date).all()
    
    # Get latest stock price for current value estimation
    latest_stock_price = get_latest_stock_price() or 0.0
    today = date.today()
    
    # Initialize totals
    total_shares_held_vested = 0.0
    total_shares_held_all = 0.0
    total_cost_basis_vested = 0.0
    total_cost_basis_all = 0.0
    total_current_value_vested = 0.0
    total_current_value_all = 0.0
    total_unrealized_gain_vested = 0.0
    total_unrealized_gain_all = 0.0
    
    # Prepare data for analysis
    analysis_data = []
    
    for grant in grants:
        vest_events = [ve for ve in all_vest_events if ve.grant_id == grant.id]
        
        # Initialize grant-level totals
        grant_shares_held_vested = 0.0
        grant_shares_held_all = 0.0
        grant_cost_basis_vested = 0.0
        grant_cost_basis_all = 0.0
        grant_current_value_vested = 0.0
        grant_current_value_all = 0.0
        grant_unrealized_gain_vested = 0.0
        grant_unrealized_gain_all = 0.0
        
        # Enrich vest event data
        enriched_vest_events = []
        is_cash_grant = grant.share_type == 'cash'
        
        for ve in vest_events:
            has_vested = ve.vest_date <= today
            
            # Calculate estimated or actual taxes
            tax_info = ve.estimate_tax_withholding(latest_stock_price)
            
            # For cash grants, shares_vested/shares_received represent USD amounts
            if is_cash_grant:
                shares_held = ve.shares_received if has_vested else ve.shares_vested
                cost_basis_per_share = 1.0  # $1 per $1 for cash
                cost_basis = shares_held  # USD amount
                current_value = shares_held  # Cash value doesn't change
                unrealized_gain = 0.0  # No gain/loss on cash
            else:
                # Stock grants
                shares_held = ve.shares_received if has_vested else ve.shares_vested
                cost_basis_per_share = ve.share_price_at_vest
                cost_basis = shares_held * cost_basis_per_share
                current_value = shares_held * latest_stock_price
                unrealized_gain = current_value - cost_basis
            
            days_held = (today - ve.vest_date).days if has_vested else 0
            is_long_term = days_held >= 365
            
            ve_data = {
                'vest_event': ve,
                'has_vested': has_vested,
                'shares_held': shares_held,
                'cost_basis_per_share': cost_basis_per_share,
                'cost_basis': cost_basis,
                'current_value': current_value,
                'unrealized_gain': unrealized_gain,
                'days_held': days_held,
                'is_long_term': is_long_term,
                'tax_amount': tax_info['tax_amount'],
                'tax_is_estimated': tax_info['is_estimated'],
                'tax_rate': tax_info['tax_rate']
            }
            enriched_vest_events.append(ve_data)
            
            # Add to grant totals
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
            'unrealized_gain_all': grant_unrealized_gain_all
        })
        
        # Add to overall totals
        total_shares_held_vested += grant_shares_held_vested
        total_shares_held_all += grant_shares_held_all
        total_cost_basis_vested += grant_cost_basis_vested
        total_cost_basis_all += grant_cost_basis_all
        total_current_value_vested += grant_current_value_vested
        total_current_value_all += grant_current_value_all
        total_unrealized_gain_vested += grant_unrealized_gain_vested
        total_unrealized_gain_all += grant_unrealized_gain_all
    
    # Get user's tax profile and calculate rates
    tax_profile = UserTaxProfile.query.filter_by(user_id=current_user.id).first()
    if tax_profile:
        tax_rates = tax_profile.get_tax_rates()
        use_manual_rates = tax_profile.use_manual_rates
    else:
        # Default rates if no profile exists
        tax_rates = {'federal': 0.24, 'state': 0.093, 'ltcg': 0.15}
        use_manual_rates = True

    # Pass all required data to the template
    return render_template('grants/finance_deep_dive.html',
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
                           tax_rates=tax_rates,
                           use_manual_rates=use_manual_rates)
