"""Course and session attendance exports."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.dependencies import ensure_course_access, get_db, require_roles
from app.errors import ApiError
from app.models import Session
from app.services.audit import audit_event
from app.services.reporting import attendance_csv, course_attendance_export, session_attendance_export


router = APIRouter(prefix="/export", tags=["Export"])


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _csv_response(payload: dict, filename: str) -> Response:
    summary = payload["summary"]
    return Response(
        attendance_csv(payload["records"]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Total-Enrolled": str(summary["total_enrolled"]),
            "X-Attendance-Rate": str(summary["attendance_rate"]),
        },
    )


@router.get("/attendance/{course_id}")
def export_course_attendance(
    course_id: UUID,
    request: Request,
    format: Literal["csv", "json"] = "csv",
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    db=Depends(get_db),
    actor=Depends(require_roles("admin", "instructor")),
):
    course = ensure_course_access(db, actor, str(course_id))
    start, end = _aware(start_date), _aware(end_date)
    if start and end and start > end:
        raise ApiError(400, "start_date must not be after end_date", "INVALID_DATE_RANGE")
    payload = course_attendance_export(db, course.id, start_date=start, end_date=end)
    audit_event(
        db,
        "data_exported",
        user_id=actor.id,
        resource_type="course",
        resource_id=course.id,
        details={"format": format, "record_count": len(payload["records"])},
        request=request,
    )
    db.commit()
    if format == "json":
        return payload
    return _csv_response(payload, f"attendance-{course.code}.csv")


@router.get("/session/{session_id}")
def export_session_attendance(
    session_id: UUID,
    request: Request,
    format: Literal["csv", "json"] = "csv",
    db=Depends(get_db),
    actor=Depends(require_roles("admin", "instructor", "ta")),
):
    session = db.get(Session, str(session_id))
    if session is None:
        raise ApiError(404, "Session not found", "SESSION_NOT_FOUND")
    course = ensure_course_access(db, actor, session.course_id)
    payload = session_attendance_export(db, session.id)
    audit_event(
        db,
        "data_exported",
        user_id=actor.id,
        resource_type="session",
        resource_id=session.id,
        details={"format": format, "record_count": len(payload["records"])},
        request=request,
    )
    db.commit()
    if format == "json":
        return payload
    return _csv_response(payload, f"attendance-{course.code}-{session.id}.csv")
