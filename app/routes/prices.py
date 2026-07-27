from flask import Blueprint, request, jsonify, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from app import db
from app.models.user_price import UserPrice
from app.utils.encryption import encrypt_for_user, decrypt_for_user, EncryptionError
from app.utils.audit_log import AuditLogger
from app.utils.market_data import public_market_start, stock_ticker, sync_market_prices

prices_bp = Blueprint('prices', __name__, url_prefix='/user/prices')


@prices_bp.route('/', methods=['GET'])
@login_required
def list_prices():
    """Private pre-IPO entries + public market series from first trading day."""
    from app.models.market_price import MarketPrice
    from app.utils.price_utils import get_latest_user_price

    # Refresh public series (throttled)
    try:
        sync_market_prices(force=False)
    except Exception:
        pass

    cutover = public_market_start()
    ticker = stock_ticker()
    private = []
    try:
        user_key = current_user.get_decrypted_user_key()
        for p in UserPrice.query.filter_by(user_id=current_user.id).order_by(UserPrice.valuation_date.desc()).all():
            try:
                price_val = float(decrypt_for_user(user_key, p.encrypted_price))
            except Exception:
                price_val = None
            private.append({
                'id': p.id,
                'valuation_date': p.valuation_date,
                'decrypted_price': price_val,
                'source': 'private',
                'editable': p.valuation_date < cutover,
            })
    except EncryptionError:
        flash(
            'Cannot unlock private price data. Server encryption key (VESTX_MASTER_KEY) may be wrong.',
            'danger',
        )

    public_rows = (
        MarketPrice.query
        .filter_by(ticker=ticker)
        .filter(MarketPrice.valuation_date >= cutover)
        .order_by(MarketPrice.valuation_date.desc())
        .limit(90)
        .all()
    )
    public = [
        {
            'id': None,
            'valuation_date': r.valuation_date,
            'decrypted_price': r.price_per_share,
            'source': r.source or 'public',
            'editable': False,
        }
        for r in public_rows
    ]

    live = get_latest_user_price(current_user.id)
    AuditLogger.log_security_event(
        'USER_PRICE_LIST',
        {'user_id': current_user.id, 'private': len(private), 'public': len(public)},
    )
    return render_template(
        'prices/list.html',
        private_prices=private,
        public_prices=public,
        cutover=cutover,
        ticker=ticker,
        live_price=live,
    )


@prices_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add_price():
    """Add a private pre-IPO price entry only."""
    from datetime import datetime

    cutover = public_market_start()
    if request.method == 'GET':
        return render_template('prices/add.html', cutover=cutover, ticker=stock_ticker())

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

    if valuation_date >= cutover:
        msg = (
            f'From {cutover.isoformat()} onward, {stock_ticker()} prices come from the public market. '
            'Add private valuations only for pre-IPO dates.'
        )
        if request.is_json:
            return jsonify({'error': msg}), 400
        flash(msg, 'danger')
        return redirect(url_for('prices.list_prices'))

    try:
        user_key = current_user.get_decrypted_user_key()
    except EncryptionError as e:
        if request.is_json:
            return jsonify({'error': str(e)}), 500
        flash(
            'Cannot unlock encryption key. Check VESTX_MASTER_KEY. Prices were not modified.',
            'danger',
        )
        return redirect(url_for('prices.list_prices'))

    token = encrypt_for_user(user_key, str(price_float))
    up = UserPrice(user_id=current_user.id, valuation_date=valuation_date, encrypted_price=token)
    db.session.add(up)
    db.session.commit()
    AuditLogger.log_security_event(
        'USER_PRICE_ADDED',
        {'user_id': current_user.id, 'price_id': up.id, 'date': up.valuation_date.isoformat()},
    )
    if request.is_json:
        return jsonify({'id': up.id, 'date': up.valuation_date.isoformat(), 'price': price_float}), 201
    flash('Price added successfully!', 'success')
    return redirect(url_for('prices.list_prices'))


@prices_bp.route('/<int:price_id>/delete', methods=['POST'])
@login_required
def delete_price(price_id):
    p = UserPrice.query.filter_by(id=price_id, user_id=current_user.id).first_or_404()
    if p.valuation_date >= public_market_start():
        flash('Post-IPO prices are market-sourced and cannot be deleted here.', 'danger')
        return redirect(url_for('prices.list_prices'))
    db.session.delete(p)
    db.session.commit()
    AuditLogger.log_security_event('USER_PRICE_DELETED', {'user_id': current_user.id, 'price_id': price_id})
    flash('Price deleted successfully!', 'success')
    return redirect(url_for('prices.list_prices'))


@prices_bp.route('/<int:price_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_price(price_id):
    """Edit a private pre-IPO price entry."""
    from datetime import datetime

    p = UserPrice.query.filter_by(id=price_id, user_id=current_user.id).first_or_404()
    cutover = public_market_start()

    if p.valuation_date >= cutover:
        flash('Post-IPO prices are market-sourced and cannot be edited.', 'danger')
        return redirect(url_for('prices.list_prices'))

    if request.method == 'GET':
        try:
            user_key = current_user.get_decrypted_user_key()
            price_str = decrypt_for_user(user_key, p.encrypted_price)
            price_val = float(price_str)
        except EncryptionError:
            flash(
                'Cannot unlock price data. Check VESTX_MASTER_KEY. Existing prices were not modified.',
                'danger',
            )
            return redirect(url_for('prices.list_prices'))
        except Exception:
            price_val = None
        return render_template(
            'prices/edit.html', price=p, decrypted_price=price_val, cutover=cutover
        )

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

    if valuation_date >= cutover:
        msg = f'Private entries must be dated before {cutover.isoformat()} (first {stock_ticker()} trading day).'
        if request.is_json:
            return jsonify({'error': msg}), 400
        flash(msg, 'danger')
        return redirect(url_for('prices.edit_price', price_id=price_id))

    try:
        user_key = current_user.get_decrypted_user_key()
    except EncryptionError as e:
        if request.is_json:
            return jsonify({'error': str(e)}), 500
        flash(
            'Cannot unlock encryption key. Check VESTX_MASTER_KEY. Prices were not modified.',
            'danger',
        )
        return redirect(url_for('prices.list_prices'))

    token = encrypt_for_user(user_key, str(price_float))
    p.valuation_date = valuation_date
    p.encrypted_price = token
    db.session.commit()

    AuditLogger.log_security_event(
        'USER_PRICE_UPDATED',
        {'user_id': current_user.id, 'price_id': p.id, 'date': p.valuation_date.isoformat()},
    )

    if request.is_json:
        return jsonify({'id': p.id, 'date': p.valuation_date.isoformat(), 'price': price_float}), 200
    flash('Price updated successfully!', 'success')
    return redirect(url_for('prices.list_prices'))


@prices_bp.route('/sync-market', methods=['POST'])
@login_required
def sync_market():
    """Force refresh public market prices (throttled server-side except force)."""
    try:
        result = sync_market_prices(force=True)
        if result.get('error'):
            flash(f'Market sync failed: {result["error"]}', 'danger')
        else:
            flash(
                f'Updated {stock_ticker()} public prices '
                f'({result.get("bars", 0)} bars from {result.get("source", "market")}).',
                'success',
            )
    except Exception as e:
        flash(f'Market sync failed: {e}', 'danger')
    return redirect(url_for('prices.list_prices'))
