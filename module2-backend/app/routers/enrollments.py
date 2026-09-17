"""Student enrollment management endpoints."""

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.database import utcnow
from app.dependencies import current_user, ensure_course_access, get_db, require_roles
from app.errors import ApiError
from app.models import Course, CourseStaff, Enrollment, EnrollmentPeriod, User
from app.schemas import CleanModel
from app.security import hash_password
from app.serializers import enrollment_data
from app.services.audit import audit_event


router = APIRouter(prefix="/enrollments", tags=["enrollments"])
Db = Annotated[DbSession, Depends(get_db)]


class EnrollmentCreate(CleanModel):
    student_id: UUID
    course_id: UUID


class BulkEnrollment(CleanModel):
    course_id: UUID
    student_emails: list[EmailStr] = Field(min_length=1, max_length=500)
    create_accounts: bool = False


def _enrollment_or_404(db: DbSession, enrollment_id: UUID | str) -> Enrollment:
    model = db.get(Enrollment, str(enrollment_id))
    if model is None:
        raise ApiError(404, "Enrollment not found", "ENROLLMENT_NOT_FOUND")
    return model


def _student_or_404(db: DbSession, student_id: UUID | str) -> User:
    student = db.get(User, str(student_id))
    if student is None:
        raise ApiError(404, "Student not found", "STUDENT_NOT_FOUND")
    if student.role != "student":
        raise ApiError(400, "Only student accounts can be enrolled", "INVALID_STUDENT")
    if not student.is_active:
        raise ApiError(400, "Student account is inactive", "INACTIVE_STUDENT")
    return student


def _enroll(db: DbSession, student: User, course: Course) -> tuple[Enrollment, str]:
    enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.student_id == student.id, Enrollment.course_id == course.id
        ).with_for_update()
    )
    if enrollment is not None and enrollment.is_active:
        return enrollment, "already_enrolled"
    if enrollment is None:
        enrollment = Enrollment(student_id=student.id, course_id=course.id)
        db.add(enrollment)
    else:
        if enrollment.dropped_at is not None:
            db.add(EnrollmentPeriod(enrollment_id=enrollment.id,
                                    started_at=enrollment.enrolled_at, ended_at=enrollment.dropped_at))
        enrollment.is_active = True
        enrollment.enrolled_at = utcnow()
        enrollment.dropped_at = None
    db.flush()
    return enrollment, "enrolled"


def _with_instructor(data: dict, db: DbSession, course_id: str) -> dict:
    instructor = db.scalar(
        select(User)
        .join(CourseStaff, CourseStaff.user_id == User.id)
        .where(CourseStaff.course_id == course_id, User.role == "instructor")
        .order_by(User.full_name)
        .limit(1)
    )
    data["instructor_name"] = instructor.full_name if instructor else None
    return data


@router.get("/my-enrollments")
def my_enrollments(db: Db, actor=Depends(require_roles("student"))):
    models = db.scalars(
        select(Enrollment)
        .join(Course, Course.id == Enrollment.course_id)
        .where(
            Enrollment.student_id == actor.id,
            Enrollment.is_active.is_(True),
            Course.is_active.is_(True),
        )
        .order_by(Course.code)
    ).all()
    return [
        _with_instructor(enrollment_data(model, db), db, model.course_id)
        for model in models
    ]


@router.get("/course/{course_id}")
def course_enrollments(
    course_id: UUID,
    db: Db,
    actor=Depends(require_roles("admin", "instructor", "ta")),
    is_active: bool | None = True,
    search: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=1_000_000),
):
    course = ensure_course_access(db, actor, str(course_id))
    filters = [Enrollment.course_id == course.id]
    if is_active is not None:
        filters.append(Enrollment.is_active == is_active)
    if search:
        term = f"%{search.strip()}%"
        filters.append(or_(User.full_name.ilike(term), User.email.ilike(term)))
    base = select(Enrollment).join(User, User.id == Enrollment.student_id).where(*filters)
    total = db.scalar(
        select(func.count()).select_from(Enrollment).join(
            User, User.id == Enrollment.student_id
        ).where(*filters)
    ) or 0
    models = db.scalars(base.order_by(User.full_name).limit(limit).offset(offset)).all()
    students = []
    for model in models:
        student = db.get(User, model.student_id)
        data = enrollment_data(model, db)
        data["face_enrolled"] = student.face_enrolled
        students.append(data)
    return {
        "course_id": course.id,
        "course_code": course.code,
        "total_enrolled": total,
        "students": students,
        "limit": limit,
        "offset": offset,
    }


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_enrollment(
    body: EnrollmentCreate,
    request: Request,
    db: Db,
    actor=Depends(require_roles("admin", "instructor")),
):
    course = ensure_course_access(db, actor, str(body.course_id))
    if not course.is_active:
        raise ApiError(400, "Cannot enroll into an inactive course", "INACTIVE_COURSE")
    student = _student_or_404(db, body.student_id)
    enrollment, result = _enroll(db, student, course)
    if result == "already_enrolled":
        raise ApiError(400, "Student is already enrolled", "ALREADY_ENROLLED")
    audit_event(
        db,
        "student_enrolled",
        user_id=actor.id,
        resource_type="enrollment",
        resource_id=enrollment.id,
        details={"course_id": course.id, "student_id": student.id},
        request=request,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(400, "Student is already enrolled", "ALREADY_ENROLLED") from exc
    db.refresh(enrollment)
    return enrollment_data(enrollment, db)


@router.post("/bulk")
def bulk_enroll(
    body: BulkEnrollment,
    request: Request,
    db: Db,
    actor=Depends(require_roles("admin", "instructor")),
):
    course = ensure_course_access(db, actor, str(body.course_id))
    if not course.is_active:
        raise ApiError(400, "Cannot enroll into an inactive course", "INACTIVE_COURSE")
    emails = [str(email).lower() for email in body.student_emails]
    if len(set(emails)) != len(emails):
        raise ApiError(400, "Student email list contains duplicates", "DUPLICATE_EMAIL")

    existing = db.scalars(select(User).where(User.email.in_(emails))).all()
    by_email = {student.email.lower(): student for student in existing}
    details = []
    counts = {"enrolled": 0, "already_enrolled": 0, "not_found": 0, "created": 0}
    settings = db.info.get("settings")
    for email in emails:
        student = by_email.get(email)
        was_created = False
        if student is None and body.create_accounts:
            student = User(
                email=email,
                full_name=email.split("@", 1)[0].replace(".", " ").title()[:255],
                role="student",
                hashed_password=hash_password(
                    secrets.token_urlsafe(32), getattr(settings, "bcrypt_rounds", 10)
                ),
            )
            db.add(student)
            db.flush()
            was_created = True
            counts["created"] += 1
        if student is None:
            result = "not_found"
        elif student.role != "student" or not student.is_active:
            result = "not_found"
        else:
            _, result = _enroll(db, student, course)
            if was_created:
                result = "created"
        counts[result] += 1 if result in counts and result != "created" else 0
        details.append({"email": email, "status": result})

    audit_event(
        db,
        "students_bulk_enrolled",
        user_id=actor.id,
        resource_type="course",
        resource_id=course.id,
        details={key: counts[key] for key in counts},
        request=request,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(400, "Bulk enrollment conflicts with existing data", "ENROLLMENT_CONFLICT") from exc
    return {**counts, "details": details}


@router.delete("/{enrollment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_enrollment(
    enrollment_id: UUID,
    request: Request,
    db: Db,
    actor=Depends(require_roles("admin", "instructor")),
):
    model = _enrollment_or_404(db, enrollment_id)
    ensure_course_access(db, actor, model.course_id)
    if model.is_active:
        model.is_active = False
        model.dropped_at = utcnow()
        audit_event(
            db,
            "student_unenrolled",
            user_id=actor.id,
            resource_type="enrollment",
            resource_id=model.id,
            details={"course_id": model.course_id, "student_id": model.student_id},
            request=request,
        )
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
