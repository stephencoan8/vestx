"""
Force migration to add actual tax columns to stock_sales table.
Run this manually if automatic migration didn't work.
"""

from app import create_app, db
from sqlalchemy import text, inspect
import sys

def force_migrate():
    """Force add actual tax columns to stock_sales."""
    app = create_app()
    
    with app.app_context():
        try:
            # Get inspector to check existing columns
            inspector = inspect(db.engine)
            existing_columns = [col['name'] for col in inspector.get_columns('stock_sales')]
            
            print("Existing columns in stock_sales:")
            for col in existing_columns:
                print(f"  - {col}")
            
            # Check which columns are missing
            required_columns = ['actual_federal_tax', 'actual_state_tax', 'actual_total_tax']
            missing_columns = [col for col in required_columns if col not in existing_columns]
            
            if not missing_columns:
                print("\n✅ All actual tax columns already exist!")
                return True
            
            print(f"\n⚠️  Missing columns: {missing_columns}")
            print("Adding missing columns...")
            
            # Add each missing column
            for col in missing_columns:
                try:
                    db.session.execute(text(f'ALTER TABLE stock_sales ADD COLUMN {col} FLOAT'))
                    print(f"  ✅ Added {col}")
                except Exception as e:
                    print(f"  ❌ Failed to add {col}: {e}")
                    db.session.rollback()
                    return False
            
            # Commit all changes
            db.session.commit()
            print("\n✅ Migration successful!")
            
            # Verify columns were added
            inspector = inspect(db.engine)
            new_columns = [col['name'] for col in inspector.get_columns('stock_sales')]
            
            print("\nVerifying columns after migration:")
            for col in required_columns:
                if col in new_columns:
                    print(f"  ✅ {col} exists")
                else:
                    print(f"  ❌ {col} MISSING")
            
            return True
            
        except Exception as e:
            print(f"\n❌ Migration failed: {e}")
            db.session.rollback()
            return False

if __name__ == '__main__':
    print("=" * 60)
    print("FORCE MIGRATION: Add actual tax columns to stock_sales")
    print("=" * 60)
    
    success = force_migrate()
    
    sys.exit(0 if success else 1)
