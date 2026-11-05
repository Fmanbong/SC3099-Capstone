"""Graded account lockout is separate from the high per-IP traffic quota."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.main import create_app


def attempt(client, email, password='incorrect-password', **kwargs):
    return client.post('/api/v1/auth/login', json={'email': email, 'password': password}, **kwargs)


@pytest.mark.parametrize('fixture', ['client', 'compat_client'])
def test_tenth_failure_locks_account_even_with_correct_password(request, fixture):
    client = request.getfixturevalue(fixture)
    email = 'lockout@example.com'
    assert client.post('/api/v1/auth/register', json={
        'email': email, 'password': 'Password123!', 'full_name': 'Lockout Student',
    }).status_code == 201
    assert [attempt(client, email).status_code for _ in range(9)] == [401] * 9
    assert attempt(client, email).status_code == 429
    assert attempt(client, email, 'Password123!').status_code == 429
    assert attempt(client, email, headers={'X-Forwarded-For': '192.168.1.10'}).status_code == 429


def test_success_resets_failures_and_other_accounts_are_independent(client, make_user):
    first, _ = make_user()
    second, _ = make_user()
    for _ in range(9):
        assert attempt(client, first.email).status_code == 401
    assert attempt(client, first.email, 'Password123!').status_code == 200
    for _ in range(9):
        assert attempt(client, first.email).status_code == 401
    assert attempt(client, first.email).status_code == 429
    assert attempt(client, second.email, 'Password123!').status_code == 200


def test_lockout_survives_new_app_instance(client, settings, make_user):
    user, _ = make_user()
    for _ in range(10):
        attempt(client, user.email)
    with TestClient(create_app(settings), headers={'X-Forwarded-For': '127.0.0.1'}) as restarted:
        assert attempt(restarted, user.email, 'Password123!').status_code == 429


def test_concurrent_failures_do_not_lose_increments(client, make_user):
    user, _ = make_user()
    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(lambda _: attempt(client, user.email).status_code, range(14)))
    assert codes.count(401) == 9
    assert codes.count(429) == 5
    assert attempt(client, user.email, 'Password123!').status_code == 429


@pytest.mark.parametrize('fixture', ['client', 'compat_client'])
@pytest.mark.parametrize('endpoint', ['login', 'register'])
def test_ip_quota_allows_the_hundred_thousandth_request_then_blocks(request, fixture, endpoint, monkeypatch):
    from app.errors import ApiError
    client = request.getfixturevalue(fixture)
    # Emulate only the external counter storage; the route owns the key/quota/window.
    counters = {(f'{endpoint}:127.0.0.1', 3600): 99_999}

    def counter(identifier, limit, window):
        key = (identifier, window)
        counters[key] = counters.get(key, 0) + 1
        if counters[key] > limit:
            raise ApiError(429, 'Too many requests', 'RATE_LIMITED')

    monkeypatch.setattr(client.app.state.limiter, 'check', counter)
    if endpoint == 'login':
        assert attempt(client, 'unknown@example.com').status_code == 401
        assert attempt(client, 'unknown@example.com').status_code == 429
    else:
        body = {'email': 'quota@example.com', 'password': 'Password123!', 'full_name': 'Quota Student'}
        assert client.post('/api/v1/auth/register', json=body).status_code == 201
        assert client.post('/api/v1/auth/register', json=body).status_code == 429


@pytest.mark.parametrize(('socket', 'forwarded', 'expected'), [
    ('127.0.0.1', None, '127.0.0.1'),
    ('10.0.0.1', '8.8.8.8, 127.0.0.1', '8.8.8.8'),
    ('10.0.0.1', ' 2404:6800:4003:c00::64 , 10.1.1.1', '2404:6800:4003:c00::64'),
    ('::ffff:192.168.1.4', None, '192.168.1.4'),
])
def test_client_address_uses_first_forwarded_address(socket, forwarded, expected):
    from app.services.client_ip import resolve_client_ip
    headers = [] if forwarded is None else [(b'x-forwarded-for', forwarded.encode())]
    req = Request({'type': 'http', 'headers': headers, 'client': (socket, 1234)})
    assert resolve_client_ip(req) == expected


@pytest.mark.parametrize('value', ['', 'garbage', ', 127.0.0.1', '8.8.8.8:1234'])
def test_invalid_forwarded_address_is_not_replaced_by_local_socket(value):
    from app.errors import ApiError
    from app.services.client_ip import resolve_client_ip
    req = Request({'type': 'http', 'headers': [(b'x-forwarded-for', value.encode())],
                   'client': ('127.0.0.1', 1234)})
    with pytest.raises(ApiError) as error:
        resolve_client_ip(req)
    assert error.value.status_code == 403
