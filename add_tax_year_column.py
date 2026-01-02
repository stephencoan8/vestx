#!/usr/bin/env python3
"""
Add tax_year column to vest_events table.
Run this once on production database.
"""

from app import create_app
from app.models.vest_event import db
from sqlalchemy import text

def add_tax_year_column():
    """Add tax_year column to vest_events table if it doesn't exist."""
    app = create_app()
    
    with app.app_context():
        try:
            # Check if column exists
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='vest_events' 
                AND column_name='tax_year'
            """))
            
            if result.fetchone() is None:
                print("Adding tax_year column to vest_events table...")
                
                # Add the column
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
                print("✓ Successfully added tax_year column and populated with vest years")
            else:
                print("✓ tax_year column already exists")
                
        except Exception as e:
            print(f"✗ Error adding column: {e}")
            db.session.rollback()
            raise

if __name__ == '__main__':
    add_tax_year_column()
