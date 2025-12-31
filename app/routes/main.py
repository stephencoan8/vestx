"""
Main application routes - dashboard, home page.
"""

from flask import Blueprint, render_template, redirect, url_for, jsonify
from flask_login import login_required, current_user
from app.models.grant import Grant
from app.models.vest_event import VestEvent
from datetime import date

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    """Landing page."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return redirect(url_for('auth.login'))


@main_bp.route('/dashboard')
@login_required
def dashboard():
    """User dashboard showing grant summary."""
    # Get user's grants
    grants = Grant.query.filter_by(user_id=current_user.id).all()
    
    # Get current stock price from user's encrypted prices
    current_price = 0.0
    try:
        from app.models.user_price import UserPrice
        from app.utils.encryption import decrypt_for_user
        user_key = current_user.get_decrypted_user_key()
        price_entry = UserPrice.query.filter_by(user_id=current_user.id).order_by(
            UserPrice.valuation_date.desc()
        ).first()
        if price_entry:
            price_str = decrypt_for_user(user_key, price_entry.encrypted_price)
            current_price = float(price_str)
    except Exception:
        current_price = 0.0
    
    # Calculate totals - use grant.current_value which handles ISOs correctly
    total_grants = len(grants)
    total_shares = sum(g.share_quantity for g in grants)
    # For total value, sum up each grant's current_value (which handles ISO spread correctly)
    total_value = sum(g.current_value for g in grants)
    
    # Get upcoming vests (vest_date in the future)
    upcoming_vests = VestEvent.query.join(Grant).filter(
        Grant.user_id == current_user.id,
        VestEvent.vest_date >= date.today()
    ).order_by(VestEvent.vest_date).limit(5).all()
    
    # Get ALL vest events and filter by has_vested property (vest_date in the past)
    all_vest_events = VestEvent.query.join(Grant).filter(
        Grant.user_id == current_user.id
    ).order_by(VestEvent.vest_date).all()
    
    # Filter vested events using the has_vested property
    vested_events = [v for v in all_vest_events if v.has_vested]
    vested_shares_gross = sum(v.shares_vested for v in vested_events)
    vested_shares_net = sum(v.shares_received for v in vested_events)
    vested_value_gross = vested_shares_gross * current_price
    vested_value_net = vested_shares_net * current_price
    
    # Build comprehensive timeline with ALL state changes (stock price updates + vest events)
    from app.models.user_price import UserPrice
    from app.utils.encryption import decrypt_for_user
    
    # Get user's encrypted prices and decrypt them
    all_user_prices = UserPrice.query.filter_by(user_id=current_user.id).order_by(UserPrice.valuation_date).all()
    all_stock_prices = []
    try:
        user_key = current_user.get_decrypted_user_key()
        for price_entry in all_user_prices:
            try:
                price_str = decrypt_for_user(user_key, price_entry.encrypted_price)
                price_val = float(price_str)
                all_stock_prices.append({
                    'valuation_date': price_entry.valuation_date,
                    'price_per_share': price_val
                })
            except Exception:
                continue
    except Exception:
        all_stock_prices = []
    
    # Create a timeline of all significant dates (vest events + price changes)
    timeline_events = []
    
    # Add all vest events
    for vest in all_vest_events:
        timeline_events.append({
            'date': vest.vest_date,
            'type': 'vest',
            'vest': vest
        })
    
    # Add all stock price updates
    for price in all_stock_prices:
        timeline_events.append({
            'date': price['valuation_date'],
            'type': 'price_update',
            'price': price['price_per_share']
        })
    
    # Sort all events by date
    timeline_events.sort(key=lambda x: x['date'])
    
    # Pre-build a dict for O(1) price lookups
    price_dict = {p['valuation_date']: p['price_per_share'] for p in all_stock_prices}
    
    # Calculate cumulative values efficiently with O(n) complexity
    vesting_timeline = []
    cumulative_vested_value = 0
    cumulative_total_value = 0
    cumulative_vested_shares = 0
    cumulative_total_shares = 0
    current_price = 0
    
    for event in timeline_events:
        event_date = event['date']
        
        # Update price if this is a price update
        if event['type'] == 'price_update':
            # Recalculate all cumulative values with new price
            # This is necessary because ISOs use spread (price - strike)
            current_price = event['price']
            
            # Recalculate from scratch when price changes
            cumulative_vested_value = 0
            cumulative_total_value = 0
            
            for vest in all_vest_events:
                if vest.vest_date <= event_date:
                    grant = vest.grant
                    shares = vest.shares_vested
                    
                    if grant.share_type in ['iso_5y', 'iso_6y']:
                        value = shares * (current_price - grant.share_price_at_grant)
                    else:
                        value = shares * current_price
                    
                    cumulative_total_value += value
                    if vest.has_vested:
                        cumulative_vested_value += value
        
        # Process vest event
        elif event['type'] == 'vest':
            vest = event['vest']
            grant = vest.grant
            shares = vest.shares_vested
            
            # Use most recent price
            if not current_price:
                continue
            
            if grant.share_type in ['iso_5y', 'iso_6y']:
                value = shares * (current_price - grant.share_price_at_grant)
            else:
                value = shares * current_price
            
            cumulative_total_value += value
            cumulative_total_shares += shares
            
            if vest.has_vested:
                cumulative_vested_value += value
                cumulative_vested_shares += shares
        
        # Only add timeline point if we have data
        if current_price > 0 and cumulative_total_shares > 0:
            vesting_timeline.append({
                'date': event_date.strftime('%Y-%m-%d'),
                'vested_shares': cumulative_vested_shares,
                'total_shares': cumulative_total_shares,
                'vested_value': cumulative_vested_value,
                'total_value': cumulative_total_value,
                'is_vested': event_date <= date.today(),
                'price_at_date': current_price,
                'event_type': event['type']
            })
    
    return render_template('main/dashboard.html',
                         total_grants=total_grants,
                         total_shares=total_shares,
                         total_value=total_value,
                         vested_shares_gross=vested_shares_gross,
                         vested_shares_net=vested_shares_net,
                         vested_value_gross=vested_value_gross,
                         vested_value_net=vested_value_net,
                         upcoming_vests=upcoming_vests,
                         current_price=current_price,
                         vesting_timeline=vesting_timeline)


@main_bp.route('/stock-price-chart-data')
@login_required
def stock_price_chart_data():
    """Get stock price data for dashboard chart."""
    from app.models.user_price import UserPrice
    from app.utils.encryption import decrypt_for_user
    
    # Get user's encrypted prices and decrypt them
    price_entries = UserPrice.query.filter_by(user_id=current_user.id).order_by(UserPrice.valuation_date).all()
    
    dates = []
    prices = []
    
    try:
        user_key = current_user.get_decrypted_user_key()
        for price_entry in price_entries:
            try:
                price_str = decrypt_for_user(user_key, price_entry.encrypted_price)
                price_val = float(price_str)
                dates.append(price_entry.valuation_date.strftime('%Y-%m-%d'))
                prices.append(price_val)
            except Exception:
                continue
    except Exception:
        pass
    
    data = {
        'dates': dates,
        'prices': prices
    }
    
    return jsonify(data)
