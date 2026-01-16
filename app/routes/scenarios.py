"""
Routes for managing stock price scenarios and projections.
"""

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from app import db
from app.models.stock_sale import StockPriceScenario, ScenarioPricePoint
from app.models.grant import Grant
from app.models.vest_event import VestEvent
from datetime import datetime, date
import logging

scenarios_bp = Blueprint('scenarios', __name__)
logger = logging.getLogger(__name__)


@scenarios_bp.route('/price-scenarios')
@login_required
def price_scenarios():
    """Main page for managing future price scenarios."""
    scenarios = StockPriceScenario.query.filter_by(user_id=current_user.id).all()
    
    # Get all unvested events to show impact
    unvested_events = VestEvent.query.join(Grant).filter(
        Grant.user_id == current_user.id,
        VestEvent.vest_date > date.today()
    ).order_by(VestEvent.vest_date).all()
    
    total_unvested_shares = sum(ve.shares_vested for ve in unvested_events)
    
    return render_template('scenarios/price_scenarios.html',
                         scenarios=scenarios,
                         unvested_events=unvested_events,
                         total_unvested_shares=total_unvested_shares)


@scenarios_bp.route('/api/scenarios', methods=['GET'])
@login_required
def get_scenarios():
    """Get all scenarios for current user."""
    scenarios = StockPriceScenario.query.filter_by(user_id=current_user.id).all()
    
    result = []
    for scenario in scenarios:
        price_points = [{
            'date': pp.price_date.isoformat(),
            'price': pp.price
        } for pp in sorted(scenario.price_points, key=lambda x: x.price_date)]
        
        result.append({
            'id': scenario.id,
            'name': scenario.scenario_name,
            'description': scenario.description,
            'is_active': scenario.is_active,
            'price_points': price_points,
            'created_at': scenario.created_at.isoformat()
        })
    
    return jsonify(result)


@scenarios_bp.route('/api/scenarios', methods=['POST'])
@login_required
def create_scenario():
    """Create a new price scenario."""
    try:
        data = request.get_json()
        
        scenario = StockPriceScenario(
            user_id=current_user.id,
            scenario_name=data['name'],
            description=data.get('description', ''),
            is_active=data.get('is_active', True)
        )
        
        db.session.add(scenario)
        db.session.flush()  # Get scenario ID
        
        # Add price points
        for point in data.get('price_points', []):
            price_point = ScenarioPricePoint(
                scenario_id=scenario.id,
                price_date=datetime.fromisoformat(point['date']).date(),
                price=float(point['price'])
            )
            db.session.add(price_point)
        
        db.session.commit()
        
        return jsonify({
            'id': scenario.id,
            'name': scenario.scenario_name,
            'message': f'Scenario "{scenario.scenario_name}" created successfully'
        }), 201
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating scenario: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 400


@scenarios_bp.route('/api/scenarios/<int:scenario_id>', methods=['PUT'])
@login_required
def update_scenario(scenario_id):
    """Update an existing scenario."""
    try:
        scenario = StockPriceScenario.query.filter_by(
            id=scenario_id,
            user_id=current_user.id
        ).first_or_404()
        
        data = request.get_json()
        
        scenario.scenario_name = data.get('name', scenario.scenario_name)
        scenario.description = data.get('description', scenario.description)
        scenario.is_active = data.get('is_active', scenario.is_active)
        scenario.updated_at = datetime.utcnow()
        
        # Update price points - delete old ones and add new
        if 'price_points' in data:
            # Delete existing points
            ScenarioPricePoint.query.filter_by(scenario_id=scenario.id).delete()
            
            # Add new points
            for point in data['price_points']:
                price_point = ScenarioPricePoint(
                    scenario_id=scenario.id,
                    price_date=datetime.fromisoformat(point['date']).date(),
                    price=float(point['price'])
                )
                db.session.add(price_point)
        
        db.session.commit()
        
        return jsonify({
            'id': scenario.id,
            'name': scenario.scenario_name,
            'message': f'Scenario "{scenario.scenario_name}" updated successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating scenario: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 400


@scenarios_bp.route('/api/scenarios/<int:scenario_id>', methods=['DELETE'])
@login_required
def delete_scenario(scenario_id):
    """Delete a scenario."""
    try:
        scenario = StockPriceScenario.query.filter_by(
            id=scenario_id,
            user_id=current_user.id
        ).first_or_404()
        
        scenario_name = scenario.scenario_name
        db.session.delete(scenario)
        db.session.commit()
        
        return jsonify({
            'message': f'Scenario "{scenario_name}" deleted successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting scenario: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 400


@scenarios_bp.route('/api/scenarios/<int:scenario_id>/projection', methods=['GET'])
@login_required
def get_scenario_projection(scenario_id):
    """Get future value projection for a scenario."""
    try:
        from sqlalchemy import and_
        
        scenario = StockPriceScenario.query.filter_by(
            id=scenario_id,
            user_id=current_user.id
        ).first_or_404()
        
        # Get current vested shares (already owned)
        vested_events = VestEvent.query.join(Grant).filter(
            and_(
                Grant.user_id == current_user.id,
                VestEvent.vest_date <= date.today()
            )
        ).all()
        
        # Calculate current vested shares by grant
        vested_shares_by_grant = {}
        for vest in vested_events:
            grant_id = vest.grant_id
            if grant_id not in vested_shares_by_grant:
                vested_shares_by_grant[grant_id] = 0
            vested_shares_by_grant[grant_id] += vest.shares_vested
        
        # Get all unvested events (future)
        from sqlalchemy import and_
        unvested_events = VestEvent.query.join(Grant).filter(
            and_(
                Grant.user_id == current_user.id,
                VestEvent.vest_date > date.today()
            )
        ).all()
        
        logger.info(f"Found {len(vested_events)} vested events, {len(unvested_events)} unvested events")
        logger.info(f"Scenario has {len(scenario.price_points)} price points")
        
        # Calculate current portfolio value
        current_price = scenario.get_price_at_date(date.today())
        current_portfolio_value = 0
        
        if current_price:
            for grant_id, shares in vested_shares_by_grant.items():
                grant = Grant.query.get(grant_id)
                if grant.share_type in ['iso_5y', 'iso_6y']:
                    value_per_share = max(0, current_price - grant.share_price_at_grant)
                else:
                    value_per_share = current_price
                current_portfolio_value += shares * value_per_share
        
        logger.info(f"Current portfolio value at ${current_price}: ${current_portfolio_value}")
        
        projections = []
        cumulative_value = current_portfolio_value
        total_new_value = 0
        
        # Sort vests by date to calculate cumulative properly
        unvested_events_sorted = sorted(unvested_events, key=lambda v: v.vest_date)
        
        for vest in unvested_events_sorted:
            try:
                projected_price = scenario.get_price_at_date(vest.vest_date)
                
                logger.info(f"Vest {vest.id} on {vest.vest_date}: projected price = {projected_price}")
                
                if projected_price is not None:
                    # For ISOs, use spread (price - strike)
                    grant = vest.grant
                    if grant.share_type in ['iso_5y', 'iso_6y']:
                        value_per_share = max(0, projected_price - grant.share_price_at_grant)
                    else:
                        value_per_share = projected_price
                    
                    projected_value = vest.shares_vested * value_per_share
                    total_new_value += projected_value
                    cumulative_value += projected_value
                    
                    projections.append({
                        'vest_date': vest.vest_date.isoformat(),
                        'shares': vest.shares_vested,
                        'projected_price': projected_price,
                        'projected_value': projected_value,
                        'cumulative_value': cumulative_value,
                        'grant_type': grant.grant_type,
                        'share_type': grant.share_type
                    })
            except Exception as vest_error:
                logger.error(f"Error processing vest {vest.id}: {str(vest_error)}", exc_info=True)
                continue
        
        return jsonify({
            'scenario_name': scenario.scenario_name,
            'current_portfolio_value': current_portfolio_value,
            'total_projected_value': cumulative_value,
            'new_vests_value': total_new_value,
            'projections': projections
        })
        
    except Exception as e:
        logger.error(f"Error getting projection: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@scenarios_bp.route('/api/scenarios/compare', methods=['POST'])
@login_required
def compare_scenarios():
    """Compare multiple scenarios side-by-side."""
    try:
        data = request.get_json()
        scenario_ids = data.get('scenario_ids', [])
        
        if not scenario_ids:
            return jsonify({'error': 'No scenarios selected'}), 400
        
        scenarios = StockPriceScenario.query.filter(
            StockPriceScenario.id.in_(scenario_ids),
            StockPriceScenario.user_id == current_user.id
        ).all()
        
        # Get unvested events
        unvested_events = VestEvent.query.join(Grant).filter(
            Grant.user_id == current_user.id,
            VestEvent.vest_date > date.today()
        ).order_by(VestEvent.vest_date).all()
        
        comparison = []
        
        for scenario in scenarios:
            total_value = 0
            
            for vest in unvested_events:
                projected_price = scenario.get_price_at_date(vest.vest_date)
                
                if projected_price:
                    grant = vest.grant
                    if grant.share_type in ['iso_5y', 'iso_6y']:
                        value_per_share = max(0, projected_price - grant.share_price_at_grant)
                    else:
                        value_per_share = projected_price
                    
                    total_value += vest.shares_vested * value_per_share
            
            comparison.append({
                'scenario_id': scenario.id,
                'scenario_name': scenario.scenario_name,
                'total_projected_value': total_value
            })
        
        return jsonify(comparison)
        
    except Exception as e:
        logger.error(f"Error comparing scenarios: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 400
