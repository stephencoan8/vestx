"""
Grant management routes - view, add, edit, delete grants.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models.grant import Grant, GrantType, ShareType
from app.models.vest_event import VestEvent
from app.utils.vest_calculator import calculate_vest_schedule, get_grant_configuration
from app.utils.init_db import get_stock_price_at_date
from datetime import datetime, date

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
                    share_price_at_vest=get_stock_price_at_date(vest['vest_date'])
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
            share_type = request.form.get('share_type')
            share_quantity = float(request.form.get('share_quantity'))
            bonus_type = request.form.get('bonus_type')
            vest_years = request.form.get('vest_years')
            notes = request.form.get('notes', '')
            
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
            grant.notes = notes
            
            # Delete old vest events
            VestEvent.query.filter_by(grant_id=grant.id).delete()
            
            # Recalculate and create new vest events
            vest_schedule = calculate_vest_schedule(grant)
            for vest in vest_schedule:
                vest_event = VestEvent(
                    grant_id=grant.id,
                    vest_date=vest['vest_date'],
                    shares_vested=vest['shares'],
                    share_price_at_vest=get_stock_price_at_date(vest['vest_date'])
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
        payment_method = request.form.get('payment_method', 'sell_to_cover')
        cash_to_cover = float(request.form.get('cash_to_cover', 0) or 0)
        shares_sold = float(request.form.get('shares_sold_to_cover', 0) or 0)
        
        print(f"DEBUG: Updating vest event {event_id}")
        print(f"DEBUG: Payment method: {payment_method}")
        print(f"DEBUG: Cash to cover: {cash_to_cover}")
        print(f"DEBUG: Shares sold: {shares_sold}")
        
        # Update payment method
        vest_event.payment_method = payment_method
        
        # Update amounts based on payment method
        if payment_method == 'cash_to_cover':
            vest_event.cash_to_cover = cash_to_cover
            vest_event.shares_sold_to_cover = 0  # Clear shares sold
        else:  # sell_to_cover
            vest_event.shares_sold_to_cover = shares_sold
            vest_event.cash_to_cover = 0  # Clear cash
        
        # Commit to database
        db.session.commit()
        
        print(f"DEBUG: Saved - payment_method={vest_event.payment_method}, cash={vest_event.cash_to_cover}, shares_sold={vest_event.shares_sold_to_cover}")
        
        # Return calculated values
        return jsonify({
            'success': True, 
            'message': 'Vest event updated',
            'payment_method': vest_event.payment_method,
            'cash_to_cover': vest_event.cash_to_cover,
            'shares_sold_to_cover': vest_event.shares_sold_to_cover,
            'shares_withheld': vest_event.shares_withheld_for_taxes,
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
