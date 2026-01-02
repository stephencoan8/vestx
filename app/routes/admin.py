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


@admin_bp.route('/run-migration')
@admin_required
def run_migration():
    """Run database migration to add ytd_wages and populate tax brackets."""
    from sqlalchemy import text
    from app.models.tax_rate import TaxBracket
    from app.models.vest_event import VestEvent
    
    try:
        # Create annual_incomes table if it doesn't exist
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS annual_incomes (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                year INTEGER NOT NULL,
                annual_income FLOAT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_user_year_income UNIQUE (user_id, year)
            )
        """))
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_annual_incomes_user_id ON annual_incomes(user_id)
        """))
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_annual_incomes_year ON annual_incomes(year)
        """))
        db.session.commit()
        flash('✓ Created annual_incomes table', 'success')
        
        # Add ytd_wages column
        result = db.session.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='user_tax_profiles' 
            AND column_name='ytd_wages'
        """))
        
        if result.fetchone() is None:
            db.session.execute(text("""
                ALTER TABLE user_tax_profiles 
                ADD COLUMN ytd_wages FLOAT DEFAULT 0.0
            """))
            db.session.commit()
            flash('✓ Added ytd_wages column', 'success')
        else:
            flash('✓ ytd_wages column already exists', 'info')
        
        # Add tax_year column to vest_events
        result = db.session.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='vest_events' 
            AND column_name='tax_year'
        """))
        
        if result.fetchone() is None:
            db.session.execute(text("""
                ALTER TABLE vest_events 
                ADD COLUMN tax_year INTEGER
            """))
            # Update existing records to use vest_date year
            db.session.execute(text("""
                UPDATE vest_events 
                SET tax_year = EXTRACT(YEAR FROM vest_date)
                WHERE tax_year IS NULL
            """))
            db.session.commit()
            flash('✓ Added tax_year column to vest_events', 'success')
        else:
            flash('✓ tax_year column already exists', 'info')
        
        # Populate tax brackets
        existing = TaxBracket.query.filter_by(tax_year=2025).count()
        if existing > 0:
            flash(f'✓ Tax brackets already populated ({existing} brackets)', 'info')
        else:
            # Federal ordinary income (Single)
            federal_single = [
                {'min': 0, 'max': 11600, 'rate': 0.10},
                {'min': 11600, 'max': 47150, 'rate': 0.12},
                {'min': 47150, 'max': 100525, 'rate': 0.22},
                {'min': 100525, 'max': 191950, 'rate': 0.24},
                {'min': 191950, 'max': 243725, 'rate': 0.32},
                {'min': 243725, 'max': 609350, 'rate': 0.35},
                {'min': 609350, 'max': None, 'rate': 0.37},
            ]
            
            for b in federal_single:
                db.session.add(TaxBracket(
                    jurisdiction='federal', tax_year=2025, filing_status='single',
                    tax_type='ordinary', income_min=b['min'], income_max=b['max'], rate=b['rate']
                ))
            
            # Federal LTCG (Single)
            ltcg_single = [
                {'min': 0, 'max': 47025, 'rate': 0.00},
                {'min': 47025, 'max': 518900, 'rate': 0.15},
                {'min': 518900, 'max': None, 'rate': 0.20},
            ]
            
            for b in ltcg_single:
                db.session.add(TaxBracket(
                    jurisdiction='federal', tax_year=2025, filing_status='single',
                    tax_type='capital_gains_long', income_min=b['min'], income_max=b['max'], rate=b['rate']
                ))
            
            # California (Single)
            ca_single = [
                {'min': 0, 'max': 10412, 'rate': 0.01},
                {'min': 10412, 'max': 24684, 'rate': 0.02},
                {'min': 24684, 'max': 38959, 'rate': 0.04},
                {'min': 38959, 'max': 54081, 'rate': 0.06},
                {'min': 54081, 'max': 68350, 'rate': 0.08},
                {'min': 68350, 'max': 349137, 'rate': 0.093},
                {'min': 349137, 'max': 418961, 'rate': 0.103},
                {'min': 418961, 'max': 698271, 'rate': 0.113},
                {'min': 698271, 'max': None, 'rate': 0.123},
            ]
            
            for b in ca_single:
                db.session.add(TaxBracket(
                    jurisdiction='CA', tax_year=2025, filing_status='single',
                    tax_type='ordinary', income_min=b['min'], income_max=b['max'], rate=b['rate']
                ))
            
            db.session.commit()
            count = TaxBracket.query.filter_by(tax_year=2025).count()
            flash(f'✓ Populated {count} tax brackets for 2025', 'success')
        
        flash('Migration completed successfully! Tax rates should now calculate correctly.', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'✗ Error running migration: {str(e)}', 'error')
    
    return redirect(url_for('admin.dashboard'))
