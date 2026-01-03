"""
Add notes column to vest_events table.
Run this script to update the database: python add_vest_notes.py
"""

from app import create_app, db
from sqlalchemy import text

app = create_app()

def add_notes_column():
    """Add notes column to vest_events table if it doesn't exist."""
    with app.app_context():
        try:
            # Check if column already exists (SQLite specific)
            result = db.session.execute(text("""
                PRAGMA table_info(vest_events)
            """))
            
            columns = [row[1] for row in result.fetchall()]
            
            if 'notes' in columns:
                print("✓ Notes column already exists in vest_events table")
                return
            
            # Add the column
            db.session.execute(text("""
                ALTER TABLE vest_events 
                ADD COLUMN notes TEXT
            """))
            db.session.commit()
            print("✓ Successfully added notes column to vest_events table")
            
        except Exception as e:
            db.session.rollback()
            print(f"✗ Error adding notes column: {e}")
            raise

if __name__ == '__main__':
    print("Adding notes column to vest_events table...")
    add_notes_column()
    print("Migration complete!")
