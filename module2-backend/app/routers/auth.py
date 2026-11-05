from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import EmailStr, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.database import utcnow
from app.dependencies import current_user, get_db
from app.errors import ApiError
from app.models import RefreshSession, User
from app.schemas import CleanModel, Name
from app.security import decode_token, hash_password, issue_tokens, revoke_family, token_hash, verify_password
from app.serializers import user_data
from app.services.audit import audit_event
from app.services.client_ip import resolve_client_ip

router = APIRouter(prefix='/auth', tags=['Authentication'])


class RegisterRequest(CleanModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    full_name: Name
    role: Literal['student', 'ta', 'instructor', 'admin'] = 'student'

    @field_validator('password')
    @classmethod
    def password_bytes(cls, value):
        if len(value.encode()) > 72:
            raise ValueError('Password must be at most 72 UTF-8 bytes')
        return value


class LoginRequest(CleanModel):
    email: EmailStr
    password: str = Field(max_length=1024)


class RefreshRequest(CleanModel):
    refresh_token: str = Field(max_length=8192)


@router.post('/register', status_code=201)
def register(payload: RegisterRequest, request: Request, db=Depends(get_db)):
    settings = request.app.state.settings
    request.app.state.limiter.check(f'register:{resolve_client_ip(request)}', 100_000, 3600)
    if payload.role != 'student' and not settings.public_test_mode:
        raise ApiError(403, 'Public registration only permits student accounts', 'ROLE_NOT_ALLOWED')
    email = str(payload.email).lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise ApiError(400, 'Email already registered', 'EMAIL_EXISTS')
    user = User(email=email, full_name=payload.full_name, role=payload.role,
                hashed_password=hash_password(payload.password, settings.bcrypt_rounds))
    db.add(user)
    try:
        db.flush()
        audit_event(db, 'user_registered', user.id, 'user', user.id, request=request)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(400, 'Email already registered', 'EMAIL_EXISTS') from None
    return user_data(user)


@router.post('/login')
def login(payload: LoginRequest, request: Request, db=Depends(get_db)):
    settings = request.app.state.settings
    request.app.state.limiter.check(f'login:{resolve_client_ip(request)}', 100_000, 3600)
    # Acquire a write lock before reading the counter and checking the password.
    # UPDATE ... RETURNING serializes this transaction on PostgreSQL and SQLite;
    # unlike SELECT FOR UPDATE it also works in isolated SQLite/concurrency tests.
    user = db.scalar(update(User).where(User.email == str(payload.email).lower())
                     .values(failed_login_attempts=User.failed_login_attempts)
                     .returning(User))
    if user is not None and user.failed_login_attempts >= 10:
        raise ApiError(429, 'Account is blocked after repeated incorrect passwords', 'ACCOUNT_LOCKED')
    # Same bcrypt work for unknown and known users to reduce account enumeration.
    stored = user.hashed_password if user else request.app.state.dummy_password_hash
    if not verify_password(payload.password, stored):
        if user is not None:
            user.failed_login_attempts += 1
        audit_event(db, 'login_failed', user.id if user else None, request=request, severity='warning')
        locked = user is not None and user.failed_login_attempts >= 10
        if locked:
            audit_event(db, 'account_locked', user.id, request=request, severity='warning')
        db.commit()
        if locked:
            raise ApiError(429, 'Account is blocked after repeated incorrect passwords', 'ACCOUNT_LOCKED')
        raise ApiError(401, 'Invalid email or password', 'INVALID_CREDENTIALS')
    if user is None:
        raise ApiError(401, 'Invalid email or password', 'INVALID_CREDENTIALS')
    if not user.is_active:
        raise ApiError(403, 'Account is disabled', 'ACCOUNT_DISABLED')
    user.failed_login_attempts = 0
    tokens = issue_tokens(db, user, settings)
    user.last_login_at = utcnow()
    audit_event(db, 'user_login', user.id, request=request)
    db.commit()
    return {**tokens, 'user': user_data(user)}


@router.post('/refresh')
def refresh(payload: RefreshRequest, request: Request, db=Depends(get_db)):
    settings = request.app.state.settings
    claims = decode_token(payload.refresh_token, settings, 'refresh')
    row = db.scalar(select(RefreshSession).where(RefreshSession.token_hash == token_hash(payload.refresh_token)))
    if row is None or row.user_id != claims['sub'] or row.family_id != claims['sid']:
        raise ApiError(401, 'Invalid refresh token', 'INVALID_TOKEN')
    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise ApiError(403, 'Account is disabled', 'ACCOUNT_DISABLED')
    now = utcnow()
    consumed = db.execute(update(RefreshSession).where(
        RefreshSession.id == row.id, RefreshSession.used_at.is_(None),
        RefreshSession.revoked_at.is_(None), RefreshSession.expires_at > now).values(used_at=now))
    if consumed.rowcount != 1:
        revoke_family(db, row.family_id)
        audit_event(db, 'refresh_token_reuse', user.id, details={'success': False}, severity='warning', request=request)
        db.commit()
        raise ApiError(401, 'Refresh token has expired or was already used', 'REFRESH_REUSED')
    tokens = issue_tokens(db, user, settings, row.family_id)
    db.commit()
    return tokens


@router.post('/logout', status_code=204)
def logout(request: Request, user=Depends(current_user), db=Depends(get_db)):
    revoke_family(db, request.state.auth_claims['sid'])
    audit_event(db, 'user_logout', user.id, request=request)
    db.commit()
    return Response(status_code=204)
