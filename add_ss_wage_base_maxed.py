#!/usr/bin/env python3
"""
Migration: Add ss_wage_base_maxed column to users table.
This allows users to indicate they've already maxed out their Social Security wage base,
so only Medicare tax applies (not the full 7.65% FICA).
"""

from app import create_app, db
from sqlalchemy import text

def migrate():
    app = create_app()
    with app.app_context():
        try:
            # Check if column already exists
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='users' AND column_name='ss_wage_base_maxed'
            """))
            
            if result.fetchone():
                print("✓ Column ss_wage_base_maxed already exists")
                return
            
            # Add the column
            print("Adding ss_wage_base_maxed column to users table...")
            db.session.execute(text("""
                ALTER TABLE users 
                ADD COLUMN ss_wage_base_maxed BOOLEAN DEFAULT FALSE
            """))
            db.session.commit()
            print("✓ Migration successful!")
            
        except Exception as e:
            db.session.rollback()
            print(f"✗ Migration failed: {e}")
            raise

if __name__ == '__main__':
    migrate()
