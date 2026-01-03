"""
Migration script to create sale_plans table
"""
import os
from app import create_app
from app import db

def create_sale_plans_table():
    """Create sale_plans table if it doesn't exist"""
    app = create_app()
    
    with app.app_context():
        try:
            # Check database type
            db_url = os.environ.get('DATABASE_URL', app.config.get('SQLALCHEMY_DATABASE_URI', ''))
            is_postgres = 'postgresql' in db_url
            
            if is_postgres:
                # Check if table exists (PostgreSQL)
                check_query = """
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'sale_plans'
                    )
                """
                result = db.session.execute(db.text(check_query))
                table_exists = result.scalar()
            else:
                # SQLite
                check_query = "SELECT name FROM sqlite_master WHERE type='table' AND name='sale_plans'"
                result = db.session.execute(db.text(check_query))
                table_exists = result.fetchone() is not None
            
            if table_exists:
                print("✓ Table 'sale_plans' already exists")
                return
            
            # Create table
            print("Creating sale_plans table...")
            create_table_sql = """
                CREATE TABLE sale_plans (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    vest_event_id INTEGER NOT NULL REFERENCES vest_events(id),
                    planned_sale_year INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """ if is_postgres else """
                CREATE TABLE sale_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    vest_event_id INTEGER NOT NULL REFERENCES vest_events(id),
                    planned_sale_year INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            
            db.session.execute(db.text(create_table_sql))
            
            # Create indexes
            db.session.execute(db.text(
                "CREATE INDEX idx_sale_plans_user ON sale_plans(user_id)"
            ))
            db.session.execute(db.text(
                "CREATE INDEX idx_sale_plans_vest ON sale_plans(vest_event_id)"
            ))
            
            db.session.commit()
            print("✓ Successfully created sale_plans table")
            
        except Exception as e:
            db.session.rollback()
            print(f"✗ Error creating sale_plans table: {str(e)}")
            raise

if __name__ == '__main__':
    create_sale_plans_table()
