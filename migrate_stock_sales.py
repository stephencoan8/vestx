"""Add actual tax columns to stock_sales table"""
from app import db
from sqlalchemy import text

def upgrade():
    """Add actual_federal_tax, actual_state_tax, actual_total_tax columns to stock_sales"""
    try:
        # Check if columns already exist
        with db.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='stock_sales' 
                AND column_name IN ('actual_federal_tax', 'actual_state_tax', 'actual_total_tax')
            """))
            existing_columns = {row[0] for row in result}
            
            # Add missing columns
            if 'actual_federal_tax' not in existing_columns:
                conn.execute(text("""
                    ALTER TABLE stock_sales 
                    ADD COLUMN actual_federal_tax FLOAT
                """))
                conn.commit()
                print("✓ Added actual_federal_tax column")
            
            if 'actual_state_tax' not in existing_columns:
                conn.execute(text("""
                    ALTER TABLE stock_sales 
                    ADD COLUMN actual_state_tax FLOAT
                """))
                conn.commit()
                print("✓ Added actual_state_tax column")
            
            if 'actual_total_tax' not in existing_columns:
                conn.execute(text("""
                    ALTER TABLE stock_sales 
                    ADD COLUMN actual_total_tax FLOAT
                """))
                conn.commit()
                print("✓ Added actual_total_tax column")
        
        print("✅ Migration completed successfully")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        raise

if __name__ == '__main__':
    from app import create_app
    app = create_app()
    with app.app_context():
        upgrade()
