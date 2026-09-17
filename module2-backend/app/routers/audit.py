"""Read-only administrative access to the append-only audit trail."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, not_, or_, select

from app.database import utcnow
from app.dependencies import get_db, require_roles
from app.errors import ApiError
from app.models import AuditLog, User
from app.serializers import page


router = APIRouter(prefix="/audit", tags=["Audit"])
_SENSITIVE_KEY_PARTS = (
    "password", "passwd", "secret", "token", "private_key", "public_key",
    "embedding", "hash", "image", "biometric", "email", "name", "review",
    "note", "credential", "nonce", "signature",
)


def _safe_details(value):
    if isinstance(value, dict):
        return {
            str(key): _safe_details(item)
            for key, item in value.items()
            if not any(part in str(key).casefold() for part in _SENSITIVE_KEY_PARTS)
        }
    if isinstance(value, list):
        return [_safe_details(item) for item in value]
    return value


def _succeeded(log: AuditLog) -> bool:
    explicit = (log.details or {}).get("success")
    return explicit if isinstance(explicit, bool) else (
        not log.action.endswith("_failed") and log.severity not in {"error", "critical"}
    )


def _audit_data(log: AuditLog, db) -> dict:
    user = db.get(User, log.user_id) if log.user_id else None
    safe_details = _safe_details(log.details or {})
    return {
        "id": log.id,
        "user_id": log.user_id,
        "user_email": user.email if user else None,
        "action": log.action,
        "resource_type": log.resource_type,
        "resource_id": log.resource_id,
        "ip_address": log.ip_address,
        "user_agent": log.user_agent,
        "device_id": safe_details.get("device_id") if isinstance(safe_details, dict) else None,
        "details": safe_details,
        "severity": log.severity,
        "success": _succeeded(log),
        "timestamp": log.created_at,
        "created_at": log.created_at,
    }


def _normalized(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _filter_conditions(
    *,
    user_id=None,
    action=None,
    resource_type=None,
    resource_id=None,
    start_date=None,
    end_date=None,
) -> list:
    conditions = []
    if user_id is not None:
        conditions.append(AuditLog.user_id == str(user_id))
    if action is not None:
        conditions.append(AuditLog.action == action)
    if resource_type is not None:
        conditions.append(AuditLog.resource_type == resource_type)
    if resource_id is not None:
        conditions.append(AuditLog.resource_id == str(resource_id))
    start, end = _normalized(start_date), _normalized(end_date)
    if start:
        conditions.append(AuditLog.created_at >= start)
    if end:
        conditions.append(AuditLog.created_at <= end)
    return conditions


def _success_expression():
    explicit = AuditLog.details["success"].as_boolean()
    inferred_failure = or_(
        AuditLog.action.endswith("_failed"),
        AuditLog.severity.in_(("error", "critical")),
    )
    return or_(
        explicit.is_(True),
        and_(explicit.is_(None), not_(inferred_failure)),
    )


@router.get("/summary")
def audit_summary(
    days: int = Query(7, ge=1, le=365),
    db=Depends(get_db),
    actor=Depends(require_roles("admin")),
):
    del actor
    start = utcnow() - timedelta(days=days)
    condition = AuditLog.created_at >= start
    total = db.scalar(select(func.count(AuditLog.id)).where(condition)) or 0
    by_action = dict(db.execute(select(AuditLog.action, func.count(AuditLog.id)).where(
        condition
    ).group_by(AuditLog.action)).all())
    resource_rows = db.execute(select(AuditLog.resource_type, func.count(AuditLog.id)).where(
        condition
    ).group_by(AuditLog.resource_type)).all()
    by_resource_type = {key or "none": value for key, value in resource_rows}
    by_severity = dict(db.execute(select(AuditLog.severity, func.count(AuditLog.id)).where(
        condition
    ).group_by(AuditLog.severity)).all())
    day_rows = db.execute(select(func.date(AuditLog.created_at), func.count(AuditLog.id)).where(
        condition
    ).group_by(func.date(AuditLog.created_at)).order_by(func.date(AuditLog.created_at))).all()
    return {
        "period_days": days,
        "total_logs": total,
        "by_action": dict(sorted(by_action.items())),
        "by_resource_type": dict(sorted(by_resource_type.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "by_day": [
            {"date": str(day), "count": count} for day, count in day_rows
        ],
    }


@router.get("/")
@router.get("/logs", include_in_schema=False)
def list_audit_logs(
    user_id: UUID | None = None,
    action: str | None = Query(None, max_length=100),
    resource_type: str | None = Query(None, max_length=100),
    resource_id: UUID | None = None,
    success: bool | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    actor=Depends(require_roles("admin")),
):
    del actor
    start, end = _normalized(start_date), _normalized(end_date)
    if start and end and start > end:
        raise ApiError(400, "start_date must not be after end_date", "INVALID_DATE_RANGE")
    conditions = _filter_conditions(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        start_date=start,
        end_date=end,
    )
    if success is not None:
        success_expression = _success_expression()
        conditions.append(success_expression if success else not_(success_expression))
    base = select(AuditLog).where(*conditions)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    logs = db.scalars(base.order_by(
        AuditLog.created_at.desc(), AuditLog.id.desc()
    ).offset(offset).limit(limit)).all()
    return page([_audit_data(log, db) for log in logs], total, limit, offset)
