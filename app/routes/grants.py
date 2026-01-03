"""
Grant management routes - view, add, edit, delete grants.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models.grant import Grant, GrantType, ShareType
from app.models.vest_event import VestEvent
from app.models.stock_price import StockPrice
from app.models.sale_plan import SalePlan
from app.utils.vest_calculator import calculate_vest_schedule, get_grant_configuration
from app.models.tax_rate import UserTaxProfile
from app.utils.price_utils import get_latest_user_price
from datetime import datetime, date, timedelta
import logging

logging.basicConfig(level=logging.DEBUG)

grants_bp = Blueprint('grants', __name__, url_prefix='/grants')


@grants_bp.route('/')
@login_required
def list_grants():
    """List all user grants."""
    grants = Grant.query.filter_by(user_id=current_user.id).order_by(Grant.grant_date.desc()).all()
    return render_template('grants/list.html', grants=grants)


@grants_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add_grant():
    """Add a new grant."""
    if request.method == 'POST':
        try:
            # Parse form data
            grant_date = datetime.strptime(request.form.get('grant_date'), '%Y-%m-%d').date()
            grant_type = request.form.get('grant_type')
            share_type = request.form.get('share_type')
            share_quantity = float(request.form.get('share_quantity'))
            bonus_type = request.form.get('bonus_type')
            vest_years = request.form.get('vest_years')
            notes = request.form.get('notes', '')
            
            # ESPP discount (typically 15% = 0.15)
            espp_discount = request.form.get('espp_discount')
            if espp_discount:
                espp_discount = float(espp_discount)
            else:
                # Default 15% for ESPP, 0% for others
                espp_discount = 0.15 if grant_type == 'espp' else 0.0
            
            # Get stock price at grant date from user's encrypted prices
            share_price = 0.0
            # Debug logging for stock price retrieval
            import logging
            logger = logging.getLogger(__name__)

            try:
                from app.models.user_price import UserPrice
                from app.utils.encryption import decrypt_for_user
                user_key = current_user.get_decrypted_user_key()

                # Ensure user is authenticated before retrieving prices
                if not current_user.is_authenticated:
                    logger.warning("User not authenticated when retrieving share_price_at_grant")
                    flash("You must be logged in to add a grant.", "error")
                    return redirect(url_for('grants.list_grants'))

                # Use grant's user_id for price retrieval (if applicable)
                user_id = current_user.id

                # Retrieve stock price at grant date
                price_entry = UserPrice.query.filter_by(user_id=user_id).filter(
                    UserPrice.valuation_date <= grant_date
                ).order_by(UserPrice.valuation_date.desc()).first()
                
                if price_entry:
                    # Use centralized helper to retrieve latest decrypted price
                    price = get_latest_user_price(current_user.id, as_of_date=grant_date)
                    if price is not None:
                        share_price = price
                        logger.debug(f"Found price {share_price} for user {current_user.id} on or before {grant_date}")
                    else:
                        logger.warning(f"No UserPrice entry found or could not decrypt for user {current_user.id} on or before {grant_date}")
                else:
                    logger.warning(f"No UserPrice entry found for user {current_user.id} on or before {grant_date}")
            except Exception as price_error:
                logger.error(f"Error retrieving or decrypting price: {price_error}", exc_info=True)
                share_price = 0.0
            
            # Get vesting configuration
            if vest_years:
                vest_years = int(vest_years)
                cliff_years = 1.0  # Default
            else:
                vest_years, cliff_years = get_grant_configuration(grant_type, share_type, bonus_type)
            
            # Create grant
            grant = Grant(
                user_id=current_user.id,
                grant_date=grant_date,
                grant_type=grant_type,
                share_type=share_type,
                share_quantity=share_quantity,
                share_price_at_grant=share_price,
                vest_years=vest_years,
                cliff_years=cliff_years,
                bonus_type=bonus_type,
                espp_discount=espp_discount,
                notes=notes
            )
            
            db.session.add(grant)
            db.session.flush()  # Get grant ID
            
            # Calculate and create vest events
            vest_schedule = calculate_vest_schedule(grant)
            for vest in vest_schedule:
                vest_event = VestEvent(
                    grant_id=grant.id,
                    vest_date=vest['vest_date'],
                    shares_vested=vest['shares'],
                    tax_year=vest['vest_date'].year
                )
                db.session.add(vest_event)
            
            db.session.commit()
            flash('Grant added successfully!', 'success')
            return redirect(url_for('grants.list_grants'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding grant: {str(e)}', 'error')
    
    return render_template('grants/add.html',
                         grant_types=GrantType,
                         share_types=ShareType)


@grants_bp.route('/<int:grant_id>')
@login_required
def view_grant(grant_id):
    """View grant details and vest schedule."""
    grant = Grant.query.get_or_404(grant_id)
    
    # Security check
    if grant.user_id != current_user.id:
        flash('Access denied', 'error')
        return redirect(url_for('grants.list_grants'))
    
    vest_events = VestEvent.query.filter_by(grant_id=grant.id).order_by(VestEvent.vest_date).all()
    
    # Debug: provide the decrypted price pulled via helper for the view
    debug_decrypted_price = get_latest_user_price(grant.user_id, as_of_date=grant.grant_date)
    
    return render_template('grants/view.html', grant=grant, vest_events=vest_events, debug_decrypted_price=debug_decrypted_price)


@grants_bp.route('/<int:grant_id>/delete', methods=['POST'])
@login_required
def delete_grant(grant_id):
    """Delete a grant."""
    grant = Grant.query.get_or_404(grant_id)
    
    # Security check
    if grant.user_id != current_user.id:
        flash('Access denied', 'error')
        return redirect(url_for('grants.list_grants'))
    
    db.session.delete(grant)
    db.session.commit()
    flash('Grant deleted successfully', 'success')
    return redirect(url_for('grants.list_grants'))


@grants_bp.route('/<int:grant_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_grant(grant_id):
    """Edit an existing grant."""
    grant = Grant.query.get_or_404(grant_id)
    
    # Security check
    if grant.user_id != current_user.id:
        flash('Access denied', 'error')
        return redirect(url_for('grants.list_grants'))
    
    if request.method == 'POST':
        try:
            # Parse form data
            grant_date = datetime.strptime(request.form.get('grant_date'), '%Y-%m-%d').date()
            grant_type = request.form.get('grant_type')
            share_type = request.form.get('share_type') or grant.share_type  # Keep existing if disabled
            share_quantity = float(request.form.get('share_quantity'))
            bonus_type = request.form.get('bonus_type') or None
            vest_years = request.form.get('vest_years') or None
            notes = request.form.get('notes', '')
            
            # ESPP discount (typically 15% = 0.15)
            espp_discount = request.form.get('espp_discount')
            if espp_discount:
                espp_discount = float(espp_discount)
            else:
                # Default 15% for ESPP, 0% for others
                espp_discount = 0.15 if grant_type == 'espp' else 0.0
            
            # Get stock price at grant date using centralized per-user price helper
            try:
                share_price = get_latest_user_price(current_user.id, as_of_date=grant_date) or 0.0
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Failed to retrieve user price for edit_grant; defaulting to 0.0")
                share_price = 0.0
            
            # Get vesting configuration
            if vest_years:
                vest_years = int(vest_years)
                cliff_years = 1.0  # Default
            else:
                vest_years, cliff_years = get_grant_configuration(grant_type, share_type, bonus_type)
            
            # Update grant
            grant.grant_date = grant_date
            grant.grant_type = grant_type
            grant.share_type = share_type
            grant.share_quantity = share_quantity
            grant.share_price_at_grant = share_price
            grant.vest_years = vest_years
            grant.cliff_years = cliff_years
            grant.bonus_type = bonus_type
            grant.espp_discount = espp_discount
            grant.notes = notes
            
            # Delete old vest events and recalculate
            VestEvent.query.filter_by(grant_id=grant.id).delete()
            
            # Recalculate and create new vest events
            vest_schedule = calculate_vest_schedule(grant)
            
            for vest in vest_schedule:
                vest_event = VestEvent(
                    grant_id=grant.id,
                    vest_date=vest['vest_date'],
                    shares_vested=vest['shares'],
                    tax_year=vest['vest_date'].year
                )
                db.session.add(vest_event)
            
            db.session.commit()
            
            flash('Grant updated successfully!', 'success')
            return redirect(url_for('grants.view_grant', grant_id=grant.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating grant: {str(e)}', 'error')
    
    return render_template('grants/edit.html',
                         grant=grant,
                         grant_types=GrantType,
                         share_types=ShareType)


@grants_bp.route('/vest-event/<int:event_id>/update', methods=['POST'])
@login_required
def update_vest_event(event_id):
    """Update vest event with tax information."""
    vest_event = VestEvent.query.get_or_404(event_id)
    
    # Security check
    if vest_event.grant.user_id != current_user.id:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        # Log incoming form for debugging
        import logging
        logger = logging.getLogger(__name__)
        logger.debug(f"update_vest_event called for event_id={event_id} form={dict(request.form)}")

        # New simplified tax fields (defensive parsing)
        def _parse_numeric(val):
             """Parse a numeric form input tolerant of common formats like "$1,234.56".

             Returns a float or raises ValueError on invalid input.
             """
             if val is None:
                 return 0.0
             if isinstance(val, (int, float)):
                 return float(val)
             s = str(val).strip()
             if s == '':
                 return 0.0
             # Remove common thousands separators and currency symbols
             s_clean = s.replace(',', '').replace('$', '').replace('(', '-').replace(')', '')
             # Allow locale variants by extracting the first numeric-looking substring
             import re
             m = re.search(r"[-+]?[0-9]*\.?[0-9]+", s_clean)
             if m:
                 try:
                     return float(m.group(0))
                 except ValueError:
                     pass

             # As a last resort, log and coerce to 0.0 rather than raising
             import logging
             logging.getLogger(__name__).warning("_parse_numeric: could not parse numeric value '%s' - coercing to 0.0", s)
             return 0.0

        # For ESPP and nqESPP, taxes are already handled prior to receipt
        # So we automatically set cash_paid to 0 and skip validation
        grant = vest_event.grant
        is_espp_type = grant.grant_type in ['espp', 'nqespp']
        
        if is_espp_type:
            cash_paid = 0.0
            cash_covered_all = True
            shares_sold = 0.0
        else:
            try:
                cash_paid = _parse_numeric(request.form.get('cash_paid', 0) or 0)
            except ValueError:
                return jsonify({'error': 'Invalid cash_paid value'}), 400
            cash_covered_all = str(request.form.get('cash_covered_all', 'true')).lower() == 'true'
            try:
                shares_sold = _parse_numeric(request.form.get('shares_sold', 0) or 0)
            except ValueError:
                return jsonify({'error': 'Invalid shares_sold value'}), 400
            
            # Validate non-negative
            if cash_paid < 0 or shares_sold < 0:
                return jsonify({'error': 'Values must be non-negative'}), 400

        # Cap shares_sold to available shares_vested
        if shares_sold > vest_event.shares_vested:
            logger.warning(f"shares_sold ({shares_sold}) > shares_vested ({vest_event.shares_vested}) for event {event_id}; capping")
            shares_sold = vest_event.shares_vested
        
        # Update vest event with new fields
        vest_event.cash_paid = cash_paid
        vest_event.cash_covered_all = cash_covered_all
        vest_event.shares_sold = 0.0 if cash_covered_all else shares_sold
        
        # Commit to database
        db.session.add(vest_event)
        db.session.commit()
        
        # Compute derived values to return
        tax_withheld = vest_event.tax_withheld
        shares_received = vest_event.shares_received
        net_value = vest_event.net_value
        
        logger.debug(f"Updated vest_event {event_id}: cash_paid={cash_paid}, cash_covered_all={cash_covered_all}, shares_sold={shares_sold}, tax_withheld={tax_withheld}, shares_received={shares_received}, net_value={net_value}")
        
        # Return calculated values
        return jsonify({
            'success': True, 
            'message': 'Vest event updated',
            'cash_paid': vest_event.cash_paid,
            'cash_covered_all': vest_event.cash_covered_all,
            'shares_sold': vest_event.shares_sold,
            'shares_received': shares_received,
            'tax_withheld': tax_withheld,
            'net_value': net_value
        })
    
    except Exception as e:
        db.session.rollback()
        import traceback
        logger = logging.getLogger(__name__)
        logger.error(f"ERROR: Failed to update vest event {event_id}: {e}", exc_info=True)
        tb = traceback.format_exc()
        # Return error detail for debugging (remove in production)
        return jsonify({'error': str(e), 'trace': tb}), 500


@grants_bp.route('/schedule')
@login_required
def vest_schedule():
    """View complete vesting schedule."""
    from app.utils.price_utils import get_latest_user_price
    from datetime import date
    from sqlalchemy.orm import joinedload
    
    # Eagerly load grant relationship to avoid N+1 queries
    vest_events = VestEvent.query.options(
        joinedload(VestEvent.grant)
    ).join(Grant).filter(
        Grant.user_id == current_user.id
    ).order_by(VestEvent.vest_date).all()
    
    # Get latest stock price for estimating future vests
    latest_stock_price = get_latest_user_price(current_user.id) or 0.0
    today = date.today()
    
    # Pre-fetch tax profile once to avoid N+1 queries
    tax_profile = UserTaxProfile.query.filter_by(user_id=current_user.id).first()
    
    # Pre-calculate tax rates for current year (for future vests)
    current_year_rates = None
    if tax_profile:
        current_year_rates = tax_profile.get_tax_rates(tax_year=today.year)
    
    # Enrich vest events with tax estimates
    enriched_events = []
    for ve in vest_events:
        # For future vests, calculate estimated tax (pass cached tax_profile and rates)
        if ve.vest_date > today:
            if tax_profile and current_year_rates:
                # Pass the cached tax_profile and rates to avoid repeated DB queries
                tax_info = ve.estimate_tax_withholding(
                    latest_stock_price, 
                    federal_rate=current_year_rates['federal'],
                    state_rate=current_year_rates['state'],
                    _tax_profile=tax_profile
                )
            else:
                tax_info = ve.estimate_tax_withholding(latest_stock_price)
            ve.estimated_tax = tax_info['tax_amount']
        else:
            ve.estimated_tax = None  # Use actual tax_withheld for vested events
        enriched_events.append(ve)
    
    return render_template('grants/schedule.html', vest_events=enriched_events)



@grants_bp.route('/rules')
@login_required
def rules():
    """View vesting rules and configurations."""
    return render_template('grants/rules.html')


@grants_bp.route('/finance-deep-dive')
@login_required
def finance_deep_dive():
    """Comprehensive tax and capital gains analysis."""
    import logging
    from sqlalchemy.orm import joinedload
    logger = logging.getLogger(__name__)

    # Get all grants with eager loading of vest events
    grants = Grant.query.options(
        joinedload(Grant.vest_events)
    ).filter_by(user_id=current_user.id).all()
    
    # Get all vest events with eager loading of grants
    all_vest_events = VestEvent.query.options(
        joinedload(VestEvent.grant)
    ).join(Grant).filter(
        Grant.user_id == current_user.id
    ).order_by(VestEvent.vest_date).all()
    
    # Get user's tax profile and calculate rates ONCE (avoid N+1 queries)
    tax_profile = UserTaxProfile.query.filter_by(user_id=current_user.id).first()
    if tax_profile:
        # Calculate tax rates once and cache them
        tax_rates = tax_profile.get_tax_rates()
        use_manual_rates = tax_profile.use_manual_rates
    else:
        # Default rates if no profile exists
        tax_rates = {'federal': 0.24, 'state': 0.093, 'ltcg': 0.15}
        use_manual_rates = True

    federal_rate_default = tax_rates.get('federal', 0.24)
    state_rate_default = tax_rates.get('state', 0.093)
    ltcg_rate_default = tax_rates.get('ltcg', 0.15)
    
    # Pre-fetch annual incomes once to avoid N+1 queries (needed for tax rate calculation)
    from app.models.annual_income import AnnualIncome
    annual_incomes_list = AnnualIncome.query.filter_by(user_id=current_user.id).all()
    annual_incomes_dict = {ai.year: ai.annual_income for ai in annual_incomes_list}
    
    # Pre-calculate rates for all possible years to avoid repeated queries
    # Use year-specific income (gross income from tax returns for accurate bracket calculation)
    years_with_vests = set(ve.vest_date.year for ve in all_vest_events)
    cached_rates_by_year = {}
    if tax_profile:
        for year in years_with_vests:
            # Use historical income for that year if available, otherwise current income
            year_income = annual_incomes_dict.get(year, tax_profile.annual_income)
            cached_rates_by_year[year] = tax_profile.get_tax_rates(tax_year=year, income_override=year_income)

    # Get latest stock price from user's encrypted prices
    from app.utils.price_utils import get_latest_user_price
    latest_stock_price = get_latest_user_price(current_user.id) or 0.0
    logger.debug(f"Using latest_stock_price={latest_stock_price} for user {current_user.id}")

    today = date.today()

    # Initialize totals
    total_shares_held_vested = 0.0
    total_shares_held_all = 0.0
    total_cost_basis_vested = 0.0
    total_cost_basis_all = 0.0
    total_current_value_vested = 0.0
    total_current_value_all = 0.0
    total_unrealized_gain_vested = 0.0
    total_unrealized_gain_all = 0.0
    total_estimated_tax = 0.0

    # Prepare data for analysis
    analysis_data = []

    for grant in grants:
        vest_events = [ve for ve in all_vest_events if ve.grant_id == grant.id]
        
        # Initialize grant-level totals
        grant_shares_held_vested = 0.0
        grant_shares_held_all = 0.0
        grant_cost_basis_vested = 0.0
        grant_cost_basis_all = 0.0
        grant_current_value_vested = 0.0
        grant_current_value_all = 0.0
        grant_unrealized_gain_vested = 0.0
        grant_unrealized_gain_all = 0.0
        
        # Enrich vest event data
        enriched_vest_events = []
        is_cash_grant = grant.share_type == 'cash'
        
        # Track grant-level estimated tax-on-sale
        grant_estimated_tax_on_sale = 0.0

        for ve in vest_events:
            has_vested = ve.vest_date <= today
            
            # Get cached rates for this vest's year
            vest_year = ve.vest_date.year
            vest_year_rates = cached_rates_by_year.get(vest_year, tax_rates)
            
            # Calculate estimated or actual taxes (pass cached tax_profile and rates)
            if tax_profile:
                tax_info = ve.estimate_tax_withholding(
                    latest_stock_price, 
                    federal_rate=vest_year_rates['federal'],
                    state_rate=vest_year_rates['state'],
                    _tax_profile=tax_profile
                )
            else:
                tax_info = ve.estimate_tax_withholding(latest_stock_price)
            
            # Get comprehensive tax breakdown (pass cached data to avoid queries)
            if has_vested:
                if tax_profile:
                    # Get total income for this vest's year (for effective SS rate)
                    vest_year_income = annual_incomes_dict.get(vest_year, tax_profile.annual_income)
                    
                    tax_breakdown = ve.get_comprehensive_tax_breakdown(
                        _tax_profile=tax_profile, 
                        _annual_incomes=annual_incomes_dict,
                        _cached_rates=vest_year_rates,
                        _year_income=vest_year_income
                    )
                else:
                    tax_breakdown = ve.get_comprehensive_tax_breakdown()
            else:
                tax_breakdown = None
            
            # For cash grants, shares_vested/shares_received represent USD amounts
            if is_cash_grant:
                shares_held = ve.shares_received if has_vested else ve.shares_vested
                cost_basis_per_share = 1.0  # $1 per $1 for cash
                cost_basis = shares_held  # USD amount
                current_value = shares_held  # Cash value doesn't change
                unrealized_gain = 0.0  # No gain/loss on cash
            else:
                # Stock grants
                shares_held = ve.shares_received if has_vested else ve.shares_vested
                
                # Cost basis depends on grant type:
                # - For ISOs: cost basis is the strike price (share_price_at_grant)
                # - For RSUs/RSAs/ESPP: cost basis is the FMV at vest (share_price_at_vest)
                if grant.share_type in [ShareType.ISO_5Y.value, ShareType.ISO_6Y.value]:
                    # For ISOs, cost basis is the strike/exercise price
                    cost_basis_per_share = grant.share_price_at_grant
                else:
                    # For RSUs/RSAs/ESPP, cost basis is the FMV at vest
                    cost_basis_per_share = ve.share_price_at_vest
                
                cost_basis = shares_held * cost_basis_per_share
                current_value = shares_held * latest_stock_price
                unrealized_gain = current_value - cost_basis
            
            days_held = (today - ve.vest_date).days if has_vested else 0
            is_long_term = days_held >= 365

            # Server-side estimated tax on sale (capital gains)
            if unrealized_gain > 0:
                if is_long_term:
                    estimated_sale_tax = unrealized_gain * (ltcg_rate_default + state_rate_default)
                else:
                    estimated_sale_tax = unrealized_gain * (federal_rate_default + state_rate_default)
            else:
                estimated_sale_tax = 0.0

            # Calculate holding period display
            if has_vested:
                if days_held >= 365:
                    years = days_held // 365
                    holding_period = f"{years}y {days_held % 365}d"
                else:
                    holding_period = f"{days_held}d"
            else:
                holding_period = "—"
            
            # Calculate estimated tax on sale (capital gains)
            # Keep the old JS placeholder behavior but populate server-side value now
            estimated_tax = estimated_sale_tax
            
            ve_data = {
                'vest_event': ve,
                'has_vested': has_vested,
                'shares_held': shares_held,
                'cost_basis_per_share': cost_basis_per_share,
                'cost_basis': cost_basis,
                'current_value': current_value,
                'unrealized_gain': unrealized_gain,
                'days_held': days_held,
                'is_long_term': is_long_term,
                'holding_period': holding_period,
                'tax_amount': tax_info['tax_amount'],
                'tax_is_estimated': tax_info['is_estimated'],
                'tax_rate': tax_info['tax_rate'],
                'estimated_tax': estimated_tax,
                'tax_breakdown': tax_breakdown
            }
            enriched_vest_events.append(ve_data)

            # accumulate grant estimated tax
            grant_estimated_tax_on_sale += estimated_tax

            # Add to grant totals
            grant_shares_held_all += shares_held
            grant_cost_basis_all += cost_basis
            grant_current_value_all += current_value
            grant_unrealized_gain_all += unrealized_gain

            if has_vested:
                grant_shares_held_vested += shares_held
                grant_cost_basis_vested += cost_basis
                grant_current_value_vested += current_value
                grant_unrealized_gain_vested += unrealized_gain
        
        analysis_data.append({
            'grant': grant,
            'vest_events': enriched_vest_events,
            'shares_held_vested': grant_shares_held_vested,
            'shares_held_all': grant_shares_held_all,
            'cost_basis_vested': grant_cost_basis_vested,
            'cost_basis_all': grant_cost_basis_all,
            'current_value_vested': grant_current_value_vested,
            'current_value_all': grant_current_value_all,
            'unrealized_gain_vested': grant_unrealized_gain_vested,
            'unrealized_gain_all': grant_unrealized_gain_all,
            'estimated_tax': grant_estimated_tax_on_sale
        })
        
        # Add to overall totals
        total_shares_held_vested += grant_shares_held_vested
        total_shares_held_all += grant_shares_held_all
        total_cost_basis_vested += grant_cost_basis_vested
        total_cost_basis_all += grant_cost_basis_all
        total_current_value_vested += grant_current_value_vested
        total_current_value_all += grant_current_value_all
        total_unrealized_gain_vested += grant_unrealized_gain_vested
        total_unrealized_gain_all += grant_unrealized_gain_all
        total_estimated_tax += grant_estimated_tax_on_sale
    
    # Debug logging for calculated totals
    logger = logging.getLogger(__name__)

    # Debug logging for calculated totals
    logger.debug(f"Total Shares Held (Vested): {total_shares_held_vested}")
    logger.debug(f"Total Shares Held (All): {total_shares_held_all}")
    logger.debug(f"Total Cost Basis (Vested): {total_cost_basis_vested}")
    logger.debug(f"Total Cost Basis (All): {total_cost_basis_all}")
    logger.debug(f"Total Current Value (Vested): {total_current_value_vested}")
    logger.debug(f"Total Current Value (All): {total_current_value_all}")
    logger.debug(f"Total Unrealized Gain (Vested): {total_unrealized_gain_vested}")
    logger.debug(f"Total Unrealized Gain (All): {total_unrealized_gain_all}")
    
    # Pass all required data to the template
    return render_template('grants/finance_deep_dive.html',
                           analysis_data=analysis_data,
                           latest_stock_price=latest_stock_price,
                           total_shares_held_vested=total_shares_held_vested,
                           total_shares_held_all=total_shares_held_all,
                           total_cost_basis_vested=total_cost_basis_vested,
                           total_cost_basis_all=total_cost_basis_all,
                           total_current_value_vested=total_current_value_vested,
                           total_current_value_all=total_current_value_all,
                           total_unrealized_gain_vested=total_unrealized_gain_vested,
                           total_unrealized_gain_all=total_unrealized_gain_all,
                           total_estimated_tax=total_estimated_tax,
                           tax_rates=tax_rates,
                           use_manual_rates=use_manual_rates)


@grants_bp.route('/vest/<int:vest_id>', methods=['GET', 'POST'])
@login_required
def vest_detail(vest_id):
    """View and edit details for a specific vest event."""
    from app.models.annual_income import AnnualIncome
    from datetime import date
    
    # Get vest event with grant relationship
    vest_event = VestEvent.query.options(
        db.joinedload(VestEvent.grant)
    ).get_or_404(vest_id)
    
    # Security check: ensure this vest belongs to current user's grant
    if vest_event.grant.user_id != current_user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('grants.list_grants'))
    
    if request.method == 'POST':
        # Update notes
        vest_event.notes = request.form.get('notes', '').strip()
        db.session.commit()
        flash('Vest notes updated successfully!', 'success')
        return redirect(url_for('grants.vest_detail', vest_id=vest_id))
    
    # Get tax profile and calculate comprehensive breakdown
    tax_profile = UserTaxProfile.query.filter_by(user_id=current_user.id).first()
    
    # Get annual incomes
    annual_incomes_list = AnnualIncome.query.filter_by(user_id=current_user.id).all()
    annual_incomes_dict = {ai.year: ai.annual_income for ai in annual_incomes_list}
    
    # Get tax breakdown if vested
    tax_breakdown = None
    if vest_event.has_vested:
        vest_year = vest_event.tax_year or vest_event.vest_date.year
        year_income = annual_incomes_dict.get(vest_year, tax_profile.annual_income if tax_profile else None)
        
        # Get cached rates for this year
        cached_rates = None
        if tax_profile and year_income:
            cached_rates = tax_profile.get_tax_rates(tax_year=vest_year, income_override=year_income)
        
        tax_breakdown = vest_event.get_comprehensive_tax_breakdown(
            _tax_profile=tax_profile,
            _annual_incomes=annual_incomes_dict,
            _cached_rates=cached_rates,
            _year_income=year_income
        )
    
    # Get current stock price
    latest_stock_price = get_latest_user_price(current_user.id) or 0.0
    
    return render_template('grants/vest_detail.html',
                         vest_event=vest_event,
                         grant=vest_event.grant,
                         tax_breakdown=tax_breakdown,
                         latest_stock_price=latest_stock_price)


@grants_bp.route('/sale-planning')
@login_required
def sale_planning():
    """Sale planning interface - drag/drop vests into years to optimize taxes"""
    # Get all vest events (vested and unvested)
    vest_events = VestEvent.query.join(Grant).filter(
        Grant.user_id == current_user.id
    ).order_by(VestEvent.vest_date).all()
    
    # Get current stock price
    latest_stock_price = get_latest_user_price(current_user.id) or 0.0
    
    # Get user's tax profile
    tax_profile = UserTaxProfile.query.filter_by(user_id=current_user.id).first()
    if not tax_profile:
        flash('Please configure your tax settings first', 'warning')
        return redirect(url_for('settings.tax_settings'))
    
    # Get existing sale plans
    existing_plans = {}
    for plan in SalePlan.query.filter_by(user_id=current_user.id).all():
        existing_plans[plan.vest_event_id] = plan.planned_sale_year
    
    # Prepare vest data for frontend
    vest_data = []
    for vest in vest_events:
        vest_info = {
            'id': vest.id,
            'grant_id': vest.grant_id,
            'grant_type': vest.grant.grant_type,
            'share_type': vest.grant.share_type,
            'vest_date': vest.vest_date.isoformat(),
            'shares_vested': vest.shares_vested,
            'shares_received': vest.shares_received,
            'has_vested': vest.has_vested,
            'value_at_vest': float(vest.value_at_vest or 0),
            'current_value': float(vest.shares_received * latest_stock_price),
            'planned_year': existing_plans.get(vest.id),
            'strike_price': float(vest.grant.share_price_at_grant or 0)
        }
        vest_data.append(vest_info)
    
    # Years to display (2026-2035)
    current_year = date.today().year
    years = list(range(current_year, 2036))  # 2026-2035
    
    return render_template('grants/sale_planning.html',
                         vest_data=vest_data,
                         years=years,
                         tax_profile=tax_profile,
                         latest_stock_price=latest_stock_price)


@grants_bp.route('/api/sale-planning/save', methods=['POST'])
@login_required
def save_sale_plan():
    """Save user's sale plan (which vests to sell in which year)"""
    try:
        data = request.get_json()
        plans = data.get('plans', {})  # {vest_event_id: year}
        
        # Delete existing plans
        SalePlan.query.filter_by(user_id=current_user.id).delete()
        
        # Create new plans
        for vest_id_str, year in plans.items():
            vest_id = int(vest_id_str)
            if year:  # Only save if assigned to a year
                plan = SalePlan(
                    user_id=current_user.id,
                    vest_event_id=vest_id,
                    planned_sale_year=int(year)
                )
                db.session.add(plan)
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400


@grants_bp.route('/api/sale-planning/calculate-taxes', methods=['POST'])
@login_required
def calculate_sale_taxes():
    """Calculate tax impact of selling specific vests in a given year"""
    try:
        data = request.get_json()
        year = int(data.get('year'))
        vest_ids = data.get('vest_ids', [])
        
        logging.debug(f"Calculating taxes for year {year} with vests {vest_ids}")
        
        # Get tax profile
        tax_profile = UserTaxProfile.query.filter_by(user_id=current_user.id).first()
        if not tax_profile:
            logging.error("Tax profile not found")
            return jsonify({'success': False, 'error': 'Tax profile not found'}), 400
        
        # Handle empty vest list
        if not vest_ids:
            return jsonify({
                'success': True,
                'total_proceeds': 0.0,
                'total_ltcg': 0.0,
                'total_stcg': 0.0,
                'federal_tax_ltcg': 0.0,
                'federal_tax_stcg': 0.0,
                'state_tax': 0.0,
                'niit': 0.0,
                'total_tax': 0.0,
                'net_proceeds': 0.0,
                'ltcg_rate': 15.0,
                'stcg_rate': 24.0
            })
        
        # Get vests
        vests = VestEvent.query.filter(VestEvent.id.in_(vest_ids)).all()
        logging.debug(f"Found {len(vests)} vests")
        
        if not vests:
            return jsonify({'success': False, 'error': 'No vests found'}), 400
        
        # Calculate taxes
        total_ltcg = 0  # Long-term capital gains (held > 1 year)
        total_stcg = 0  # Short-term capital gains (held <= 1 year)
        total_proceeds = 0
        
        sale_date = date(year, 1, 1)  # Assume sale on Jan 1 of that year
        current_price = get_latest_user_price(current_user.id) or 0
        
        logging.debug(f"Current stock price: ${current_price}")
        
        for vest in vests:
            shares = vest.shares_received
            cost_basis = vest.value_at_vest or 0
            proceeds = shares * current_price
            gain = proceeds - cost_basis
            
            # Determine if LTCG or STCG (1 year holding period)
            holding_period = (sale_date - vest.vest_date).days
            if holding_period > 365:
                total_ltcg += gain
            else:
                total_stcg += gain
            
            total_proceeds += proceeds
            logging.debug(f"Vest {vest.id}: {shares} shares, gain ${gain}, holding {holding_period} days")
        
        # Calculate federal taxes
        # LTCG rates: 0%, 15%, 20% based on income
        # STCG taxed as ordinary income
        income = tax_profile.annual_income or 0
        ltcg_rate = 0.15  # Could be 0%, 15%, or 20% based on income
        if income > 500000:
            ltcg_rate = 0.20
        elif income < 80000:
            ltcg_rate = 0.0
        
        stcg_rate = tax_profile.federal_tax_rate / 100.0 if tax_profile.federal_tax_rate else 0.24
        
        federal_tax_ltcg = total_ltcg * ltcg_rate if total_ltcg > 0 else 0
        federal_tax_stcg = total_stcg * stcg_rate if total_stcg > 0 else 0
        state_tax = (total_ltcg + total_stcg) * (tax_profile.state_tax_rate / 100.0 if tax_profile.state_tax_rate else 0)
        
        # NIIT (3.8% on investment income for high earners)
        niit = 0
        if income > 200000:
            niit = (total_ltcg + total_stcg) * 0.038
        
        total_tax = federal_tax_ltcg + federal_tax_stcg + state_tax + niit
        net_proceeds = total_proceeds - total_tax
        
        result = {
            'success': True,
            'total_proceeds': float(total_proceeds),
            'total_ltcg': float(total_ltcg),
            'total_stcg': float(total_stcg),
            'federal_tax_ltcg': float(federal_tax_ltcg),
            'federal_tax_stcg': float(federal_tax_stcg),
            'state_tax': float(state_tax),
            'niit': float(niit),
            'total_tax': float(total_tax),
            'net_proceeds': float(net_proceeds),
            'ltcg_rate': float(ltcg_rate * 100),
            'stcg_rate': float(stcg_rate * 100)
        }
        
        logging.debug(f"Tax calculation result: {result}")
        return jsonify(result)
        
    except Exception as e:
        logging.error(f"Error in calculate_sale_taxes: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 400
