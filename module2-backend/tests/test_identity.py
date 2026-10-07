import uuid


def register(client, **overrides):
    body = {'email': f'{uuid.uuid4().hex}@example.com', 'password': 'Password123!',
            'full_name': 'Student', 'role': 'student', **overrides}
    return client.post('/api/v1/auth/register', json=body), body


def login(client, body):
    return client.post('/api/v1/auth/login', json={key: body[key] for key in ('email', 'password')})


def test_registration_login_and_partial_profile_update(client):
    response, body = register(client)
    assert response.status_code == 201
    assert 'hashed_password' not in response.json()
    tokens = login(client, body).json()
    assert tokens['token_type'] == 'bearer'
    headers = {'Authorization': f"Bearer {tokens['access_token']}"}
    profile = client.put('/api/v1/users/me', headers=headers, json={'camera_consent': True})
    assert profile.status_code == 200
    assert profile.json()['camera_consent'] is True
    assert profile.json()['full_name'] == body['full_name']


def test_public_registration_cannot_create_admin(client):
    response, _ = register(client, role='admin')
    assert response.status_code == 403
    assert response.json()['code'] == 'ROLE_NOT_ALLOWED'


def test_validation_does_not_echo_password(client):
    response, _ = register(client, password='bad', email='not-an-email')
    assert response.status_code == 422
    assert isinstance(response.json()['detail'], list)
    assert 'bad' not in response.text
    assert 'request_id' in response.json()


def test_refresh_is_one_time_and_reuse_revokes_family(client):
    _, body = register(client)
    first = login(client, body).json()
    second = client.post('/api/v1/auth/refresh', json={'refresh_token': first['refresh_token']})
    assert second.status_code == 200
    assert second.json()['refresh_token'] != first['refresh_token']
    replay = client.post('/api/v1/auth/refresh', json={'refresh_token': first['refresh_token']})
    assert replay.status_code == 401
    headers = {'Authorization': f"Bearer {second.json()['access_token']}"}
    assert client.get('/api/v1/users/me', headers=headers).status_code == 401


def test_refresh_token_cannot_authorize_api_and_access_cannot_refresh(client):
    _, body = register(client)
    tokens = login(client, body).json()
    headers = {'Authorization': f"Bearer {tokens['refresh_token']}"}
    assert client.get('/api/v1/users/me', headers=headers).status_code == 401
    assert client.post('/api/v1/auth/refresh', json={'refresh_token': tokens['access_token']}).status_code == 401


def test_logout_revokes_access_and_refresh(client):
    _, body = register(client)
    tokens = login(client, body).json()
    headers = {'Authorization': f"Bearer {tokens['access_token']}"}
    assert client.post('/api/v1/auth/logout', headers=headers).status_code == 204
    assert client.get('/api/v1/users/me', headers=headers).status_code == 401
    assert client.post('/api/v1/auth/refresh', json={'refresh_token': tokens['refresh_token']}).status_code == 401


def test_duplicate_registration_and_wrong_password(client):
    _, body = register(client)
    assert client.post('/api/v1/auth/register', json=body).status_code == 400
    assert login(client, {**body, 'password': 'WrongPassword123'}).status_code == 401


def test_unknown_route_has_consistent_error(client):
    response = client.get('/api/v1/not-a-route')
    assert response.status_code == 404
    assert response.json()['code'] == 'NOT_FOUND'
    assert response.headers['x-request-id'] == response.json()['request_id']
