"""
Migration utility to add ss_wage_base_maxed column to users table.
"""

from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)


def migrate_ss_wage_base(app):
    """Add ss_wage_base_maxed column to users table if it doesn't exist."""
    from app import db
    
    try:
        # Check if column exists
        result = db.session.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='users' AND column_name='ss_wage_base_maxed'
        """))
        
        if result.fetchone():
            logger.info("✓ ss_wage_base_maxed column already exists")
            return
        
        # Add the column
        logger.info("Adding ss_wage_base_maxed column to users table...")
        db.session.rollback()  # Clear any pending transaction
        db.session.execute(text("""
            ALTER TABLE users 
            ADD COLUMN ss_wage_base_maxed BOOLEAN DEFAULT FALSE
        """))
        db.session.commit()
        logger.info("✓ ss_wage_base_maxed migration successful!")
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Migration failed: {e}", exc_info=True)
        # Don't raise - allow app to continue
