# NEW REFACTORED vest_detail ROUTE - Replace in app/routes/grants.py

@grants_bp.route('/vest/<int:vest_id>', methods=['GET', 'POST'])
@login_required
def vest_detail(vest_id):
    """View and edit details for a specific vest event - USES CENTRALIZED DATA METHOD."""
    from app.models.vest_event import VestEvent
    from app.models.stock_sale import StockSale
    from app.models.iso_exercise import ISOExercise
    from app.models.tax_rate import UserTaxProfile
    from app.models.annual_income import AnnualIncome
    import logging
    import os
    
    logger = logging.getLogger(__name__)
    
    try:
        logger.info(f"Loading vest {vest_id} for user {current_user.id}")
        vest_event = VestEvent.query.get_or_404(vest_id)
        
        # Security check
        if vest_event.grant.user_id != current_user.id:
            flash('Access denied.', 'danger')
            return redirect(url_for('grants.list_grants'))
        
        # Handle POST (update notes)
        if request.method == 'POST':
            vest_event.notes = request.form.get('notes', '').strip()
            db.session.commit()
            flash('Vest notes updated successfully!', 'success')
            return redirect(url_for('grants.vest_detail', vest_id=vest_id))
        
        # Get user's decryption key
        user_key = current_user.get_decrypted_user_key()
        
        # Get tax data
        tax_profile = UserTaxProfile.query.filter_by(user_id=current_user.id).first()
        annual_incomes_list = AnnualIncome.query.filter_by(user_id=current_user.id).all()
        annual_incomes_dict = {ai.year: ai.annual_income for ai in annual_incomes_list}
        
        # Get sales and exercises
        sales = StockSale.query.filter_by(vest_event_id=vest_id).order_by(
            StockSale.sale_date.desc()
        ).all()
        
        # Add estimated tax to each sale
        for sale in sales:
            if sale.capital_gain > 0:
                try:
                    sale.estimated_tax = sale.get_estimated_tax()
                except Exception:
                    sale.estimated_tax = None
        
        exercises = ISOExercise.query.filter_by(vest_event_id=vest_id).order_by(
            ISOExercise.exercise_date.desc()
        ).all()
        
        # *** SINGLE SOURCE OF TRUTH - GET ALL DATA FROM ONE METHOD ***
        vest_data = vest_event.get_complete_data(
            user_key=user_key,
            current_price=None,  # Will fetch latest
            tax_profile=tax_profile,
            annual_incomes=annual_incomes_dict,
            sales_data=sales,
            exercises_data=exercises
        )
        
        return render_template('grants/vest_detail.html',
                             vest_event=vest_event,
                             grant=vest_event.grant,
                             vest_data=vest_data,  # ALL DATA IN ONE PLACE
                             sales=sales,
                             exercises=exercises)
        
    except Exception as e:
        logger.error(f"Error in vest_detail: {e}", exc_info=True)
        db.session.rollback()
        flash(f'Error loading vest details: {str(e)}', 'danger')
        return redirect(url_for('grants.list_grants'))
