import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.models import AuditLog


def test_audit_history_cannot_be_rewritten_with_sql(db):
    entry = AuditLog(action='original', details={})
    db.add(entry)
    db.commit()
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE audit_logs SET action='forged' WHERE id=:id"), {'id': entry.id})
        db.commit()
    db.rollback()
    assert db.get(AuditLog, entry.id).action == 'original'


def test_test_compatibility_requires_explicit_test_environment():
    from app.config import Settings
    with pytest.raises(ValueError):
        Settings(environment='production', public_test_mode=True)


def test_errors_and_metrics_use_route_patterns(client):
    response = client.get('/api/v1/users/not-a-valid-uuid')
    assert response.status_code in (401, 422)
    assert response.headers['x-request-id']
    assert 'saiv_http_requests_total' in client.get('/metrics').text


def test_large_body_rejected_without_echo(client):
    body = 'private-photo-marker' * 500000
    response = client.post('/api/v1/auth/register', content=body)
    assert response.status_code == 413
    assert 'private-photo-marker' not in response.text


def test_validation_reports_all_invalid_fields(client):
    response = client.post('/api/v1/auth/register', json={'email': 'bad', 'password': 'short', 'full_name': ''})
    assert response.status_code == 422
    fields = {error['loc'][-1] for error in response.json()['detail']}
    assert {'email', 'password', 'full_name'} <= fields


def test_escaped_name_cannot_overflow_database_column(client):
    response = client.post('/api/v1/auth/register', json={
        'email': 'name-overflow@example.com', 'password': 'Password123!', 'full_name': '<' * 100,
    })
    assert response.status_code == 422


def test_unhandled_error_keeps_request_id_and_hides_input(client, caplog):
    from fastapi.testclient import TestClient
    @client.app.get('/test-error')
    def fail():
        raise RuntimeError('private-image-data')
    # It must be handled inside the app so Uvicorn cannot re-log its unsafe message.
    with TestClient(client.app, raise_server_exceptions=True) as safe_client:
        response = safe_client.get('/test-error', headers={'X-Request-ID': 'failure-test'})
    assert response.status_code == 500
    assert response.headers['x-request-id'] == 'failure-test'
    assert response.json()['request_id'] == 'failure-test'
    assert 'private-image-data' not in response.text
    assert 'private-image-data' not in caplog.text
