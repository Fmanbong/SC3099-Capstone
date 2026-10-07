"""Attendance session CRUD and relevant-session queries."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as DbSession

from app.database import utcnow
from app.dependencies import (
    current_user,
    ensure_course_access,
    get_db,
    require_roles,
    visible_course_ids,
)
from app.errors import ApiError
from app.models import Course, CourseStaff, Enrollment, Session
from app.schemas import CleanModel, Name
from app.serializers import page, session_data
from app.services.audit import audit_event


router = APIRouter(prefix="/sessions", tags=["sessions"])
Db = Annotated[DbSession, Depends(get_db)]
SessionStatus = Literal["scheduled", "active", "closed", "cancelled"]
SessionType = Literal["lecture", "tutorial", "lab", "exam"]

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "scheduled": {"active", "cancelled"},
    "active": {"closed", "cancelled"},
    "closed": {"cancelled"},
    "cancelled": set(),
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value.astimezone(UTC)


class SessionCreate(CleanModel):
    course_id: UUID
    name: Name
    session_type: SessionType = "lecture"
    description: str | None = Field(default=None, max_length=5000)
    scheduled_start: datetime
    scheduled_end: datetime
    checkin_opens_at: datetime | None = None
    checkin_closes_at: datetime | None = None
    venue_latitude: float | None = Field(default=None, ge=-90, le=90)
    venue_longitude: float | None = Field(default=None, ge=-180, le=180)
    venue_name: str | None = Field(default=None, max_length=255)
    geofence_radius_meters: float | None = Field(default=None, gt=0, le=10_000)
    require_liveness_check: bool = True
    require_face_match: bool | None = None
    risk_threshold: float | None = Field(default=None, ge=0, le=1)

    @field_validator(
        "scheduled_start", "scheduled_end", "checkin_opens_at", "checkin_closes_at"
    )
    @classmethod
    def timezone_aware(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None


class SessionUpdate(CleanModel):
    name: Name | None = None
    session_type: SessionType | None = None
    description: str | None = Field(default=None, max_length=5000)
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    checkin_opens_at: datetime | None = None
    checkin_closes_at: datetime | None = None
    status: SessionStatus | None = None
    venue_latitude: float | None = Field(default=None, ge=-90, le=90)
    venue_longitude: float | None = Field(default=None, ge=-180, le=180)
    venue_name: str | None = Field(default=None, max_length=255)
    geofence_radius_meters: float | None = Field(default=None, gt=0, le=10_000)
    require_liveness_check: bool | None = None
    require_face_match: bool | None = None
    risk_threshold: float | None = Field(default=None, ge=0, le=1)

    @field_validator(
        "scheduled_start", "scheduled_end", "checkin_opens_at", "checkin_closes_at"
    )
    @classmethod
    def timezone_aware(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @field_validator(
        "name",
        "session_type",
        "scheduled_start",
        "scheduled_end",
        "checkin_opens_at",
        "checkin_closes_at",
        "status",
        "require_liveness_check",
        "require_face_match",
    )
    @classmethod
    def required_fields_cannot_be_null(cls, value):
        if value is None:
            raise ValueError("field cannot be null")
        return value


def _session_or_404(db: DbSession, session_id: UUID | str) -> Session:
    model = db.get(Session, str(session_id))
    if model is None:
        raise ApiError(404, "Session not found", "SESSION_NOT_FOUND")
    return model


def _validate_window(values: dict, *, require_future: bool) -> None:
    start = values["scheduled_start"]
    end = values["scheduled_end"]
    opens = values["checkin_opens_at"]
    closes = values["checkin_closes_at"]
    if require_future and start <= utcnow():
        raise ApiError(400, "Session must start in the future", "INVALID_SESSION_WINDOW")
    if end <= start:
        raise ApiError(400, "scheduled_end must be after scheduled_start", "INVALID_SESSION_WINDOW")
    if closes <= opens:
        raise ApiError(400, "checkin_closes_at must be after checkin_opens_at", "INVALID_CHECKIN_WINDOW")
    if opens > end or closes < start:
        raise ApiError(400, "Check-in window must overlap the session", "INVALID_CHECKIN_WINDOW")


def _assert_session_owner(db: DbSession, actor, model: Session) -> None:
    if actor.role == "admin":
        return
    if actor.role != "instructor" or model.instructor_id != actor.id:
        raise ApiError(403, "Only the session owner may modify it", "FORBIDDEN")
    ensure_course_access(db, actor, model.course_id)


@router.get("/active")
def active_sessions(db: Db):
    now = utcnow()
    models = db.scalars(
        select(Session)
        .join(Course, Course.id == Session.course_id)
        .where(
            Session.status == "active",
            Session.checkin_opens_at <= now,
            Session.checkin_closes_at >= now,
            Course.is_active.is_(True),
        )
        .order_by(Session.checkin_closes_at, Session.id)
        .limit(100)
    ).all()
    return [session_data(model, db) for model in models]


@router.get("/my-sessions")
def my_sessions(
    db: Db,
    actor=Depends(current_user),
    session_status: SessionStatus | None = Query(default=None, alias="status"),
    upcoming: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
):
    stmt = select(Session)
    if actor.role == "admin":
        pass
    elif actor.role == "student":
        stmt = stmt.where(
            Session.course_id.in_(
                select(Enrollment.course_id).where(
                    Enrollment.student_id == actor.id, Enrollment.is_active.is_(True)
                )
            )
        )
    else:
        stmt = stmt.where(
            or_(
                Session.instructor_id == actor.id,
                Session.course_id.in_(
                    select(CourseStaff.course_id).where(CourseStaff.user_id == actor.id)
                ),
            )
        )
    if session_status:
        stmt = stmt.where(Session.status == session_status)
    if upcoming:
        stmt = stmt.where(Session.scheduled_start >= utcnow())
    models = db.scalars(stmt.order_by(Session.scheduled_start).limit(limit)).all()
    return [session_data(model, db) for model in models]


@router.get("/")
def list_sessions(
    db: Db,
    actor=Depends(require_roles("admin", "instructor", "ta")),
    session_status: SessionStatus | None = Query(default=None, alias="status"),
    course_id: UUID | None = None,
    instructor_id: UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1_000_000),
):
    try:
        if start_date is not None:
            start_date = _utc(start_date)
        if end_date is not None:
            end_date = _utc(end_date)
    except ValueError as exc:
        raise ApiError(400, str(exc), "INVALID_DATE_RANGE") from exc
    if start_date and end_date and end_date < start_date:
        raise ApiError(400, "end_date must not precede start_date", "INVALID_DATE_RANGE")
    filters = []
    if actor.role != "admin":
        ids = visible_course_ids(db, actor)
        filters.append(Session.course_id.in_(ids))
    if session_status:
        filters.append(Session.status == session_status)
    if course_id:
        filters.append(Session.course_id == str(course_id))
    if instructor_id:
        filters.append(Session.instructor_id == str(instructor_id))
    if start_date:
        filters.append(Session.scheduled_start >= start_date)
    if end_date:
        filters.append(Session.scheduled_start <= end_date)
    stmt = select(Session).where(*filters)
    total = db.scalar(select(func.count()).select_from(Session).where(*filters)) or 0
    models = db.scalars(
        stmt.order_by(Session.scheduled_start.desc()).limit(limit).offset(offset)
    ).all()
    return page([session_data(model, db) for model in models], total, limit, offset)


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_session(
    body: SessionCreate,
    request: Request,
    db: Db,
    actor=Depends(require_roles("admin", "instructor")),
):
    course = ensure_course_access(db, actor, str(body.course_id))
    if not course.is_active:
        raise ApiError(400, "Cannot create a session for an inactive course", "INACTIVE_COURSE")
    values = body.model_dump()
    values["course_id"] = str(body.course_id)
    values["instructor_id"] = actor.id
    values["status"] = "scheduled"
    values["checkin_opens_at"] = body.checkin_opens_at or body.scheduled_start - timedelta(minutes=15)
    values["checkin_closes_at"] = body.checkin_closes_at or body.scheduled_start + timedelta(minutes=30)
    for field in ("venue_latitude", "venue_longitude", "venue_name", "geofence_radius_meters", "risk_threshold"):
        if values[field] is None:
            values[field] = getattr(course, field)
    if body.require_face_match is None:
        values["require_face_match"] = course.require_face_recognition
    _validate_window(values, require_future=True)
    model = Session(**values)
    db.add(model)
    db.flush()
    audit_event(
        db,
        "session_created",
        user_id=actor.id,
        resource_type="session",
        resource_id=model.id,
        details={"course_id": course.id},
        request=request,
    )
    db.commit()
    db.refresh(model)
    return session_data(model, db)


@router.get("/{session_id}")
def get_session(session_id: UUID, db: Db, actor=Depends(current_user)):
    model = _session_or_404(db, session_id)
    # Students can preview the same session metadata exposed by the public active
    # list. Enrollment is enforced when they submit a check-in.
    if actor.role != "student":
        ensure_course_access(db, actor, model.course_id)
    return session_data(model, db)


@router.patch("/{session_id}")
@router.put("/{session_id}")
def update_session(
    session_id: UUID,
    body: SessionUpdate,
    request: Request,
    db: Db,
    actor=Depends(current_user),
):
    model = _session_or_404(db, session_id)
    _assert_session_owner(db, actor, model)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(400, "At least one field is required", "EMPTY_UPDATE")

    requested_status = changes.pop("status", None)
    if requested_status and requested_status != model.status:
        if requested_status not in ALLOWED_TRANSITIONS.get(model.status, set()):
            raise ApiError(
                400,
                f"Cannot transition session from {model.status} to {requested_status}",
                "INVALID_STATUS_TRANSITION",
            )
        model.status = requested_status
        if requested_status == "active":
            model.actual_start = utcnow()
        elif requested_status in {"closed", "cancelled"}:
            model.actual_end = utcnow()

    temporal_fields = {
        "scheduled_start",
        "scheduled_end",
        "checkin_opens_at",
        "checkin_closes_at",
    }
    if temporal_fields.intersection(changes) and model.status != "scheduled":
        raise ApiError(400, "Active or finished session times cannot be changed", "SESSION_ALREADY_STARTED")
    candidate = {
        name: changes.get(name, getattr(model, name))
        for name in temporal_fields
    }
    _validate_window(candidate, require_future=False)
    for key, value in changes.items():
        setattr(model, key, value)
    changed_fields = sorted(set(changes) | ({"status"} if requested_status else set()))
    audit_event(
        db,
        "session_updated",
        user_id=actor.id,
        resource_type="session",
        resource_id=model.id,
        details={"fields": changed_fields},
        request=request,
    )
    db.commit()
    db.refresh(model)
    return session_data(model, db)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: UUID,
    request: Request,
    db: Db,
    actor=Depends(current_user),
):
    model = _session_or_404(db, session_id)
    _assert_session_owner(db, actor, model)
    if model.status != "scheduled":
        raise ApiError(400, "Only scheduled sessions may be deleted", "SESSION_ALREADY_STARTED")
    resource_id = model.id
    db.delete(model)
    audit_event(
        db,
        "session_deleted",
        user_id=actor.id,
        resource_type="session",
        resource_id=resource_id,
        request=request,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
