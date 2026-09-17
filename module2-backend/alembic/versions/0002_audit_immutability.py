"""Enforce append-only audit events at the database boundary."""
from alembic import op

revision = '0002_audit_immutability'
down_revision = 'd5b134da1bfd'
branch_labels = None
depends_on = None


def upgrade():
    from app.services.audit_guards import install_audit_guards
    install_audit_guards(op.get_bind())


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute('DROP TRIGGER IF EXISTS saiv_audit_immutable ON audit_logs')
        op.execute('DROP FUNCTION IF EXISTS saiv_guard_audit()')
    elif bind.dialect.name == 'sqlite':
        op.execute('DROP TRIGGER IF EXISTS saiv_audit_no_update')
        op.execute('DROP TRIGGER IF EXISTS saiv_audit_no_delete')
