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
    try:
        with app.app_context():
            # First create all tables if they don't exist
            try:
                db.create_all()
                logger.info("✅ Ensured all tables exist (create_all)")
            except Exception as e:
                logger.error(f"Error in db.create_all(): {e}")
            
            # Check and add tax_year column to vest_events if missing
            try:
                result = db.session.execute(text('SELECT tax_year FROM vest_events LIMIT 1'))
                logger.info("tax_year column already exists in vest_events")
            except Exception as e:
                logger.info(f"tax_year column missing, attempting to add: {e}")
                try:
                    db.session.execute(text('ALTER TABLE vest_events ADD COLUMN tax_year INTEGER'))
                    db.session.commit()
                    logger.info("✅ tax_year column added to vest_events")
                except Exception as alter_error:
                    logger.warning(f"Could not add tax_year column (may already exist): {alter_error}")
                    db.session.rollback()
            
            # Check and add actual tax columns to stock_sales if missing
            try:
                result = db.session.execute(text('SELECT actual_federal_tax FROM stock_sales LIMIT 1'))
                result.close()
                logger.info("actual tax columns already exist in stock_sales")
            except Exception as e:
                # Rollback the failed SELECT transaction
                db.session.rollback()
                logger.info(f"actual tax columns missing in stock_sales, attempting to add: {e}")
                try:
                    db.session.execute(text('ALTER TABLE stock_sales ADD COLUMN actual_federal_tax FLOAT'))
                    db.session.execute(text('ALTER TABLE stock_sales ADD COLUMN actual_state_tax FLOAT'))
                    db.session.execute(text('ALTER TABLE stock_sales ADD COLUMN actual_total_tax FLOAT'))
                    db.session.commit()
                    logger.info("✅ actual tax columns added to stock_sales")
                except Exception as alter_error:
                    logger.warning(f"Could not add actual tax columns (may already exist): {alter_error}")
                    db.session.rollback()
            
            # Check and add tax preference columns to users if missing
            try:
                result = db.session.execute(text('SELECT federal_tax_rate FROM users LIMIT 1'))
                result.close()
                logger.info("tax preference columns already exist in users")
            except Exception as e:
                # Rollback the failed SELECT transaction
                db.session.rollback()
                logger.info(f"tax preference columns missing in users, attempting to add: {e}")
                try:
                    db.session.execute(text('ALTER TABLE users ADD COLUMN federal_tax_rate FLOAT'))
                    db.session.execute(text('ALTER TABLE users ADD COLUMN state_tax_rate FLOAT'))
                    db.session.execute(text('ALTER TABLE users ADD COLUMN include_fica BOOLEAN'))
                    # Set defaults for existing users
                    db.session.execute(text('UPDATE users SET federal_tax_rate = 0.22 WHERE federal_tax_rate IS NULL'))
                    db.session.execute(text('UPDATE users SET state_tax_rate = 0.0 WHERE state_tax_rate IS NULL'))
                    db.session.execute(text('UPDATE users SET include_fica = TRUE WHERE include_fica IS NULL'))
                    db.session.commit()
                    logger.info("✅ tax preference columns added to users")
                except Exception as alter_error:
                    logger.warning(f"Could not add tax preference columns (may already exist): {alter_error}")
                    db.session.rollback()
    
    except Exception as e:
        logger.error(f"Migration failed but continuing: {e}")
        # Don't crash the app even if migration fails


if __name__ == '__main__':
    from app import create_app
    app = create_app()
    migrate_transactions(app)
    print("Migration complete!")
