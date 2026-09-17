"""Preserve closed enrollment periods when students rejoin a course."""
from alembic import op
import sqlalchemy as sa

revision = '0003_enrollment_history'
down_revision = '0002_audit_immutability'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('enrollment_periods',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('enrollment_id', sa.String(36), sa.ForeignKey('enrollments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('enrollment_id', 'started_at'))
    op.create_index('ix_enrollment_periods_enrollment_id', 'enrollment_periods', ['enrollment_id'])


def downgrade():
    op.drop_table('enrollment_periods')
