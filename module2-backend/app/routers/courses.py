"""Course management and course staff assignment endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.dependencies import bearer, current_user, ensure_course_access, get_db, require_roles
from app.errors import ApiError
from app.models import Course, CourseStaff, User
from app.schemas import CleanModel, Name
from app.serializers import course_data, page, user_data
from app.services.audit import audit_event


router = APIRouter(prefix="/courses", tags=["courses"])
Db = Annotated[DbSession, Depends(get_db)]


class CourseCreate(CleanModel):
    code: str = Field(min_length=1, max_length=20)
    name: Name
    description: str | None = Field(default=None, max_length=5000)
    semester: str = Field(min_length=1, max_length=50)
    venue_latitude: float | None = Field(default=None, ge=-90, le=90)
    venue_longitude: float | None = Field(default=None, ge=-180, le=180)
    venue_name: str | None = Field(default=None, max_length=255)
    geofence_radius_meters: float = Field(default=100.0, gt=0, le=10_000)
    require_face_recognition: bool = False
    require_device_binding: bool = True
    risk_threshold: float = Field(default=0.5, ge=0, le=1)
    instructor_id: UUID | None = None

    @field_validator("code")
    @classmethod
    def clean_code(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("course code cannot be blank")
        return value

    @field_validator("semester")
    @classmethod
    def clean_semester(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("semester cannot be blank")
        return value

    @field_validator("venue_name", "description")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class CourseUpdate(CleanModel):
    code: str | None = Field(default=None, min_length=1, max_length=20)
    name: Name | None = None
    description: str | None = Field(default=None, max_length=5000)
    semester: str | None = Field(default=None, min_length=1, max_length=50)
    is_active: bool | None = None
    venue_latitude: float | None = Field(default=None, ge=-90, le=90)
    venue_longitude: float | None = Field(default=None, ge=-180, le=180)
    venue_name: str | None = Field(default=None, max_length=255)
    geofence_radius_meters: float | None = Field(default=None, gt=0, le=10_000)
    require_face_recognition: bool | None = None
    require_device_binding: bool | None = None
    risk_threshold: float | None = Field(default=None, ge=0, le=1)

    @field_validator("code")
    @classmethod
    def clean_code(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("course code cannot be null")
        value = value.strip().upper()
        if not value:
            raise ValueError("course code cannot be blank")
        return value

    @field_validator("semester")
    @classmethod
    def clean_semester(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("semester cannot be null")
        value = value.strip()
        if not value:
            raise ValueError("semester cannot be blank")
        return value

    @field_validator(
        "name",
        "semester",
        "is_active",
        "geofence_radius_meters",
        "require_face_recognition",
        "require_device_binding",
        "risk_threshold",
    )
    @classmethod
    def required_fields_cannot_be_null(cls, value):
        if value is None:
            raise ValueError("field cannot be null")
        return value


class CourseStaffUpdate(CleanModel):
    user_ids: list[UUID] = Field(default_factory=list, max_length=100)


def _course_or_404(db: DbSession, course_id: UUID | str) -> Course:
    course = db.get(Course, str(course_id))
    if course is None:
        raise ApiError(404, "Course not found", "COURSE_NOT_FOUND")
    return course


def _commit_unique(db: DbSession, duplicate_detail: str, duplicate_code: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(400, duplicate_detail, duplicate_code) from exc


def _full_course(model: Course, db: DbSession) -> dict:
    data = course_data(model, db)
    instructor = db.scalar(
        select(User)
        .join(CourseStaff, CourseStaff.user_id == User.id)
        .where(CourseStaff.course_id == model.id, User.role == "instructor")
        .order_by(User.full_name)
        .limit(1)
    )
    data["instructor_id"] = instructor.id if instructor else None
    data["instructor_name"] = instructor.full_name if instructor else None
    return data


def _public_course(model: Course, db: DbSession) -> dict:
    data = _full_course(model, db)
    for field in ("risk_threshold", "require_face_recognition", "require_device_binding"):
        data.pop(field, None)
    return data


def optional_current_user(request: Request, credentials=Depends(bearer), db=Depends(get_db)):
    """Authenticate supplied credentials while allowing a request with no token."""
    if credentials is None:
        return None
    return current_user(request=request, credentials=credentials, db=db)


@router.get("/")
def list_courses(
    db: Db,
    actor=Depends(optional_current_user),
    is_active: bool | None = True,
    semester: str | None = Query(default=None, max_length=50),
    instructor_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1_000_000),
):
    stmt = select(Course)
    count_stmt = select(func.count()).select_from(Course)
    filters = []
    if is_active is not None:
        filters.append(Course.is_active == is_active)
    if semester:
        filters.append(Course.semester == semester.strip())
    if instructor_id:
        filters.append(
            Course.id.in_(
                select(CourseStaff.course_id).where(CourseStaff.user_id == str(instructor_id))
            )
        )
    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)
    total = db.scalar(count_stmt) or 0
    courses = db.scalars(stmt.order_by(Course.code).limit(limit).offset(offset)).all()
    serializer = _full_course if actor is not None else _public_course
    return page([serializer(course, db) for course in courses], total, limit, offset)


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_course(
    body: CourseCreate,
    request: Request,
    db: Db,
    actor=Depends(require_roles("admin")),
):
    values = body.model_dump(exclude={"instructor_id"})
    course = Course(**values)
    db.add(course)
    db.flush()
    if body.instructor_id is not None:
        staff = db.get(User, str(body.instructor_id))
        if staff is None:
            raise ApiError(404, "Instructor not found", "USER_NOT_FOUND")
        if staff.role not in {"instructor", "ta"}:
            raise ApiError(400, "Course staff must be an instructor or TA", "INVALID_COURSE_STAFF")
        db.add(CourseStaff(course_id=course.id, user_id=staff.id))
    audit_event(
        db,
        "course_created",
        user_id=actor.id,
        resource_type="course",
        resource_id=course.id,
        details={"code": course.code},
        request=request,
    )
    _commit_unique(db, "Course code already exists", "COURSE_EXISTS")
    db.refresh(course)
    return _full_course(course, db)


@router.put("/{course_id}/staff")
def replace_course_staff(
    course_id: UUID,
    body: CourseStaffUpdate,
    request: Request,
    db: Db,
    actor=Depends(require_roles("admin")),
):
    course = _course_or_404(db, course_id)
    ids = list(dict.fromkeys(str(item) for item in body.user_ids))
    users = db.scalars(select(User).where(User.id.in_(ids))).all() if ids else []
    found = {user.id for user in users}
    missing = [item for item in ids if item not in found]
    if missing:
        raise ApiError(404, "One or more users were not found", "USER_NOT_FOUND")
    if any(user.role not in {"instructor", "ta"} for user in users):
        raise ApiError(400, "Course staff must be instructors or TAs", "INVALID_COURSE_STAFF")

    existing = db.scalars(select(CourseStaff).where(CourseStaff.course_id == course.id)).all()
    existing_ids = {assignment.user_id for assignment in existing}
    requested_ids = set(ids)
    for assignment in existing:
        if assignment.user_id not in requested_ids:
            db.delete(assignment)
    for user_id in ids:
        if user_id not in existing_ids:
            db.add(CourseStaff(course_id=course.id, user_id=user_id))
    audit_event(
        db,
        "course_staff_updated",
        user_id=actor.id,
        resource_type="course",
        resource_id=course.id,
        details={"staff_count": len(ids)},
        request=request,
    )
    db.commit()
    ordered = {user.id: user for user in users}
    return {"course_id": course.id, "staff": [user_data(ordered[item]) for item in ids]}


@router.get("/{course_id}")
def get_course(course_id: UUID, db: Db, actor=Depends(optional_current_user)):
    course = _course_or_404(db, course_id)
    return _full_course(course, db) if actor is not None else _public_course(course, db)


@router.put("/{course_id}")
@router.patch("/{course_id}")
def update_course(
    course_id: UUID,
    body: CourseUpdate,
    request: Request,
    db: Db,
    actor=Depends(current_user),
):
    if actor.role not in {"admin", "instructor"}:
        raise ApiError(403, "Insufficient permissions", "FORBIDDEN")
    course = ensure_course_access(db, actor, str(course_id))
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(400, "At least one field is required", "EMPTY_UPDATE")
    if actor.role != "admin" and "is_active" in changes:
        raise ApiError(403, "Only administrators may change course activation", "FORBIDDEN")
    for key, value in changes.items():
        setattr(course, key, value)
    audit_event(
        db,
        "course_updated",
        user_id=actor.id,
        resource_type="course",
        resource_id=course.id,
        details={"fields": sorted(changes)},
        request=request,
    )
    _commit_unique(db, "Course code already exists", "COURSE_EXISTS")
    db.refresh(course)
    return _full_course(course, db)


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_course(
    course_id: UUID,
    request: Request,
    db: Db,
    actor=Depends(require_roles("admin")),
):
    course = _course_or_404(db, course_id)
    course.is_active = False
    audit_event(
        db,
        "course_deleted",
        user_id=actor.id,
        resource_type="course",
        resource_id=course.id,
        request=request,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
