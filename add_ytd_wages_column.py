#!/usr/bin/env python3
"""
Add ytd_wages column to user_tax_profiles table.
Run this once on production database.
"""

import os
from app import create_app
from app.models.tax_rate import db, UserTaxProfile
from sqlalchemy import text

def add_ytd_wages_column():
    """Add ytd_wages column to user_tax_profiles table if it doesn't exist."""
    app = create_app()
    
    with app.app_context():
        try:
            # Check if column exists
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='user_tax_profiles' 
                AND column_name='ytd_wages'
            """))
            
            if result.fetchone() is None:
                print("Adding ytd_wages column to user_tax_profiles table...")
                
                # Add the column with default value of 0.0
                db.session.execute(text("""
                    ALTER TABLE user_tax_profiles 
                    ADD COLUMN ytd_wages FLOAT DEFAULT 0.0
                """))
                
                db.session.commit()
                print("✓ Successfully added ytd_wages column")
            else:
                print("✓ ytd_wages column already exists")
                
        except Exception as e:
            print(f"✗ Error adding column: {e}")
            db.session.rollback()
            raise

if __name__ == '__main__':
    add_ytd_wages_column()
