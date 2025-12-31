"""
Database initialization utilities.
"""

from app import db
from app.models.user import User
from datetime import date
import os


def init_admin_user():
    """Create admin user if it doesn't exist."""
    admin_username = os.getenv('ADMIN_USERNAME', 'admin')
    admin_password = os.getenv('ADMIN_PASSWORD', 'admin')
    
    admin = User.query.filter_by(username=admin_username).first()
    if not admin:
        admin = User(
            username=admin_username,
            email='admin@vestx.com',
            is_admin=True
        )
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()
        print(f"Admin user created: {admin_username}")
