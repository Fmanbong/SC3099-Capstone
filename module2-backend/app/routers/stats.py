"""Scoped analytics for administrative and teaching dashboards."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.dependencies import ensure_course_access, get_db, require_roles, visible_course_ids
from app.errors import ApiError
from app.models import Enrollment, Session, User
from app.services.reporting import (
    course_statistics,
    overview_statistics,
    session_statistics,
    student_statistics,
)


router = APIRouter(prefix="/stats", tags=["Statistics"])


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@router.get("/overview")
def stats_overview(
    course_id: UUID | None = None,
    days: int = Query(7, ge=1, le=365),
    db=Depends(get_db),
    actor=Depends(require_roles("admin", "instructor")),
):
    if course_id is not None:
        course = ensure_course_access(db, actor, str(course_id))
        scope = [course.id]
    else:
        scope = visible_course_ids(db, actor)
    return overview_statistics(db, scope, days=days)


@router.get("/sessions/{session_id}")
def stats_session(
    session_id: UUID,
    db=Depends(get_db),
    actor=Depends(require_roles("admin", "instructor", "ta")),
):
    session = db.get(Session, str(session_id))
    if session is None:
        raise ApiError(404, "Session not found", "SESSION_NOT_FOUND")
    ensure_course_access(db, actor, session.course_id)
    return session_statistics(db, session.id)


@router.get("/courses/{course_id}")
def stats_course(
    course_id: UUID,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    db=Depends(get_db),
    actor=Depends(require_roles("admin", "instructor")),
):
    course = ensure_course_access(db, actor, str(course_id))
    start, end = _aware(start_date), _aware(end_date)
    if start and end and start > end:
        raise ApiError(400, "start_date must not be after end_date", "INVALID_DATE_RANGE")
    return course_statistics(db, course.id, start_date=start, end_date=end)


@router.get("/students/{student_id}")
def stats_student(
    student_id: UUID,
    db=Depends(get_db),
    actor=Depends(require_roles("admin", "instructor")),
):
    student = db.get(User, str(student_id))
    if student is None or student.role != "student":
        raise ApiError(404, "Student not found", "STUDENT_NOT_FOUND")
    scope = visible_course_ids(db, actor)
    if actor.role != "admin":
        has_shared_course = db.scalar(
            select(Enrollment.id).where(
                Enrollment.student_id == student.id,
                Enrollment.course_id.in_(scope),
            )
        ) if scope else None
        if not has_shared_course:
            raise ApiError(403, "You do not have access to this student", "FORBIDDEN")
    return student_statistics(db, student.id, scope)
