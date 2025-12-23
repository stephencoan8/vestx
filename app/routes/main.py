"""
Main application routes - dashboard, home page.
"""

from flask import Blueprint, render_template, redirect, url_for, jsonify
from flask_login import login_required, current_user
from app.models.grant import Grant
from app.models.vest_event import VestEvent
from app.models.stock_price import StockPrice
from app.utils.init_db import get_latest_stock_price
from datetime import date
from sqlalchemy import func

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
    
    # Calculate totals - use grant.current_value which handles ISOs correctly
    total_grants = len(grants)
    total_shares = sum(g.share_quantity for g in grants)
    current_price = get_latest_stock_price()
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
    
    # Prepare vesting timeline data for chart
    vesting_timeline = []
    cumulative_vested_shares = 0
    cumulative_total_shares = 0
    cumulative_vested_value = 0
    cumulative_total_value = 0
    
    for vest in all_vest_events:
        shares = vest.shares_received  # Use net shares received
        # Use the value at vest date (historical price), not current price
        value = vest.net_value  # This uses price_at_vest (historical)
        
        cumulative_total_shares += shares
        cumulative_total_value += value
        
        if vest.has_vested:
            cumulative_vested_shares += shares
            cumulative_vested_value += value
        
        vesting_timeline.append({
            'date': vest.vest_date.strftime('%Y-%m-%d'),
            'vested_shares': cumulative_vested_shares,
            'total_shares': cumulative_total_shares,
            'vested_value': cumulative_vested_value,
            'total_value': cumulative_total_value,
            'is_vested': vest.has_vested,
            'shares': shares,
            'value': value
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
    prices = StockPrice.query.order_by(StockPrice.valuation_date).all()
    
    data = {
        'dates': [p.valuation_date.strftime('%Y-%m-%d') for p in prices],
        'prices': [p.price_per_share for p in prices]
    }
    
    return jsonify(data)
