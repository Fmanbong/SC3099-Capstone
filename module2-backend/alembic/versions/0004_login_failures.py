"""Persist consecutive account password failures across restarts."""
from alembic import op
import sqlalchemy as sa

revision = '0004_login_failures'
down_revision = '0003_enrollment_history'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('failed_login_attempts', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('users', 'failed_login_attempts')
