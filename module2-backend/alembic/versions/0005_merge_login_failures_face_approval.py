"""Join the two 0004 branches (login lockout and face enrollment approval).

Both were written on separate branches on top of 0003, so Alembic saw two
heads and `alembic upgrade head` refused to run. This revision has no schema
changes of its own; it only makes a single head again.
"""

revision = '0005_merge_login_face'
down_revision = ('0004_login_failures', '0004_face_enrollment_approval')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
