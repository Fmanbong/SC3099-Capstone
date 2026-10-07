import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from app.models import User
from app.security import hash_password, issue_tokens


class UnavailableFaceService:
    def __getattr__(self, name):
        if name == 'close':
            return lambda: None
        def unavailable(*args, **kwargs):
            raise ApiError(503, 'Face service unavailable in this test', 'FACE_SERVICE_UNAVAILABLE')
        return unavailable


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=f'sqlite:///{tmp_path}/backend.db', secret_key='test-only-secret-key-with-at-least-32-characters',
                    environment='test', rate_limit_enabled=False, auto_create_schema=True, retention_enabled=False)


@pytest.fixture
def client(settings):
    app = create_app(settings, face_client=UnavailableFaceService())
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def compat_client(settings, tmp_path):
    config = settings.model_copy(update={'public_test_mode': True, 'database_url': f'sqlite:///{tmp_path}/compat.db'})
    with TestClient(create_app(config, face_client=UnavailableFaceService())) as test_client:
        yield test_client


@pytest.fixture
def db(client):
    with client.app.state.session_factory() as session:
        session.info['settings'] = client.app.state.settings
        yield session


@pytest.fixture
def make_user(db, client):
    import uuid
    def make(role='student', **overrides):
        password = overrides.pop('password', 'Password123!')
        data = {'email': f'{uuid.uuid4().hex}@example.com', 'full_name': 'Test User', 'role': role,
                'hashed_password': hash_password(password), **overrides}
        user = User(**data)
        db.add(user)
        db.flush()
        tokens = issue_tokens(db, user, client.app.state.settings)
        db.commit()
        return user, {'Authorization': f"Bearer {tokens['access_token']}"}
    return make
