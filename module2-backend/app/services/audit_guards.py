"""Database enforcement for immutable events, allowing only deleted-user unlinking."""


def install_audit_guards(connection):
    if connection.dialect.name == 'postgresql':
        connection.exec_driver_sql('''
        CREATE OR REPLACE FUNCTION saiv_guard_audit() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            IF OLD.user_id IS NOT NULL AND NEW.user_id IS NULL
               AND (to_jsonb(OLD) - 'user_id') = (to_jsonb(NEW) - 'user_id')
               AND NOT EXISTS (SELECT 1 FROM users WHERE id = OLD.user_id)
            THEN RETURN NEW; END IF;
          END IF;
          RAISE EXCEPTION 'Audit entries are append-only';
        END; $$ LANGUAGE plpgsql;
        ''')
        connection.exec_driver_sql('DROP TRIGGER IF EXISTS saiv_audit_immutable ON audit_logs')
        connection.exec_driver_sql('''CREATE TRIGGER saiv_audit_immutable BEFORE UPDATE OR DELETE
            ON audit_logs FOR EACH ROW EXECUTE FUNCTION saiv_guard_audit()''')
    elif connection.dialect.name == 'sqlite':
        same = ' AND '.join(f'NEW.{name} IS OLD.{name}' for name in (
            'id', 'action', 'resource_type', 'resource_id', 'details', 'ip_address', 'user_agent', 'severity', 'created_at'))
        connection.exec_driver_sql(f'''CREATE TRIGGER IF NOT EXISTS saiv_audit_no_update
            BEFORE UPDATE ON audit_logs WHEN NOT (
                OLD.user_id IS NOT NULL AND NEW.user_id IS NULL AND {same}
                AND NOT EXISTS (SELECT 1 FROM users WHERE id = OLD.user_id))
            BEGIN SELECT RAISE(ABORT, 'Audit entries are append-only'); END''')
        connection.exec_driver_sql('''CREATE TRIGGER IF NOT EXISTS saiv_audit_no_delete
            BEFORE DELETE ON audit_logs
            BEGIN SELECT RAISE(ABORT, 'Audit entries are append-only'); END''')
