from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.database import utcnow
from app.errors import ApiError
from app.models import Course, CourseStaff, Enrollment, Session, User
from app.security import active_family, decode_token

bearer = HTTPBearer(auto_error=False)


def get_settings(request: Request):
    return request.app.state.settings


def get_db(request: Request):
    with request.app.state.session_factory() as db:
        db.info['settings'] = request.app.state.settings
        try:
            yield db
        except Exception:
            db.rollback()
            raise


def current_user(request: Request, credentials: HTTPAuthorizationCredentials = Depends(bearer), db=Depends(get_db)):
    if credentials is None or credentials.scheme.lower() != 'bearer':
        raise ApiError(401, 'Authentication required', 'AUTH_REQUIRED')
    settings = request.app.state.settings
    claims = decode_token(credentials.credentials, settings, 'access')
    user = db.get(User, claims['sub'])
    if user is None:
        raise ApiError(401, 'Invalid or expired token', 'INVALID_TOKEN')
    if not user.is_active:
        raise ApiError(403, 'Account is disabled', 'ACCOUNT_DISABLED')
    if not active_family(db, user.id, claims['sid']):
        raise ApiError(401, 'Login session has expired or been revoked', 'SESSION_REVOKED')
    request.state.auth_claims = claims
    request.state.user_id = user.id
    if not settings.public_test_mode:
        request.app.state.limiter.check(f'api:{user.id}', 1000, 3600)
    return user


def require_roles(*roles):
    def permitted(user=Depends(current_user)):
        if user.role not in roles:
            raise ApiError(403, 'Insufficient permissions', 'FORBIDDEN')
        return user
    return permitted


def visible_course_ids(db, user):
    settings = db.info.get('settings')
    if user.role == 'admin' or (settings and settings.public_test_mode and user.role in {'instructor', 'ta'}):
        return list(db.scalars(select(Course.id)))
    if user.role == 'student':
        return list(db.scalars(select(Enrollment.course_id).where(
            Enrollment.student_id == user.id, Enrollment.is_active.is_(True))))
    assigned = set(db.scalars(select(CourseStaff.course_id).where(CourseStaff.user_id == user.id)))
    assigned.update(db.scalars(select(Session.course_id).where(Session.instructor_id == user.id)))
    return list(assigned)


def ensure_course_access(db, user, course_id, *, allow_student=False):
    course = db.get(Course, str(course_id))
    if course is None:
        raise ApiError(404, 'Course not found', 'COURSE_NOT_FOUND')
    if user.role == 'student' and not allow_student:
        raise ApiError(403, 'Staff access required', 'FORBIDDEN')
    if course.id not in visible_course_ids(db, user):
        raise ApiError(403, 'You do not have access to this course', 'FORBIDDEN')
    return course
