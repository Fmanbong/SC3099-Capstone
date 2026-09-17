"""Administrative operations also used to provision automated test scenarios."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import utcnow
from app.dependencies import get_db, require_roles
from app.errors import ApiError
from app.models import Session, User
from app.routers.auth import RegisterRequest
from app.routers.enrollments import EnrollmentCreate, create_enrollment
from app.schemas import CleanModel
from app.security import hash_password, revoke_user
from app.serializers import session_data, user_data
from app.services.audit import audit_event

router = APIRouter(prefix='/admin', tags=['Administration'])


class BulkUsers(CleanModel):
    users: list[RegisterRequest] = Field(min_length=1, max_length=500)


class StatusUpdate(CleanModel):
    status: Literal['scheduled', 'active', 'closed', 'cancelled']


def change_active(user_id, enabled, request, actor, db):
    target = db.get(User, str(user_id))
    if target is None:
        raise ApiError(404, 'User not found', 'USER_NOT_FOUND')
    target.is_active = enabled
    revoke_user(db, target.id)
    if enabled:
        target.scheduled_deletion_at = None
    audit_event(db, 'user_activated' if enabled else 'user_deactivated', actor.id, 'user', target.id, request=request)
    db.commit()
    return {**user_data(target), 'message': 'User activated successfully' if enabled else 'User deactivated successfully'}


@router.patch('/users/{user_id}/deactivate')
def deactivate(user_id: UUID, request: Request, actor=Depends(require_roles('admin')), db=Depends(get_db)):
    return change_active(user_id, False, request, actor, db)


@router.patch('/users/{user_id}/activate')
def activate(user_id: UUID, request: Request, actor=Depends(require_roles('admin')), db=Depends(get_db)):
    return change_active(user_id, True, request, actor, db)


@router.post('/users/bulk', status_code=201)
def bulk_users(payload: BulkUsers, request: Request, actor=Depends(require_roles('admin')), db=Depends(get_db)):
    created, errors = [], []
    for index, entry in enumerate(payload.users):
        email = str(entry.email).lower()
        if db.scalar(select(User.id).where(User.email == email)):
            errors.append({'index': index, 'code': 'EMAIL_EXISTS', 'detail': 'Email already registered'})
            continue
        try:
            with db.begin_nested():
                target = User(email=email, full_name=entry.full_name, role=entry.role,
                              hashed_password=hash_password(entry.password, request.app.state.settings.bcrypt_rounds))
                db.add(target)
                db.flush()
                audit_event(db, 'user_created_by_admin', actor.id, 'user', target.id, request=request)
            created.append(user_data(target))
        except IntegrityError:
            errors.append({'index': index, 'code': 'EMAIL_EXISTS', 'detail': 'Email already registered'})
    db.commit()
    return {'created': len(created), 'failed': len(errors), 'users': created, 'errors': errors}


@router.patch('/sessions/{session_id}/status')
def session_status(session_id: UUID, payload: StatusUpdate, request: Request,
                   actor=Depends(require_roles('admin')), db=Depends(get_db)):
    session = db.get(Session, str(session_id))
    if session is None:
        raise ApiError(404, 'Session not found', 'SESSION_NOT_FOUND')
    previous = session.status
    if not request.app.state.settings.public_test_mode:
        from app.routers.sessions import SessionUpdate, update_session
        result = update_session(session_id, SessionUpdate(status=payload.status), request, db, actor)
        return {**result, 'message': f"Session status changed from '{previous}' to '{payload.status}'"}
    session.status = payload.status
    if payload.status == 'active':
        session.actual_start = utcnow()
        session.actual_end = None
    elif payload.status in {'closed', 'cancelled'}:
        session.actual_end = utcnow()
    else:
        session.actual_start = session.actual_end = None
    audit_event(db, 'session_status_changed', actor.id, 'session', session.id,
                details={'from': previous, 'to': payload.status}, request=request)
    db.commit()
    return {**session_data(session, db), 'message': f"Session status changed from '{previous}' to '{payload.status}'"}


@router.post('/enrollments/', status_code=201)
def admin_enrollment(payload: EnrollmentCreate, request: Request,
                     actor=Depends(require_roles('admin')), db=Depends(get_db)):
    return create_enrollment(body=payload, request=request, db=db, actor=actor)
