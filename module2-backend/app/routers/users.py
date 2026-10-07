from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field
from sqlalchemy import func, select

from app.database import utcnow
from app.dependencies import current_user, get_db, require_roles, visible_course_ids
from app.errors import ApiError
from app.models import Enrollment, User
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
    user.face_embedding_hash = result['face_template_hash']
    user.face_enrolled = True
    audit_event(db, 'face_enrolled', user.id, 'user', user.id, request=request)
    db.commit()
    return {'success': True, 'message': 'Face enrolled successfully', 'face_enrolled': True,
            'quality_score': result['quality_score']}


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
    target.scheduled_deletion_at = utcnow() + timedelta(days=request.app.state.settings.retention_days)
    revoke_user(db, target.id)
    audit_event(db, 'user_deletion_scheduled', user.id, 'user', target.id, request=request)
    db.commit()
    return Response(status_code=204)
