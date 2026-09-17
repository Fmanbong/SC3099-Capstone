from app.models import AuditLog


def audit_event(db, action, user_id=None, resource_type=None, resource_id=None,
                details=None, request=None, severity='info'):
    # Callers supply a small allowlisted metadata object, never the request body.
    entry = AuditLog(action=action, user_id=user_id, resource_type=resource_type,
                     resource_id=str(resource_id) if resource_id is not None else None,
                     details=details or {}, severity=severity,
                     ip_address=request.client.host if request and request.client else None,
                     user_agent=request.headers.get('user-agent', '')[:512] if request else None)
    db.add(entry)
    return entry
