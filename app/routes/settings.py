"""
User settings routes - tax configuration, preferences.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models.tax_rate import UserTaxProfile, TaxBracket
from app.utils.tax_calculator import get_all_us_states, NO_INCOME_TAX_STATES

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
    
    return render_template(
        'settings/tax.html',
        tax_profile=tax_profile,
        calculated_rates=rates,
        all_states=all_states,
        no_tax_states=NO_INCOME_TAX_STATES
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
