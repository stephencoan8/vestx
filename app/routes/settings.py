"""
User settings routes - tax configuration, preferences.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models.tax_rate import UserTaxProfile, TaxBracket
from app.models.annual_income import AnnualIncome
from app.models.vest_event import VestEvent
from app.utils.tax_calculator import get_all_us_states, NO_INCOME_TAX_STATES
from datetime import date

settings_bp = Blueprint('settings', __name__, url_prefix='/settings')


@settings_bp.route('/tax', methods=['GET', 'POST'])
@login_required
def tax_settings():
    """Tax configuration settings."""
    # Get or create tax profile
    tax_profile = UserTaxProfile.query.filter_by(user_id=current_user.id).first()
    if not tax_profile:
        tax_profile = UserTaxProfile(
            user_id=current_user.id,
            filing_status='single',
            use_manual_rates=False
        )
        db.session.add(tax_profile)
        db.session.commit()
    
    if request.method == 'POST':
        try:
            # Get form data
            use_manual = request.form.get('use_manual_rates') == 'true'
            
            if use_manual:
                # Manual rates
                tax_profile.use_manual_rates = True
                tax_profile.manual_federal_rate = float(request.form.get('manual_federal_rate', 0)) / 100
                tax_profile.manual_state_rate = float(request.form.get('manual_state_rate', 0)) / 100
                tax_profile.manual_ltcg_rate = float(request.form.get('manual_ltcg_rate', 0)) / 100
            else:
                # Automatic rates
                tax_profile.use_manual_rates = False
                tax_profile.state = request.form.get('state', '').upper() or None
                tax_profile.filing_status = request.form.get('filing_status', 'single')
                annual_income_str = request.form.get('annual_income', '')
                tax_profile.annual_income = float(annual_income_str) if annual_income_str else None
                ytd_wages_str = request.form.get('ytd_wages', '')
                tax_profile.ytd_wages = float(ytd_wages_str) if ytd_wages_str else 0.0
            
            db.session.commit()
            flash('Tax settings saved successfully!', 'success')
            return redirect(url_for('settings.tax_settings'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving tax settings: {str(e)}', 'danger')
    
    # Get calculated rates for display
    rates = tax_profile.get_tax_rates()
    
    # Get all US states for dropdown
    all_states = get_all_us_states()
    
    # Get years where user has vested events
    vest_years = db.session.query(
        db.func.extract('year', VestEvent.vest_date).label('year')
    ).join(
        VestEvent.grant
    ).filter(
        VestEvent.grant.has(user_id=current_user.id)
    ).distinct().order_by('year').all()
    
    vest_years = [int(year[0]) for year in vest_years]
    
    # Get existing annual income records
    annual_incomes = {
        ai.year: ai.annual_income 
        for ai in AnnualIncome.query.filter_by(user_id=current_user.id).all()
    }
    
    # Get current year
    from datetime import date
    current_year = date.today().year
    
    return render_template(
        'settings/tax.html',
        tax_profile=tax_profile,
        calculated_rates=rates,
        all_states=all_states,
        no_tax_states=NO_INCOME_TAX_STATES,
        vest_years=vest_years,
        annual_incomes=annual_incomes,
        current_year=current_year
    )


@settings_bp.route('/tax/calculate-rates', methods=['POST'])
@login_required
def calculate_rates_preview():
    """AJAX endpoint to preview tax rates without saving."""
    try:
        data = request.get_json()
        state = data.get('state', '').upper() or None
        filing_status = data.get('filing_status', 'single')
        annual_income = float(data.get('annual_income', 0)) if data.get('annual_income') else None
        
        if not annual_income:
            return jsonify({'error': 'Annual income required'}), 400
        
        # Create temporary profile to calculate rates
        temp_profile = UserTaxProfile(
            user_id=current_user.id,
            state=state,
            filing_status=filing_status,
            annual_income=annual_income,
            use_manual_rates=False
        )
        
        rates = temp_profile.get_tax_rates()
        
        return jsonify({
            'federal': round(rates['federal'] * 100, 2),
            'state': round(rates['state'] * 100, 2),
            'ltcg': round(rates['ltcg'] * 100, 2)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@settings_bp.route('/tax/save-annual-income', methods=['POST'])
@login_required
def save_annual_income():
    """Save annual income for a specific year."""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        year = data.get('year')
        annual_income = data.get('annual_income')
        
        if not year:
            return jsonify({'error': 'Year is required'}), 400
            
        if not annual_income:
            return jsonify({'error': 'Annual income is required'}), 400
        
        year = int(year)
        annual_income = float(annual_income)
        
        # Get or create annual income record
        income_record = AnnualIncome.query.filter_by(
            user_id=current_user.id,
            year=year
        ).first()
        
        if income_record:
            income_record.annual_income = annual_income
        else:
            income_record = AnnualIncome(
                user_id=current_user.id,
                year=year,
                annual_income=annual_income
            )
            db.session.add(income_record)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Saved {year} income: ${annual_income:,.0f}'
        })
        
    except ValueError as e:
        return jsonify({'error': f'Invalid number format: {str(e)}'}), 400
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
