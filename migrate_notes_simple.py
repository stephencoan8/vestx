"""
Simple migration script to add notes column - no Flask dependencies
"""
import os
import psycopg2

# Get database URL from environment
DATABASE_URL = os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable not set")
    exit(1)

print(f"Connecting to database...")

try:
    # Connect directly with psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    # Check if column exists
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='vest_events' 
        AND column_name='notes'
    """)
    
    if cursor.fetchone():
        print("✓ Column 'notes' already exists in vest_events table")
    else:
        print("Adding notes column...")
        cursor.execute("ALTER TABLE vest_events ADD COLUMN notes TEXT")
        conn.commit()
        print("✓ Successfully added notes column to vest_events table")
    
    cursor.close()
    conn.close()
    
except Exception as e:
    print(f"✗ Error: {str(e)}")
    raise
