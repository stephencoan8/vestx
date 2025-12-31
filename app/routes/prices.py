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


@prices_bp.route('/add', methods=['POST'])
@login_required
def add_price():
    """Add a new user price entry. Accepts JSON {date: ISO, price: number}."""
    data = request.get_json() or {}
    date_str = data.get('date')
    price_val = data.get('price')
    if not date_str or price_val is None:
        return jsonify({'error': 'date and price required'}), 400
    try:
        from datetime import datetime
        valuation_date = datetime.fromisoformat(date_str).date()
        price_float = float(price_val)
    except Exception:
        return jsonify({'error': 'invalid date or price'}), 400

    user_key = current_user.get_decrypted_user_key()
    token = encrypt_for_user(user_key, str(price_float))

    up = UserPrice(user_id=current_user.id, valuation_date=valuation_date, encrypted_price=token)
    db.session.add(up)
    db.session.commit()
    AuditLogger.log_security_event('USER_PRICE_ADDED', {'user_id': current_user.id, 'price_id': up.id, 'date': up.valuation_date.isoformat()})
    return jsonify({'id': up.id, 'date': up.valuation_date.isoformat(), 'price': price_float}), 201


@prices_bp.route('/<int:price_id>/delete', methods=['POST'])
@login_required
def delete_price(price_id):
    p = UserPrice.query.filter_by(id=price_id, user_id=current_user.id).first_or_404()
    db.session.delete(p)
    db.session.commit()
    AuditLogger.log_security_event('USER_PRICE_DELETED', {'user_id': current_user.id, 'price_id': price_id})
    return jsonify({'success': True})
