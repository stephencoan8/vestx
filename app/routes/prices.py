from flask import Blueprint, request, jsonify, current_app, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from app import db
from app.models.user_price import UserPrice
from app.utils.encryption import encrypt_for_user, decrypt_for_user
from app.utils.audit_log import AuditLogger

prices_bp = Blueprint('prices', __name__, url_prefix='/user/prices')


@prices_bp.route('/', methods=['GET'])
@login_required
def list_prices():
    """List decrypted prices for current user as HTML."""
    user_key = current_user.get_decrypted_user_key()
    prices = []
    for p in UserPrice.query.filter_by(user_id=current_user.id).order_by(UserPrice.valuation_date.desc()).all():
        try:
            price_str = decrypt_for_user(user_key, p.encrypted_price)
            price_val = float(price_str)
        except Exception:
            price_val = None
        prices.append({
            'id': p.id,
            'valuation_date': p.valuation_date,
            'decrypted_price': price_val
        })
    AuditLogger.log_security_event('USER_PRICE_LIST', {'user_id': current_user.id, 'count': len(prices)})
    return render_template('prices/list.html', prices=prices)


@prices_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add_price():
    """Add a new user price entry. Supports HTML form and JSON POST."""
    from datetime import datetime
    if request.method == 'GET':
        return render_template('prices/add.html')
    # Handle form or JSON POST
    if request.is_json:
        data = request.get_json() or {}
        date_str = data.get('date')
        price_val = data.get('price')
    else:
        date_str = request.form.get('date')
        price_val = request.form.get('price')
    if not date_str or price_val is None:
        if request.is_json:
            return jsonify({'error': 'date and price required'}), 400
        flash('Date and price are required.', 'danger')
        return redirect(url_for('prices.add_price'))
    try:
        valuation_date = datetime.fromisoformat(date_str).date()
        price_float = float(price_val)
    except Exception:
        if request.is_json:
            return jsonify({'error': 'invalid date or price'}), 400
        flash('Invalid date or price.', 'danger')
        return redirect(url_for('prices.add_price'))
    user_key = current_user.get_decrypted_user_key()
    token = encrypt_for_user(user_key, str(price_float))
    up = UserPrice(user_id=current_user.id, valuation_date=valuation_date, encrypted_price=token)
    db.session.add(up)
    db.session.commit()
    AuditLogger.log_security_event('USER_PRICE_ADDED', {'user_id': current_user.id, 'price_id': up.id, 'date': up.valuation_date.isoformat()})
    if request.is_json:
        return jsonify({'id': up.id, 'date': up.valuation_date.isoformat(), 'price': price_float}), 201
    flash('Price added successfully!', 'success')
    return redirect(url_for('prices.list_prices'))


@prices_bp.route('/<int:price_id>/delete', methods=['POST'])
@login_required
def delete_price(price_id):
    p = UserPrice.query.filter_by(id=price_id, user_id=current_user.id).first_or_404()
    db.session.delete(p)
    db.session.commit()
    AuditLogger.log_security_event('USER_PRICE_DELETED', {'user_id': current_user.id, 'price_id': price_id})
    flash('Price deleted successfully!', 'success')
    return redirect(url_for('prices.list_prices'))


@prices_bp.route('/<int:price_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_price(price_id):
    """Edit an existing price entry."""
    from datetime import datetime
    p = UserPrice.query.filter_by(id=price_id, user_id=current_user.id).first_or_404()
    
    if request.method == 'GET':
        user_key = current_user.get_decrypted_user_key()
        try:
            price_str = decrypt_for_user(user_key, p.encrypted_price)
            price_val = float(price_str)
        except Exception:
            price_val = None
        return render_template('prices/edit.html', price=p, decrypted_price=price_val)
    
    # Handle form or JSON POST
    if request.is_json:
        data = request.get_json() or {}
        date_str = data.get('date')
        price_val = data.get('price')
    else:
        date_str = request.form.get('date')
        price_val = request.form.get('price')
    
    if not date_str or price_val is None:
        if request.is_json:
            return jsonify({'error': 'date and price required'}), 400
        flash('Date and price are required.', 'danger')
        return redirect(url_for('prices.edit_price', price_id=price_id))
    
    try:
        valuation_date = datetime.fromisoformat(date_str).date()
        price_float = float(price_val)
    except Exception:
        if request.is_json:
            return jsonify({'error': 'invalid date or price'}), 400
        flash('Invalid date or price.', 'danger')
        return redirect(url_for('prices.edit_price', price_id=price_id))
    
    user_key = current_user.get_decrypted_user_key()
    token = encrypt_for_user(user_key, str(price_float))
    
    p.valuation_date = valuation_date
    p.encrypted_price = token
    db.session.commit()
    
    AuditLogger.log_security_event('USER_PRICE_UPDATED', {'user_id': current_user.id, 'price_id': p.id, 'date': p.valuation_date.isoformat()})
    
    if request.is_json:
        return jsonify({'id': p.id, 'date': p.valuation_date.isoformat(), 'price': price_float}), 200
    flash('Price updated successfully!', 'success')
    return redirect(url_for('prices.list_prices'))
