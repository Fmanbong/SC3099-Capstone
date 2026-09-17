"""Password verification and revocable, rotating JWT login sessions."""
import hashlib
import uuid
from datetime import timedelta

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select, update

from app.database import utcnow
from app.errors import ApiError
from app.models import RefreshSession

ISSUER = 'saiv-backend'
AUDIENCE = 'saiv'


def hash_password(password, rounds=10):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(password, encoded):
    try:
        return len(password.encode()) <= 72 and bcrypt.checkpw(password.encode(), encoded.encode())
    except (ValueError, TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def decode_token(token, settings, token_type):
    try:
        if len(token) > 8192:
            raise ValueError('Token too long')
        claims = jwt.decode(token, settings.secret_key, algorithms=['HS256'], audience=AUDIENCE,
                            issuer=ISSUER, options={'require_exp': True, 'require_sub': True, 'require_iat': True})
        if claims.get('type') != token_type:
            raise ValueError('Wrong token type')
        uuid.UUID(claims['sub'])
        uuid.UUID(claims['sid'])
        uuid.UUID(claims['jti'])
        return claims
    except (JWTError, ValueError, KeyError, TypeError):
        raise ApiError(401, 'Invalid or expired token', 'INVALID_TOKEN') from None


def issue_tokens(db, user, settings, family_id=None):
    now = utcnow()
    family_id = family_id or str(uuid.uuid4())
    base = {'sub': user.id, 'role': user.role, 'sid': family_id, 'iat': now,
            'iss': ISSUER, 'aud': AUDIENCE}
    access = jwt.encode({**base, 'type': 'access', 'jti': str(uuid.uuid4()),
                         'exp': now + timedelta(minutes=settings.access_token_expire_minutes)},
                        settings.secret_key, algorithm='HS256')
    expires = now + timedelta(days=settings.refresh_token_expire_days)
    refresh = jwt.encode({**base, 'type': 'refresh', 'jti': str(uuid.uuid4()), 'exp': expires},
                         settings.secret_key, algorithm='HS256')
    db.add(RefreshSession(user_id=user.id, family_id=family_id,
                          token_hash=token_hash(refresh), expires_at=expires))
    return {'access_token': access, 'refresh_token': refresh, 'token_type': 'bearer'}


def revoke_family(db, family_id):
    db.execute(update(RefreshSession).where(RefreshSession.family_id == family_id,
                                           RefreshSession.revoked_at.is_(None)).values(revoked_at=utcnow()))


def revoke_user(db, user_id):
    db.execute(update(RefreshSession).where(RefreshSession.user_id == user_id,
                                           RefreshSession.revoked_at.is_(None)).values(revoked_at=utcnow()))


def active_family(db, user_id, family_id):
    return db.scalar(select(RefreshSession.id).where(
        RefreshSession.user_id == user_id, RefreshSession.family_id == family_id,
        RefreshSession.revoked_at.is_(None), RefreshSession.used_at.is_(None),
        RefreshSession.expires_at > utcnow()).limit(1)) is not None
