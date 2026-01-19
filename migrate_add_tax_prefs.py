"""
Migration script to add tax preference columns to users table.
Run this with: python migrate_add_tax_prefs.py
"""

from app import db, create_app
from sqlalchemy import text

def migrate():
    """Add tax preference columns to users table."""
    app = create_app()
    
    with app.app_context():
        try:
            # Check if columns already exist
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='users' AND column_name='federal_tax_rate'
            """))
            
            if result.fetchone():
                print("Tax preference columns already exist. Skipping migration.")
                return
            
            print("Adding tax preference columns to users table...")
            
            # Add columns
            db.session.execute(text("""
                ALTER TABLE users 
                ADD COLUMN IF NOT EXISTS federal_tax_rate FLOAT,
                ADD COLUMN IF NOT EXISTS state_tax_rate FLOAT,
                ADD COLUMN IF NOT EXISTS include_fica BOOLEAN
            """))
            
            # Set default values for existing users
            db.session.execute(text("""
                UPDATE users 
                SET federal_tax_rate = 0.22,
                    state_tax_rate = 0.0,
                    include_fica = TRUE
                WHERE federal_tax_rate IS NULL
            """))
            
            db.session.commit()
            print("✅ Tax preference columns added successfully!")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error during migration: {e}")
            raise

if __name__ == '__main__':
    migrate()
