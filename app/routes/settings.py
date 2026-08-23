"""
User settings — account, Grok key, and pre-IPO price marks.

Tax profile (wages, filing, CA engine, AMT credits) lives under Plan.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app import db

settings_bp = Blueprint('settings', __name__, url_prefix='/settings')


@settings_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """Account settings: appearance, Grok API key, pre-IPO prices."""
    if request.method == 'POST':
        try:
            # Theme is primarily client-side (localStorage); accept preference for future use
            theme = (request.form.get('theme') or 'dark').strip().lower()
            if theme not in ('dark', 'light'):
                theme = 'dark'

            # Per-user encrypted xAI API key
            if request.form.get('clear_xai_api_key') == 'on':
                current_user.clear_xai_api_key()
            else:
                new_key = (request.form.get('xai_api_key') or '').strip()
                if new_key:
                    current_user.set_xai_api_key(new_key)
            model = (request.form.get('xai_model') or '').strip()
            current_user.xai_model = model or None

            db.session.commit()
            flash('Settings saved.', 'success')
            # Pass theme back so the page can apply it immediately if JS missed it
            return redirect(url_for('settings.profile', theme=theme))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving settings: {str(e)}', 'danger')

    xai_hint = None
    try:
        xai_hint = current_user.xai_key_hint() if current_user.has_xai_api_key() else None
    except Exception:
        xai_hint = (
            '•••• (saved; unlock failed — check VESTX_MASTER_KEY)'
            if current_user.has_xai_api_key()
            else None
        )

    from app.utils.price_utils import list_private_user_prices
    from app.utils.market_data import public_market_start, stock_ticker

    try:
        private_prices = list_private_user_prices(current_user.id)
    except Exception:
        private_prices = []

    return render_template(
        'settings/profile.html',
        user=current_user,
        xai_key_hint=xai_hint,
        xai_key_saved=current_user.has_xai_api_key(),
        private_prices=private_prices,
        price_cutover=public_market_start(),
        ticker=stock_ticker(),
    )


@settings_bp.route('/tax', methods=['GET', 'POST'])
@login_required
def tax_settings():
    """Old tax-settings URL → Sales & Tax profile."""
    return redirect(url_for('tax_center.tax_profile'))
