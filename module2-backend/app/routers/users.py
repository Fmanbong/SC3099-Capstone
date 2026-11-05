import html
from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator
from sqlalchemy import func, select

from app.database import utcnow
from app.dependencies import current_user, get_db, require_roles, visible_course_ids
from app.errors import ApiError
from app.models import Course, Enrollment, User
from app.schemas import CleanModel, Name
from app.security import revoke_user
from app.serializers import page, user_data
from app.services.audit import audit_event

router = APIRouter(prefix='/users', tags=['Users'])


class ProfileUpdate(CleanModel):
    full_name: Name | None = None
    camera_consent: bool | None = None
    geolocation_consent: bool | None = None


class UserUpdate(CleanModel):
    role: Literal['student', 'ta', 'instructor', 'admin'] | None = None
    is_active: bool | None = None


class FaceEnroll(CleanModel):
    image: str = Field(min_length=1, max_length=7 * 1024 * 1024)


class FaceEnrollmentDecision(CleanModel):
    review_notes: str | None = Field(None, max_length=2000)

    @field_validator('review_notes')
    @classmethod
    def clean_notes(cls, value: str | None) -> str | None:
        value = (value or '').strip()
        return html.escape(value, quote=True) if value else None


class FaceEnrollmentRejection(FaceEnrollmentDecision):
    review_notes: str = Field(min_length=1, max_length=2000)

    @field_validator('review_notes')
    @classmethod
    def clean_notes(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('Give the student a reason for the rejection')
        return html.escape(value, quote=True)


def _clear_pending_face(user):
    user.pending_face_hash = None
    user.face_enrollment_status = 'approved' if user.face_enrolled else 'none'


def _reviewable_student(db, actor, user_id):
    """Students staff may review: anyone for admins, otherwise only students in the actor's courses."""
    target = db.get(User, str(user_id))
    if target is None or target.role != 'student':
        raise ApiError(404, 'Student not found', 'USER_NOT_FOUND')
    if actor.role != 'admin':
        shared = db.scalar(select(Enrollment.id).where(Enrollment.student_id == target.id,
                           Enrollment.is_active.is_(True), Enrollment.course_id.in_(visible_course_ids(db, actor))))
        if not shared:
            raise ApiError(403, 'You do not teach this student', 'FORBIDDEN')
    return target


@router.get('/me')
def me(user=Depends(current_user)):
    return user_data(user)


@router.put('/me')
@router.patch('/me')
def update_me(payload: ProfileUpdate, request: Request, user=Depends(current_user), db=Depends(get_db)):
    changes = payload.model_dump(exclude_unset=True)
    if any(value is None for value in changes.values()):
        raise ApiError(422, 'Profile fields cannot be null', 'VALIDATION_ERROR')
    for key, value in changes.items():
        setattr(user, key, value)
    if changes.get('camera_consent') is False:
        user.face_embedding_hash = None
        user.face_enrolled = False
        _clear_pending_face(user)
    audit_event(db, 'consent_updated' if any('consent' in key for key in changes) else 'user_updated',
                user.id, 'user', user.id, details={'fields': list(changes)}, request=request)
    db.commit()
    return user_data(user)


@router.post('/me/face/enroll')
def enroll_face(payload: FaceEnroll, request: Request, user=Depends(current_user), db=Depends(get_db)):
    if not user.camera_consent:
        raise ApiError(400, 'Camera consent is required', 'CAMERA_CONSENT_REQUIRED')
    result = request.app.state.face_client.enroll(user.id, payload.image, user.camera_consent)
    if not result.get('enrollment_successful') or not result.get('face_template_hash'):
        raise ApiError(400, 'Face enrollment failed', 'FACE_ENROLLMENT_FAILED')
    if request.app.state.settings.requires_face_enrollment_approval and user.role == 'student':
        # Held until staff confirm in person that this is the student; any
        # previously approved face keeps working for check-in meanwhile.
        user.pending_face_hash = result['face_template_hash']
        user.face_enrollment_status = 'pending'
        user.face_enrollment_requested_at = utcnow()
        user.face_enrollment_reviewed_by_id = user.face_enrollment_reviewed_at = None
        user.face_enrollment_review_notes = None
        audit_event(db, 'face_enrollment_requested', user.id, 'user', user.id, request=request)
        db.commit()
        return JSONResponse(status_code=202, content={
            'success': True, 'approval_pending': True, 'face_enrolled': user.face_enrolled,
            'message': 'Face enrollment submitted. An instructor must approve it before it is used for check-in.',
            'quality_score': result['quality_score']})
    user.face_embedding_hash = result['face_template_hash']
    user.face_enrolled = True
    user.face_enrollment_status = 'approved'
    user.pending_face_hash = None
    audit_event(db, 'face_enrolled', user.id, 'user', user.id, request=request)
    db.commit()
    return {'success': True, 'message': 'Face enrolled successfully', 'face_enrolled': True,
            'quality_score': result['quality_score']}


@router.get('/face-enrollments/pending')
def pending_face_enrollments(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                             actor=Depends(require_roles('admin', 'instructor', 'ta')), db=Depends(get_db)):
    query = select(User).where(User.role == 'student', User.face_enrollment_status == 'pending')
    course_ids = None
    if actor.role != 'admin':
        course_ids = visible_course_ids(db, actor)
        query = query.where(User.id.in_(select(Enrollment.student_id).where(
            Enrollment.is_active.is_(True), Enrollment.course_id.in_(course_ids))))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    students = list(db.scalars(query.order_by(User.face_enrollment_requested_at, User.id).limit(limit).offset(offset)))
    codes = {}
    if students:
        rows = select(Enrollment.student_id, Course.code).join(Course, Course.id == Enrollment.course_id).where(
            Enrollment.student_id.in_([s.id for s in students]), Enrollment.is_active.is_(True))
        if course_ids is not None:
            rows = rows.where(Enrollment.course_id.in_(course_ids))
        for student_id, code in db.execute(rows):
            codes.setdefault(student_id, []).append(code)
    items = [{'id': s.id, 'email': s.email, 'full_name': s.full_name,
              'requested_at': s.face_enrollment_requested_at, 'replaces_existing_face': s.face_enrolled,
              'courses': sorted(codes.get(s.id, []))} for s in students]
    return page(items, total, limit, offset)


@router.post('/{user_id}/face-enrollment/approve')
def approve_face_enrollment(user_id: UUID, payload: FaceEnrollmentDecision, request: Request,
                            actor=Depends(require_roles('admin', 'instructor', 'ta')), db=Depends(get_db)):
    target = _reviewable_student(db, actor, user_id)
    if target.face_enrollment_status != 'pending' or not target.pending_face_hash:
        raise ApiError(400, 'This student has no face enrollment awaiting approval', 'NO_PENDING_ENROLLMENT')
    target.face_embedding_hash = target.pending_face_hash
    target.face_enrolled = True
    target.pending_face_hash = None
    target.face_enrollment_status = 'approved'
    target.face_enrollment_reviewed_by_id = actor.id
    target.face_enrollment_reviewed_at = utcnow()
    target.face_enrollment_review_notes = payload.review_notes
    audit_event(db, 'face_enrollment_approved', actor.id, 'user', target.id, request=request)
    audit_event(db, 'face_enrolled', target.id, 'user', target.id, details={'approved_by': actor.id}, request=request)
    db.commit()
    return user_data(target)


@router.post('/{user_id}/face-enrollment/reject')
def reject_face_enrollment(user_id: UUID, payload: FaceEnrollmentRejection, request: Request,
                           actor=Depends(require_roles('admin', 'instructor', 'ta')), db=Depends(get_db)):
    target = _reviewable_student(db, actor, user_id)
    if target.face_enrollment_status != 'pending' or not target.pending_face_hash:
        raise ApiError(400, 'This student has no face enrollment awaiting approval', 'NO_PENDING_ENROLLMENT')
    target.pending_face_hash = None
    target.face_enrollment_status = 'rejected'
    target.face_enrollment_reviewed_by_id = actor.id
    target.face_enrollment_reviewed_at = utcnow()
    target.face_enrollment_review_notes = payload.review_notes
    audit_event(db, 'face_enrollment_rejected', actor.id, 'user', target.id, request=request, severity='warning')
    db.commit()
    return user_data(target)


@router.get('/')
def list_users(role: Literal['student', 'ta', 'instructor', 'admin'] | None = None,
               is_active: bool | None = None, search: str | None = Query(None, max_length=255),
               limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
               user=Depends(require_roles('admin')), db=Depends(get_db)):
    query = select(User)
    if role is not None:
        query = query.where(User.role == role)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    if search:
        query = query.where(User.email.icontains(search, autoescape=True) | User.full_name.icontains(search, autoescape=True))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    return page([user_data(row) for row in db.scalars(query.order_by(User.created_at, User.id).limit(limit).offset(offset))], total, limit, offset)


@router.get('/{user_id}')
def get_user(user_id: UUID, user=Depends(current_user), db=Depends(get_db)):
    target = db.get(User, str(user_id))
    if target is None:
        raise ApiError(404, 'User not found', 'USER_NOT_FOUND')
    if target.id != user.id and user.role != 'admin':
        shared = db.scalar(select(Enrollment.id).where(Enrollment.student_id == target.id,
                          Enrollment.is_active.is_(True), Enrollment.course_id.in_(visible_course_ids(db, user))))
        if user.role not in {'instructor', 'ta'} or not shared:
            raise ApiError(403, 'You do not have access to this user', 'FORBIDDEN')
    return user_data(target)


@router.patch('/{user_id}')
def update_user(user_id: UUID, payload: UserUpdate, request: Request,
                user=Depends(require_roles('admin')), db=Depends(get_db)):
    target = db.get(User, str(user_id))
    if target is None:
        raise ApiError(404, 'User not found', 'USER_NOT_FOUND')
    changes = payload.model_dump(exclude_unset=True)
    if any(value is None for value in changes.values()):
        raise ApiError(422, 'User fields cannot be null', 'VALIDATION_ERROR')
    for key, value in changes.items():
        setattr(target, key, value)
    revoke_user(db, target.id)
    audit_event(db, 'user_updated', user.id, 'user', target.id, details=changes, request=request)
    db.commit()
    return user_data(target)


@router.delete('/{user_id}', status_code=204)
def delete_user(user_id: UUID, request: Request, user=Depends(current_user), db=Depends(get_db)):
    if str(user_id) != user.id and user.role != 'admin':
        raise ApiError(403, 'Only the account owner or an admin can delete it', 'FORBIDDEN')
    target = db.get(User, str(user_id))
    if target is None:
        raise ApiError(404, 'User not found', 'USER_NOT_FOUND')
    target.is_active = False
    target.face_embedding_hash = None
    target.face_enrolled = False
    _clear_pending_face(target)
    target.scheduled_deletion_at = utcnow() + timedelta(days=request.app.state.settings.retention_days)
    revoke_user(db, target.id)
    audit_event(db, 'user_deletion_scheduled', user.id, 'user', target.id, request=request)
    db.commit()
    return Response(status_code=204)
