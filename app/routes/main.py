"""
Main application routes - dashboard, home page.
"""

from flask import Blueprint, render_template, redirect, url_for, jsonify, request
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


@main_bp.route('/holdings')
@login_required
def holdings_redirect():
    """Canonical holdings live at /grants/."""
    tab = request.args.get('tab')
    if tab == 'upcoming':
        tab = 'schedule'
    if tab:
        return redirect(url_for('grants.list_grants', tab=tab))
    return redirect(url_for('grants.list_grants'))


@main_bp.route('/dashboard')
@login_required
def dashboard():
    """User dashboard showing grant summary."""
    from app.utils.price_utils import get_latest_user_price, warm_user_price_history

    grants = Grant.query.filter_by(user_id=current_user.id).all()

    warm_user_price_history(current_user.id)
    current_price = get_latest_user_price(current_user.id) or 0.0

    total_grants = len(grants)

    # Shareworks-aligned: Available (vested held) + Unavailable (unvested)
    from app.utils.portfolio_summary import summarize_held_portfolio
    from app.utils.valuation import build_vesting_timeline, scheduled_vest_value
    today = date.today()
    held = summarize_held_portfolio(
        current_user.id, live_price=current_price, as_of=today,
    )
    total_shares = held['held_shares']
    total_value = held['total_value']  # available + unavailable
    available_value = held['available_value']
    unavailable_value = held['unavailable_value']
    unavailable_shares = held['unavailable_shares']
    available_stock_value = held['available_stock_value']
    grant_book_value = held['grant_book_value']
    grant_book_shares = held['grant_book_shares']
    unexercised_iso_shares = held['iso_unexercised']
    unexercised_iso_value = held['iso_unexercised_value']
    shares_sold_market = held['shares_sold_market']

    upcoming_vests = (
        VestEvent.query
        .options(joinedload(VestEvent.grant))
        .join(Grant)
        .filter(Grant.user_id == current_user.id, VestEvent.vest_date >= date.today())
        .order_by(VestEvent.vest_date)
        .limit(48)  # ~4 years monthly; list is scrollable on the dashboard
        .all()
    )
    for vest in upcoming_vests:
        vest.mtm_value = scheduled_vest_value(vest, current_price)

    all_vest_events = (
        VestEvent.query
        .options(joinedload(VestEvent.grant))
        .join(Grant)
        .filter(Grant.user_id == current_user.id)
        .order_by(VestEvent.vest_date)
        .all()
    )

    vested_events = [v for v in all_vest_events if v.vest_date <= today]
    vested_shares_gross = sum(v.shares_vested for v in vested_events)
    # Net received before market sales (withholding only) — kept for reference
    vested_shares_net_received = sum(v.shares_received for v in vested_events)
    # Actually still held (sellable RSU + exercised ISO held)
    vested_shares_net = held['held_shares']
    vested_value_gross = vested_shares_gross * current_price
    vested_value_net = held['available_stock_value']
    needs_info_count = sum(1 for v in vested_events if v.needs_tax_info)

    # Past line marks shares still held (withholding and sales already out of
    # the lots) plus vested unexercised options at the floored spread. The
    # point on today uses the same live price as the hero, so it matches
    # Vested. Future points add scheduled vests at that price.
    from app.utils.price_utils import get_merged_price_series
    vesting_timeline = build_vesting_timeline(
        held['lots'],
        [v for v in all_vest_events if v.vest_date > today],
        get_merged_price_series(current_user.id),
        live_price=current_price,
        as_of=today,
    )

    return render_template(
        'main/dashboard.html',
        total_grants=total_grants,
        total_shares=total_shares,
        total_value=total_value,
        available_value=available_value,
        available_stock_value=available_stock_value,
        unavailable_value=unavailable_value,
        unavailable_shares=unavailable_shares,
        grant_book_value=grant_book_value,
        grant_book_shares=grant_book_shares,
        shares_sold_market=shares_sold_market,
        vested_shares_gross=vested_shares_gross,
        vested_shares_net=vested_shares_net,
        vested_shares_net_received=vested_shares_net_received,
        vested_value_gross=vested_value_gross,
        vested_value_net=vested_value_net,
        unexercised_iso_shares=unexercised_iso_shares,
        unexercised_iso_value=unexercised_iso_value,
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
