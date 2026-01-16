"""
Transaction routes for stock sales and ISO exercises.
"""

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from app import db
from app.models.stock_sale import StockSale, ISOExercise
from app.models.vest_event import VestEvent
from app.models.grant import Grant
from datetime import datetime, date
import logging

logger = logging.getLogger(__name__)

transactions_bp = Blueprint('transactions', __name__)


@transactions_bp.route('/transactions')
@login_required
def transactions_page():
    """Main transactions page showing sales and exercises."""
    # Get all stock sales
    sales = StockSale.query.filter_by(user_id=current_user.id).order_by(
        StockSale.sale_date.desc()
    ).all()
    
    # Get all ISO exercises
    exercises = ISOExercise.query.filter_by(user_id=current_user.id).order_by(
        ISOExercise.exercise_date.desc()
    ).all()
    
    # Get available vests for dropdowns
    vests = VestEvent.query.join(Grant).filter(
        Grant.user_id == current_user.id,
        VestEvent.vest_date <= date.today()
    ).order_by(VestEvent.vest_date.desc()).all()
    
    return render_template(
        'transactions/transactions.html',
        sales=sales,
        exercises=exercises,
        vests=vests
    )


@transactions_bp.route('/api/transactions/sales', methods=['POST'])
@login_required
def create_sale():
    """Create a new stock sale."""
    try:
        data = request.get_json()
        
        sale_date = datetime.fromisoformat(data['sale_date']).date()
        shares_sold = float(data['shares_sold'])
        sale_price = float(data['sale_price'])
        vest_event_id = data.get('vest_event_id')
        
        # Get cost basis from vest event if provided
        cost_basis_per_share = float(data['cost_basis_per_share'])
        
        total_proceeds = shares_sold * sale_price
        total_cost_basis = shares_sold * cost_basis_per_share
        capital_gain = total_proceeds - total_cost_basis
        
        # Determine if long-term (> 1 year)
        is_long_term = False
        if vest_event_id:
            vest = VestEvent.query.get(vest_event_id)
            if vest:
                holding_days = (sale_date - vest.vest_date).days
                is_long_term = holding_days > 365
        
        sale = StockSale(
            user_id=current_user.id,
            vest_event_id=vest_event_id,
            sale_date=sale_date,
            shares_sold=shares_sold,
            sale_price=sale_price,
            total_proceeds=total_proceeds,
            cost_basis_per_share=cost_basis_per_share,
            total_cost_basis=total_cost_basis,
            capital_gain=capital_gain,
            is_long_term=is_long_term,
            commission_fees=float(data.get('commission_fees', 0)),
            notes=data.get('notes', '')
        )
        
        db.session.add(sale)
        db.session.commit()
        
        return jsonify({
            'id': sale.id,
            'message': 'Stock sale recorded successfully'
        }), 201
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating sale: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 400


@transactions_bp.route('/api/transactions/sales/<int:sale_id>', methods=['DELETE'])
@login_required
def delete_sale(sale_id):
    """Delete a stock sale."""
    try:
        sale = StockSale.query.filter_by(
            id=sale_id,
            user_id=current_user.id
        ).first_or_404()
        
        db.session.delete(sale)
        db.session.commit()
        
        return jsonify({'message': 'Sale deleted successfully'})
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting sale: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 400


@transactions_bp.route('/api/transactions/exercises', methods=['POST'])
@login_required
def create_exercise():
    """Create a new ISO exercise."""
    try:
        data = request.get_json()
        
        exercise_date = datetime.fromisoformat(data['exercise_date']).date()
        shares_exercised = float(data['shares_exercised'])
        strike_price = float(data['strike_price'])
        fmv_at_exercise = float(data['fmv_at_exercise'])
        vest_event_id = data.get('vest_event_id')
        
        # Calculate bargain element
        bargain_element_per_share = fmv_at_exercise - strike_price
        total_bargain_element = shares_exercised * bargain_element_per_share
        
        # Get grant date from vest event
        grant_date = None
        if vest_event_id:
            vest = VestEvent.query.get(vest_event_id)
            if vest and vest.grant:
                grant_date = vest.grant.grant_date
        
        exercise = ISOExercise(
            user_id=current_user.id,
            vest_event_id=vest_event_id,
            exercise_date=exercise_date,
            shares_exercised=shares_exercised,
            strike_price=strike_price,
            fmv_at_exercise=fmv_at_exercise,
            bargain_element_per_share=bargain_element_per_share,
            total_bargain_element=total_bargain_element,
            amt_triggered=data.get('amt_triggered', False),
            amt_paid=data.get('amt_paid'),
            amt_credit_generated=data.get('amt_credit_generated'),
            cash_paid=data.get('cash_paid'),
            shares_still_held=shares_exercised,  # Initially all shares held
            grant_date=grant_date,
            notes=data.get('notes', '')
        )
        
        db.session.add(exercise)
        db.session.commit()
        
        return jsonify({
            'id': exercise.id,
            'message': 'ISO exercise recorded successfully'
        }), 201
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating exercise: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 400


@transactions_bp.route('/api/transactions/exercises/<int:exercise_id>', methods=['DELETE'])
@login_required
def delete_exercise(exercise_id):
    """Delete an ISO exercise."""
    try:
        exercise = ISOExercise.query.filter_by(
            id=exercise_id,
            user_id=current_user.id
        ).first_or_404()
        
        db.session.delete(exercise)
        db.session.commit()
        
        return jsonify({'message': 'Exercise deleted successfully'})
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting exercise: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 400
