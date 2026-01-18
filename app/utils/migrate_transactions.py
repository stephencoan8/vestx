"""
Migration script to add transaction tracking tables.
Run this once to add stock_sales and iso_exercises tables.
"""

from app import db
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)


def migrate_transactions(app):
    """Add transaction tracking tables if they don't exist."""
    with app.app_context():
        try:
            # Check if tables exist by trying to query them
            db.session.execute(text('SELECT 1 FROM stock_sales LIMIT 1'))
            logger.info("stock_sales table already exists")
        except Exception:
            logger.info("Creating stock_sales and iso_exercises tables...")
            # Create all tables defined in models
            db.create_all()
            logger.info("✅ Transaction tables created")
        
        # Check and add tax_year column to vest_events if missing
        try:
            db.session.execute(text('SELECT tax_year FROM vest_events LIMIT 1'))
            logger.info("tax_year column already exists in vest_events")
        except Exception:
            logger.info("Adding tax_year column to vest_events...")
            db.session.execute(text('ALTER TABLE vest_events ADD COLUMN tax_year INTEGER'))
            db.session.commit()
            logger.info("✅ tax_year column added to vest_events")


if __name__ == '__main__':
    from app import create_app
    app = create_app()
    migrate_transactions(app)
    print("Migration complete!")
