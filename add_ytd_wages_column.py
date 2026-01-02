#!/usr/bin/env python3
"""
Add ytd_wages column to user_tax_profiles table AND populate tax brackets.
Run this once on production database.
"""

import os
from app import create_app
from app.models.tax_rate import db, UserTaxProfile, TaxBracket
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

def populate_tax_brackets():
    """Populate 2025 tax brackets."""
    app = create_app()
    
    with app.app_context():
        # Check if already populated
        existing = TaxBracket.query.filter_by(tax_year=2025).count()
        if existing > 0:
            print(f"✓ Tax brackets already populated ({existing} brackets)")
            return
        
        print("Populating 2025 tax brackets...")
        
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
        print(f"✓ Populated {count} tax brackets for 2025")

if __name__ == '__main__':
    add_ytd_wages_column()
    populate_tax_brackets()
