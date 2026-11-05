"""Staff approval for face enrollments (FACE_ENROLLMENT_APPROVAL)."""
from alembic import op
import sqlalchemy as sa

revision = '0004_face_enrollment_approval'
down_revision = '0003_enrollment_history'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('face_enrollment_status', sa.String(20), nullable=False, server_default='none'))
    op.add_column('users', sa.Column('pending_face_hash', sa.String(256)))
    op.add_column('users', sa.Column('face_enrollment_requested_at', sa.DateTime(timezone=True)))
    op.add_column('users', sa.Column('face_enrollment_reviewed_by_id', sa.String(36),
                                     sa.ForeignKey('users.id', name='fk_users_face_enrollment_reviewer',
                                                   ondelete='SET NULL')))
    op.add_column('users', sa.Column('face_enrollment_reviewed_at', sa.DateTime(timezone=True)))
    op.add_column('users', sa.Column('face_enrollment_review_notes', sa.Text()))
    op.create_check_constraint('ck_users_face_enrollment_status', 'users',
                               "face_enrollment_status IN ('none','pending','approved','rejected')")
    op.create_index('ix_users_face_enrollment_status', 'users', ['face_enrollment_status'])
    # Faces enrolled before approval existed keep working.
    op.execute("UPDATE users SET face_enrollment_status = 'approved' WHERE face_enrolled")


def downgrade():
    op.drop_index('ix_users_face_enrollment_status', 'users')
    op.drop_constraint('ck_users_face_enrollment_status', 'users', type_='check')
    for column in ('face_enrollment_review_notes', 'face_enrollment_reviewed_at', 'face_enrollment_reviewed_by_id',
                   'face_enrollment_requested_at', 'pending_face_hash', 'face_enrollment_status'):
        op.drop_column('users', column)
