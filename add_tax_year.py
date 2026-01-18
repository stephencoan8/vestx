"""Add tax_year column to vest_events table."""

from app import create_app, db
from sqlalchemy import text

app = create_app()

with app.app_context():
    # Add tax_year column
    db.session.execute(text('ALTER TABLE vest_events ADD COLUMN tax_year INTEGER'))
    db.session.commit()
    print('✅ Added tax_year column to vest_events')
