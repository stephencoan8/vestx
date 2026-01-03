"""
Migration script to add notes column to vest_events table (PostgreSQL version)
Safe to run multiple times - checks if column exists before adding
"""
import os
from app import create_app
from app import db

def add_notes_column():
    """Add notes column to vest_events table if it doesn't exist"""
    app = create_app()
    
    with app.app_context():
        try:
            # Check if notes column exists (PostgreSQL)
            check_query = """
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='vest_events' 
                AND column_name='notes'
            """
            result = db.session.execute(db.text(check_query))
            column_exists = result.fetchone() is not None
            
            if column_exists:
                print("✓ Column 'notes' already exists in vest_events table")
                return
            
            # Add notes column
            print("Adding notes column to vest_events table...")
            db.session.execute(db.text(
                "ALTER TABLE vest_events ADD COLUMN notes TEXT"
            ))
            db.session.commit()
            print("✓ Successfully added notes column to vest_events table")
            
        except Exception as e:
            db.session.rollback()
            print(f"✗ Error adding notes column: {str(e)}")
            raise

if __name__ == '__main__':
    add_notes_column()
