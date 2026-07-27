"""
Admin routes - manage stock prices, view users.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from functools import wraps
from app import db
from app.models.stock_price import StockPrice
from app.models.user import User
from datetime import datetime

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def admin_required(f):
    """Decorator to require admin access."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash('Admin access required', 'error')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated_function


@admin_bp.route('/')
@admin_required
def dashboard():
    """Admin dashboard."""
    user_count = User.query.count()
    latest_price = StockPrice.query.order_by(StockPrice.valuation_date.desc()).first()
    
    return render_template('admin/dashboard.html',
                         user_count=user_count,
                         latest_price=latest_price)


@admin_bp.route('/stock-prices')
@admin_required
def stock_prices():
    """View and manage stock prices."""
    prices = StockPrice.query.order_by(StockPrice.valuation_date.desc()).all()
    return render_template('admin/stock_prices.html', prices=prices)


@admin_bp.route('/stock-prices/add', methods=['POST'])
@admin_required
def add_stock_price():
    """Add a new stock price."""
    try:
        valuation_date = datetime.strptime(request.form.get('valuation_date'), '%Y-%m-%d').date()
        price = float(request.form.get('price'))
        notes = request.form.get('notes', '')
        
        # Check if price already exists for this date
        existing = StockPrice.query.filter_by(valuation_date=valuation_date).first()
        if existing:
            flash('A price already exists for this date', 'error')
            return redirect(url_for('admin.stock_prices'))
        
        stock_price = StockPrice(
            valuation_date=valuation_date,
            price_per_share=price,
            notes=notes,
            created_by=current_user.id
        )
        
        db.session.add(stock_price)
        db.session.commit()
        flash('Stock price added successfully', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding stock price: {str(e)}', 'error')
    
    return redirect(url_for('admin.stock_prices'))


@admin_bp.route('/stock-prices/<int:price_id>/delete', methods=['POST'])
@admin_required
def delete_stock_price(price_id):
    """Delete a stock price."""
    price = StockPrice.query.get_or_404(price_id)
    db.session.delete(price)
    db.session.commit()
    flash('Stock price deleted', 'success')
    return redirect(url_for('admin.stock_prices'))


@admin_bp.route('/stock-prices/chart-data')
@admin_required
def stock_price_chart_data():
    """Get stock price data for chart."""
    prices = StockPrice.query.order_by(StockPrice.valuation_date).all()
    
    data = {
        'dates': [p.valuation_date.isoformat() for p in prices],
        'prices': [p.price_per_share for p in prices]
    }
    
    return jsonify(data)


@admin_bp.route('/users')
@admin_required
def users():
    """View all users."""
    all_users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=all_users)


@admin_bp.route('/apply-stock-split', methods=['POST'])
@admin_required
def apply_stock_split():
    """
    One-shot 5:1 (or custom ratio) restatement for the logged-in admin's own account.
    Idempotent: refuses if this ratio was already applied unless confirm_force is set.
    """
    from app.utils.stock_split import apply_stock_split_for_user, already_applied

    try:
        ratio = float(request.form.get('ratio', 5))
    except (TypeError, ValueError):
        flash('Invalid ratio', 'error')
        return redirect(url_for('admin.dashboard'))

    force = request.form.get('force') == '1'
    confirm = request.form.get('confirm') == 'APPLY'

    if not confirm:
        flash('Split not applied — confirmation checkbox required.', 'error')
        return redirect(url_for('admin.dashboard'))

    if already_applied(current_user.id, ratio) and not force:
        flash(
            f'{ratio:g}:1 split already applied for your account. '
            'Check "Force re-apply" only if you know you need it.',
            'error',
        )
        return redirect(url_for('admin.dashboard'))

    try:
        stats = apply_stock_split_for_user(current_user, ratio=ratio, force=force)
        flash(
            f'Applied {ratio:g}:1 split for {current_user.username}: '
            f'{stats["grants"]} grants, {stats["vests"]} vests, '
            f'{stats["user_prices"]} prices, {stats["sales"]} sales, '
            f'{stats["exercises"]} exercises, '
            f'{stats["cash_grants_skipped"]} cash grants skipped'
            + (f', {stats["price_errors"]} price errors' if stats.get("price_errors") else ''),
            'success',
        )
    except Exception as e:
        db.session.rollback()
        flash(f'Split failed: {e}', 'error')

    return redirect(url_for('admin.dashboard'))
