"""Opt-in checks against dedicated Redis/PostgreSQL instances, never shared data."""
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select, text
from sqlalchemy.exc import DBAPIError

from app.database import build_database, utcnow
from app.errors import ApiError
from app.main import create_app
from app.models import AuditLog, CheckIn, Course, Device, Enrollment, Session, User
from app.security import issue_tokens
from app.services.rate_limit import RateLimiter


@pytest.fixture
def real_limiter(settings):
    url = os.getenv('SAIV_TEST_REDIS_URL')
    if not url:
        pytest.skip('Set SAIV_TEST_REDIS_URL to a dedicated test Redis')
    limiter = RateLimiter(settings.model_copy(update={'redis_url': url, 'rate_limit_enabled': True}))
    yield limiter
    limiter.close()


def key_for(identifier, window):
    return 'saiv:rate_limit:' + hashlib.sha256(identifier.encode()).hexdigest() + f':{window}'


def test_redis_counter_is_atomic_with_expiry(real_limiter):
    identifier = f'test:{uuid4()}'
    key = key_for(identifier, 60)
    def attempt(_):
        try:
            real_limiter.check(identifier, 10, 60)
            return 200
        except ApiError as exc:
            assert exc.status_code == 429
            assert 1 <= int(exc.headers['Retry-After']) <= 60
            return 429
    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            codes = list(pool.map(attempt, range(40)))
        assert codes.count(200) == 10
        assert codes.count(429) == 30
        assert int(real_limiter.redis.get(key)) == 40
        real_limiter.redis.expire(key, 10)
        with pytest.raises(ApiError):
            real_limiter.check(identifier, 10, 60)
        assert 0 < real_limiter.redis.ttl(key) <= 10
    finally:
        real_limiter.redis.delete(key)


def test_real_login_quota_and_outage_response(settings, real_limiter):
    app = create_app(settings)
    with TestClient(app, headers={'X-Forwarded-For': '127.0.0.1'}) as client:
        original = app.state.limiter
        app.state.limiter = real_limiter
        identifier = 'login:127.0.0.1'
        key = key_for(identifier, 3600)
        try:
            real_limiter.redis.set(key, 99_999, ex=3600)
            body = {'email': 'missing@example.com', 'password': 'not-a-password'}
            assert client.post('/api/v1/auth/login', json=body).status_code == 401
            response = client.post('/api/v1/auth/login', json=body)
            assert response.status_code == 429
            assert response.headers['Retry-After']
            assert response.json()['code'] == 'RATE_LIMITED'
        finally:
            real_limiter.redis.delete(key)
            app.state.limiter = original


def test_redis_outage_fails_closed(settings):
    limiter = RateLimiter(settings.model_copy(update={'redis_url': 'redis://127.0.0.1:1/0', 'rate_limit_enabled': True}))
    try:
        with pytest.raises(ApiError) as failure:
            limiter.check('outage', 10, 60)
        assert failure.value.status_code == 503
        assert failure.value.code == 'RATE_LIMIT_UNAVAILABLE'
    finally:
        limiter.close()


def test_postgresql_migration_audit_guards_and_user_unlink():
    url = os.getenv('SAIV_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set SAIV_TEST_DATABASE_URL to a migrated test PostgreSQL database')
    engine, _ = build_database(url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                assert connection.scalar(text('SELECT version_num FROM alembic_version'))
                user_id, audit_id = str(uuid4()), str(uuid4())
                connection.execute(insert(User).values(id=user_id, email=f'{uuid4()}@example.com', full_name='Test', hashed_password='not-a-real-password'))
                connection.execute(insert(AuditLog).values(id=audit_id, user_id=user_id, action='original'))
                assert connection.scalar(select(User.created_at).where(User.id == user_id)).utcoffset() is not None
                for statement in ('UPDATE audit_logs SET action=:value WHERE id=:id', 'DELETE FROM audit_logs WHERE id=:id'):
                    with pytest.raises(DBAPIError):
                        with connection.begin_nested():
                            connection.execute(text(statement), {'id': audit_id, 'value': 'forged'})
                connection.execute(delete(User).where(User.id == user_id))
                record = connection.execute(select(AuditLog.user_id, AuditLog.action).where(AuditLog.id == audit_id)).one()
                assert record == (None, 'original')
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def test_postgresql_concurrent_checkin_and_refresh_reuse(settings):
    url = os.getenv('SAIV_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set SAIV_TEST_DATABASE_URL to a migrated test PostgreSQL database')
    settings = settings.model_copy(update={'database_url': url, 'auto_create_schema': False})
    app = create_app(settings)
    with TestClient(app, headers={'X-Forwarded-For': '127.0.0.1'}) as client:
        with app.state.session_factory() as db:
            user = User(email=f'{uuid4()}@example.com', full_name='Concurrent Student',
                        hashed_password='not-used-for-login', geolocation_consent=True)
            course = Course(code=uuid4().hex[:20], name='Concurrent Test', semester='Test',
                            venue_latitude=1.3483, venue_longitude=103.6831, require_device_binding=False)
            db.add_all([user, course])
            db.flush()
            now = utcnow()
            session = Session(course_id=course.id, name='Concurrent Test', status='active',
                              scheduled_start=now, scheduled_end=now + timedelta(hours=1),
                              checkin_opens_at=now - timedelta(minutes=5),
                              checkin_closes_at=now + timedelta(minutes=30), require_liveness_check=False)
            device = Device(user_id=user.id, device_fingerprint=uuid4().hex)
            db.add_all([session, device, Enrollment(student_id=user.id, course_id=course.id)])
            db.flush()
            tokens = issue_tokens(db, user, settings)
            db.commit()
            student_id, session_id, fingerprint = user.id, session.id, device.device_fingerprint
        headers = {'Authorization': f"Bearer {tokens['access_token']}"}
        payload = {'session_id': session_id, 'latitude': 1.3483, 'longitude': 103.6831,
                   'device_fingerprint': fingerprint}
        with ThreadPoolExecutor(max_workers=8) as pool:
            codes = list(pool.map(lambda _: client.post('/api/v1/checkins/', headers=headers, json=payload).status_code, range(8)))
        assert codes.count(201) == 1
        assert codes.count(400) == 7
        with app.state.session_factory() as db:
            assert len(db.scalars(select(CheckIn).where(CheckIn.student_id == student_id)).all()) == 1
        with ThreadPoolExecutor(max_workers=6) as pool:
            refreshed = list(pool.map(lambda _: client.post('/api/v1/auth/refresh', json={'refresh_token': tokens['refresh_token']}), range(6)))
        assert [r.status_code for r in refreshed].count(200) == 1
        assert [r.status_code for r in refreshed].count(401) == 5
        replacement = next(r.json()['access_token'] for r in refreshed if r.status_code == 200)
        assert client.get('/api/v1/users/me', headers={'Authorization': f'Bearer {replacement}'}).status_code == 401
