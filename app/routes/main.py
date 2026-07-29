"""
Main application routes - dashboard, home page.
"""

from flask import Blueprint, render_template, redirect, url_for, jsonify
from flask_login import login_required, current_user
from app.models.grant import Grant
from app.models.vest_event import VestEvent
from datetime import date
from sqlalchemy.orm import joinedload

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
    from app.utils.price_utils import get_latest_user_price, warm_user_price_history

    grants = Grant.query.filter_by(user_id=current_user.id).all()

    warm_user_price_history(current_user.id)
    current_price = get_latest_user_price(current_user.id) or 0.0

    total_grants = len(grants)
    total_shares = sum(g.share_quantity for g in grants)

    # Compute values from current_price directly — avoid N property lookups
    total_value = 0.0
    for g in grants:
        if g.share_type == 'cash':
            total_value += g.share_quantity
        elif g.share_type in ('iso_5y', 'iso_6y'):
            total_value += g.share_quantity * (current_price - g.share_price_at_grant)
        else:
            total_value += g.share_quantity * current_price

    upcoming_vests = (
        VestEvent.query
        .options(joinedload(VestEvent.grant))
        .join(Grant)
        .filter(Grant.user_id == current_user.id, VestEvent.vest_date >= date.today())
        .order_by(VestEvent.vest_date)
        .limit(48)  # ~4 years monthly; list is scrollable on the dashboard
        .all()
    )

    all_vest_events = (
        VestEvent.query
        .options(joinedload(VestEvent.grant))
        .join(Grant)
        .filter(Grant.user_id == current_user.id)
        .order_by(VestEvent.vest_date)
        .all()
    )

    today = date.today()
    vested_events = [v for v in all_vest_events if v.vest_date <= today]
    vested_shares_gross = sum(v.shares_vested for v in vested_events)
    vested_shares_net = sum(v.shares_received for v in vested_events)
    vested_value_gross = vested_shares_gross * current_price
    vested_value_net = vested_shares_net * current_price
    needs_info_count = sum(1 for v in vested_events if v.needs_tax_info)

    # Merged private pre-IPO + public SPCX history for timeline price points
    from app.utils.price_utils import get_merged_price_series
    all_stock_prices = [
        {'valuation_date': d, 'price_per_share': p}
        for d, p in get_merged_price_series(current_user.id)
    ]

    timeline_events = []
    for vest in all_vest_events:
        timeline_events.append({'date': vest.vest_date, 'type': 'vest', 'vest': vest})
    for price in all_stock_prices:
        timeline_events.append({
            'date': price['valuation_date'],
            'type': 'price_update',
            'price': price['price_per_share'],
        })
    timeline_events.sort(key=lambda x: x['date'])

    # Precompute share types / strike to avoid repeated attribute access in nested loops
    vest_meta = []
    for vest in all_vest_events:
        grant = vest.grant
        is_iso = grant.share_type in ('iso_5y', 'iso_6y')
        vest_meta.append({
            'vest': vest,
            'shares': vest.shares_vested,
            'is_iso': is_iso,
            'strike': grant.share_price_at_grant if is_iso else 0.0,
            'vest_date': vest.vest_date,
        })

    vesting_timeline = []
    cumulative_vested_value = 0.0
    cumulative_total_value = 0.0
    cumulative_vested_shares = 0.0
    cumulative_total_shares = 0.0
    running_price = 0.0

    for event in timeline_events:
        event_date = event['date']

        if event['type'] == 'price_update':
            running_price = event['price']
            cumulative_vested_value = 0.0
            cumulative_total_value = 0.0
            for meta in vest_meta:
                if meta['vest_date'] <= event_date:
                    if meta['is_iso']:
                        value = meta['shares'] * (running_price - meta['strike'])
                    else:
                        value = meta['shares'] * running_price
                    cumulative_total_value += value
                    if meta['vest_date'] <= today:
                        cumulative_vested_value += value

        elif event['type'] == 'vest':
            vest = event['vest']
            grant = vest.grant
            shares = vest.shares_vested
            if not running_price:
                continue
            if grant.share_type in ('iso_5y', 'iso_6y'):
                value = shares * (running_price - grant.share_price_at_grant)
            else:
                value = shares * running_price

            cumulative_total_value += value
            cumulative_total_shares += shares
            if vest.vest_date <= today:
                cumulative_vested_value += value
                cumulative_vested_shares += shares

        if running_price > 0 and cumulative_total_shares > 0:
            vesting_timeline.append({
                'date': event_date.strftime('%Y-%m-%d'),
                'vested_shares': cumulative_vested_shares,
                'total_shares': cumulative_total_shares,
                'vested_value': cumulative_vested_value,
                'total_value': cumulative_total_value,
                'is_vested': event_date <= today,
                'price_at_date': running_price,
                'event_type': event['type'],
            })

    return render_template(
        'main/dashboard.html',
        total_grants=total_grants,
        total_shares=total_shares,
        total_value=total_value,
        vested_shares_gross=vested_shares_gross,
        vested_shares_net=vested_shares_net,
        vested_value_gross=vested_value_gross,
        vested_value_net=vested_value_net,
        upcoming_vests=upcoming_vests,
        current_price=current_price,
        vesting_timeline=vesting_timeline,
        needs_info_count=needs_info_count,
    )


@main_bp.route('/stock-price-chart-data')
@login_required
def stock_price_chart_data():
    """Merged price series: private pre-IPO + public SPCX post-IPO."""
    from app.utils.price_utils import get_merged_price_series, warm_user_price_history
    from app.utils.market_data import public_market_start

    warm_user_price_history(current_user.id)
    series = get_merged_price_series(current_user.id)
    cutover = public_market_start()
    return jsonify({
        'dates': [d.strftime('%Y-%m-%d') for d, _ in series],
        'prices': [p for _, p in series],
        'cutover': cutover.isoformat(),
        'sources': [
            'private' if d < cutover else 'public'
            for d, _ in series
        ],
    })
