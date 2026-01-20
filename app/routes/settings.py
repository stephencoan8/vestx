"""
User settings routes - tax configuration, preferences.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app import db

settings_bp = Blueprint('settings', __name__, url_prefix='/settings')

# Common US state tax rates (simplified - top marginal rates)
STATE_TAX_RATES = {
    'AL': 0.05, 'AK': 0.0, 'AZ': 0.045, 'AR': 0.059, 'CA': 0.093,
    'CO': 0.0463, 'CT': 0.0699, 'DE': 0.066, 'FL': 0.0, 'GA': 0.0575,
    'HI': 0.11, 'ID': 0.06, 'IL': 0.0495, 'IN': 0.0323, 'IA': 0.0853,
    'KS': 0.057, 'KY': 0.05, 'LA': 0.06, 'ME': 0.0715, 'MD': 0.0575,
    'MA': 0.05, 'MI': 0.0425, 'MN': 0.0985, 'MS': 0.05, 'MO': 0.054,
    'MT': 0.069, 'NE': 0.0684, 'NV': 0.0, 'NH': 0.0, 'NJ': 0.1075,
    'NM': 0.059, 'NY': 0.0882, 'NC': 0.0499, 'ND': 0.029, 'OH': 0.0399,
    'OK': 0.05, 'OR': 0.099, 'PA': 0.0307, 'RI': 0.0599, 'SC': 0.07,
    'SD': 0.0, 'TN': 0.0, 'TX': 0.0, 'UT': 0.0495, 'VT': 0.0875,
    'VA': 0.0575, 'WA': 0.0, 'WV': 0.065, 'WI': 0.0765, 'WY': 0.0,
    'DC': 0.1075
}

# Federal tax brackets (2024)
FEDERAL_TAX_BRACKETS = [
    {'rate': 0.10, 'label': '10%'},
    {'rate': 0.12, 'label': '12%'},
    {'rate': 0.22, 'label': '22% (default)'},
    {'rate': 0.24, 'label': '24%'},
    {'rate': 0.32, 'label': '32%'},
    {'rate': 0.35, 'label': '35%'},
    {'rate': 0.37, 'label': '37%'},
]


@settings_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """User profile and tax preferences."""
    if request.method == 'POST':
        try:
            # Get tax preferences from form
            federal_rate = float(request.form.get('federal_tax_rate', 0.22))
            state_code = request.form.get('state_code', '')
            custom_state_rate = request.form.get('custom_state_rate', '')
            include_fica = request.form.get('include_fica') == 'on'
            ss_wage_base_maxed = request.form.get('ss_wage_base_maxed') == 'on'
            
            # Determine state rate - custom rate takes priority over state selection
            if custom_state_rate:
                # Custom rate entered - use it (convert percentage to decimal)
                state_rate = float(custom_state_rate) / 100
            elif state_code and state_code in STATE_TAX_RATES:
                # State selected - use predefined rate
                state_rate = STATE_TAX_RATES[state_code]
            else:
                # No state or custom rate
                state_rate = 0.0
            
            # Update user tax preferences
            current_user.federal_tax_rate = federal_rate
            current_user.state_tax_rate = state_rate
            current_user.include_fica = include_fica
            current_user.ss_wage_base_maxed = ss_wage_base_maxed
            
            db.session.commit()
            flash('Tax preferences saved successfully!', 'success')
            return redirect(url_for('settings.profile'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving preferences: {str(e)}', 'danger')
    
    # Calculate current rates for display
    rates = current_user.get_tax_rates()
    
    # Find current state code if applicable
    current_state = None
    for code, rate in STATE_TAX_RATES.items():
        if abs(rate - (current_user.state_tax_rate or 0.0)) < 0.0001:
            current_state = code
            break
    
    return render_template(
        'settings/profile.html',
        user=current_user,
        rates=rates,
        federal_brackets=FEDERAL_TAX_BRACKETS,
        state_tax_rates=STATE_TAX_RATES,
        current_state=current_state
    )


# Redirect old tax settings URL to new profile page
@settings_bp.route('/tax', methods=['GET', 'POST'])
@login_required
def tax_settings():
    """Redirect old tax settings to new profile page."""
    return redirect(url_for('settings.profile'))
