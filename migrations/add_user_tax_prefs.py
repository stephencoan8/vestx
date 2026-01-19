"""Add simplified tax preferences to User model

Revision ID: add_user_tax_prefs
Revises: 
Create Date: 2026-01-19

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_user_tax_prefs'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Add tax preference columns to users table
    op.add_column('users', sa.Column('federal_tax_rate', sa.Float(), nullable=True))
    op.add_column('users', sa.Column('state_tax_rate', sa.Column(), nullable=True))
    op.add_column('users', sa.Column('include_fica', sa.Boolean(), nullable=True))
    
    # Set default values for existing users
    op.execute("UPDATE users SET federal_tax_rate = 0.22 WHERE federal_tax_rate IS NULL")
    op.execute("UPDATE users SET state_tax_rate = 0.0 WHERE state_tax_rate IS NULL")
    op.execute("UPDATE users SET include_fica = TRUE WHERE include_fica IS NULL")


def downgrade():
    # Remove tax preference columns
    op.drop_column('users', 'include_fica')
    op.drop_column('users', 'state_tax_rate')
    op.drop_column('users', 'federal_tax_rate')
