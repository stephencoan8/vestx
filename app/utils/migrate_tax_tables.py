"""
Add stock sales, ISO exercises, and price scenarios tables.

Professional tax engine requires tracking:
- Stock sales with cost basis and holding periods
- ISO exercise events (separate from vesting)
- User-defined future price scenarios

Run with: python app/utils/migrate_tax_tables.py
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app import db


def add_sales_exercises_scenarios():
    """Add new tables for professional tax tracking."""
    
    # SQL to create stock_sales table
    create_stock_sales = """
    CREATE TABLE IF NOT EXISTS stock_sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        vest_event_id INTEGER,
        sale_date DATE NOT NULL,
        shares_sold REAL NOT NULL,
        sale_price REAL NOT NULL,
        total_proceeds REAL NOT NULL,
        cost_basis_per_share REAL NOT NULL,
        total_cost_basis REAL NOT NULL,
        capital_gain REAL NOT NULL,
        is_long_term BOOLEAN NOT NULL,
        is_qualifying_disposition BOOLEAN,
        disqualifying_ordinary_income REAL,
        is_wash_sale BOOLEAN DEFAULT 0,
        wash_sale_loss_disallowed REAL,
        commission_fees REAL DEFAULT 0.0,
        lot_selection_method VARCHAR(20) DEFAULT 'FIFO',
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (vest_event_id) REFERENCES vest_events(id) ON DELETE SET NULL
    );
    """
    
    create_stock_sales_indices = """
    CREATE INDEX IF NOT EXISTS ix_stock_sales_user_id ON stock_sales(user_id);
    CREATE INDEX IF NOT EXISTS ix_stock_sales_sale_date ON stock_sales(sale_date);
    """
    
    # SQL to create iso_exercises table
    create_iso_exercises = """
    CREATE TABLE IF NOT EXISTS iso_exercises (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        vest_event_id INTEGER NOT NULL,
        exercise_date DATE NOT NULL,
        shares_exercised REAL NOT NULL,
        strike_price REAL NOT NULL,
        fmv_at_exercise REAL NOT NULL,
        bargain_element_per_share REAL NOT NULL,
        total_bargain_element REAL NOT NULL,
        amt_triggered BOOLEAN DEFAULT 0,
        amt_paid REAL,
        amt_credit_generated REAL,
        cash_paid REAL,
        shares_surrendered REAL,
        shares_still_held REAL NOT NULL,
        grant_date DATE,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (vest_event_id) REFERENCES vest_events(id) ON DELETE CASCADE
    );
    """
    
    create_iso_exercises_indices = """
    CREATE INDEX IF NOT EXISTS ix_iso_exercises_user_id ON iso_exercises(user_id);
    CREATE INDEX IF NOT EXISTS ix_iso_exercises_exercise_date ON iso_exercises(exercise_date);
    """
    
    # SQL to create stock_price_scenarios table
    create_price_scenarios = """
    CREATE TABLE IF NOT EXISTS stock_price_scenarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        scenario_name VARCHAR(100) NOT NULL,
        description TEXT,
        is_active BOOLEAN DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """
    
    # SQL to create scenario_price_points table
    create_price_points = """
    CREATE TABLE IF NOT EXISTS scenario_price_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scenario_id INTEGER NOT NULL,
        price_date DATE NOT NULL,
        price REAL NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (scenario_id) REFERENCES stock_price_scenarios(id) ON DELETE CASCADE
    );
    """
    
    create_price_points_indices = """
    CREATE INDEX IF NOT EXISTS ix_scenario_price_points_scenario_id ON scenario_price_points(scenario_id);
    CREATE INDEX IF NOT EXISTS ix_scenario_price_points_price_date ON scenario_price_points(price_date);
    """
    
    try:
        # Execute all CREATE TABLE statements
        db.session.execute(db.text(create_stock_sales))
        db.session.execute(db.text("CREATE INDEX IF NOT EXISTS ix_stock_sales_user_id ON stock_sales(user_id)"))
        db.session.execute(db.text("CREATE INDEX IF NOT EXISTS ix_stock_sales_sale_date ON stock_sales(sale_date)"))
        
        db.session.execute(db.text(create_iso_exercises))
        db.session.execute(db.text("CREATE INDEX IF NOT EXISTS ix_iso_exercises_user_id ON iso_exercises(user_id)"))
        db.session.execute(db.text("CREATE INDEX IF NOT EXISTS ix_iso_exercises_exercise_date ON iso_exercises(exercise_date)"))
        
        db.session.execute(db.text(create_price_scenarios))
        
        db.session.execute(db.text(create_price_points))
        db.session.execute(db.text("CREATE INDEX IF NOT EXISTS ix_scenario_price_points_scenario_id ON scenario_price_points(scenario_id)"))
        db.session.execute(db.text("CREATE INDEX IF NOT EXISTS ix_scenario_price_points_price_date ON scenario_price_points(price_date)"))
        
        db.session.commit()
        
        print("✅ Successfully created stock_sales, iso_exercises, and price scenario tables")
        print("\nNew tables:")
        print("  - stock_sales: Track actual stock sales with cost basis")
        print("  - iso_exercises: Track ISO exercise events for AMT calculation")
        print("  - stock_price_scenarios: User-defined future price projections")
        print("  - scenario_price_points: Individual price points in scenarios")
        
        return True
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error creating tables: {str(e)}")
        return False


if __name__ == '__main__':
    import sys
    import os
    # Add parent directory to path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    
    from app import create_app
    app = create_app()
    with app.app_context():
        success = add_sales_exercises_scenarios()
        sys.exit(0 if success else 1)
