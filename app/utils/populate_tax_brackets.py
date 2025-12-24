"""
Populate tax brackets with 2025 federal and state tax rates.
"""

from app import create_app, db
from app.models.tax_rate import TaxBracket


def populate_2025_tax_brackets():
    """Populate database with 2025 tax brackets."""
    app = create_app()
    
    with app.app_context():
        # Clear existing 2025 brackets
        TaxBracket.query.filter_by(tax_year=2025).delete()
        
        # Federal ordinary income tax brackets for 2025 (Single filers)
        federal_single_ordinary_2025 = [
            {'min': 0, 'max': 11600, 'rate': 0.10},
            {'min': 11600, 'max': 47150, 'rate': 0.12},
            {'min': 47150, 'max': 100525, 'rate': 0.22},
            {'min': 100525, 'max': 191950, 'rate': 0.24},
            {'min': 191950, 'max': 243725, 'rate': 0.32},
            {'min': 243725, 'max': 609350, 'rate': 0.35},
            {'min': 609350, 'max': None, 'rate': 0.37},
        ]
        
        # Federal ordinary income tax brackets for 2025 (Married filing jointly)
        federal_joint_ordinary_2025 = [
            {'min': 0, 'max': 23200, 'rate': 0.10},
            {'min': 23200, 'max': 94300, 'rate': 0.12},
            {'min': 94300, 'max': 201050, 'rate': 0.22},
            {'min': 201050, 'max': 383900, 'rate': 0.24},
            {'min': 383900, 'max': 487450, 'rate': 0.32},
            {'min': 487450, 'max': 731200, 'rate': 0.35},
            {'min': 731200, 'max': None, 'rate': 0.37},
        ]
        
        # Federal long-term capital gains tax brackets for 2025 (Single)
        federal_single_ltcg_2025 = [
            {'min': 0, 'max': 47025, 'rate': 0.00},
            {'min': 47025, 'max': 518900, 'rate': 0.15},
            {'min': 518900, 'max': None, 'rate': 0.20},
        ]
        
        # Federal long-term capital gains tax brackets for 2025 (Married filing jointly)
        federal_joint_ltcg_2025 = [
            {'min': 0, 'max': 94050, 'rate': 0.00},
            {'min': 94050, 'max': 583750, 'rate': 0.15},
            {'min': 583750, 'max': None, 'rate': 0.20},
        ]
        
        # Add federal brackets
        for bracket in federal_single_ordinary_2025:
            db.session.add(TaxBracket(
                jurisdiction='federal',
                tax_year=2025,
                filing_status='single',
                tax_type='ordinary',
                income_min=bracket['min'],
                income_max=bracket['max'],
                rate=bracket['rate']
            ))
        
        for bracket in federal_joint_ordinary_2025:
            db.session.add(TaxBracket(
                jurisdiction='federal',
                tax_year=2025,
                filing_status='married_joint',
                tax_type='ordinary',
                income_min=bracket['min'],
                income_max=bracket['max'],
                rate=bracket['rate']
            ))
        
        for bracket in federal_single_ltcg_2025:
            db.session.add(TaxBracket(
                jurisdiction='federal',
                tax_year=2025,
                filing_status='single',
                tax_type='capital_gains_long',
                income_min=bracket['min'],
                income_max=bracket['max'],
                rate=bracket['rate']
            ))
        
        for bracket in federal_joint_ltcg_2025:
            db.session.add(TaxBracket(
                jurisdiction='federal',
                tax_year=2025,
                filing_status='married_joint',
                tax_type='capital_gains_long',
                income_min=bracket['min'],
                income_max=bracket['max'],
                rate=bracket['rate']
            ))
        
        # California state tax brackets for 2025 (Single)
        ca_single_2025 = [
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
        
        for bracket in ca_single_2025:
            db.session.add(TaxBracket(
                jurisdiction='CA',
                tax_year=2025,
                filing_status='single',
                tax_type='ordinary',
                income_min=bracket['min'],
                income_max=bracket['max'],
                rate=bracket['rate']
            ))
        
        # Texas - no state income tax
        db.session.add(TaxBracket(
            jurisdiction='TX',
            tax_year=2025,
            filing_status='single',
            tax_type='ordinary',
            income_min=0,
            income_max=None,
            rate=0.0
        ))
        
        db.session.add(TaxBracket(
            jurisdiction='TX',
            tax_year=2025,
            filing_status='married_joint',
            tax_type='ordinary',
            income_min=0,
            income_max=None,
            rate=0.0
        ))
        
        # Washington - no state income tax
        db.session.add(TaxBracket(
            jurisdiction='WA',
            tax_year=2025,
            filing_status='single',
            tax_type='ordinary',
            income_min=0,
            income_max=None,
            rate=0.0
        ))
        
        db.session.add(TaxBracket(
            jurisdiction='WA',
            tax_year=2025,
            filing_status='married_joint',
            tax_type='ordinary',
            income_min=0,
            income_max=None,
            rate=0.0
        ))
        
        # New York state tax brackets for 2025 (Single) - simplified
        ny_single_2025 = [
            {'min': 0, 'max': 8500, 'rate': 0.04},
            {'min': 8500, 'max': 11700, 'rate': 0.045},
            {'min': 11700, 'max': 13900, 'rate': 0.0525},
            {'min': 13900, 'max': 80650, 'rate': 0.055},
            {'min': 80650, 'max': 215400, 'rate': 0.06},
            {'min': 215400, 'max': 1077550, 'rate': 0.0685},
            {'min': 1077550, 'max': 5000000, 'rate': 0.0965},
            {'min': 5000000, 'max': 25000000, 'rate': 0.103},
            {'min': 25000000, 'max': None, 'rate': 0.109},
        ]
        
        for bracket in ny_single_2025:
            db.session.add(TaxBracket(
                jurisdiction='NY',
                tax_year=2025,
                filing_status='single',
                tax_type='ordinary',
                income_min=bracket['min'],
                income_max=bracket['max'],
                rate=bracket['rate']
            ))
        
        db.session.commit()
        
        count = TaxBracket.query.filter_by(tax_year=2025).count()
        print(f"✅ Successfully populated {count} tax brackets for 2025!")


if __name__ == '__main__':
    populate_2025_tax_brackets()
