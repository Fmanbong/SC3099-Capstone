import uuid


def test_admin_deactivation_invalidates_existing_access(client, make_user):
    _, admin_headers = make_user('admin')
    student, student_headers = make_user()
    response = client.patch(f'/api/v1/admin/users/{student.id}/deactivate', headers=admin_headers)
    assert response.status_code == 200
    assert response.json()['is_active'] is False
    assert client.get('/api/v1/users/me', headers=student_headers).status_code == 403
    assert client.patch(f'/api/v1/admin/users/{student.id}/activate', headers=admin_headers).status_code == 200
    assert client.get('/api/v1/users/me', headers=student_headers).status_code == 401


def test_bulk_user_creation_is_admin_only_and_handles_duplicate(client, make_user):
    _, admin = make_user('admin')
    _, student = make_user()
    user = {'email': f'{uuid.uuid4().hex}@example.com', 'full_name': 'New', 'password': 'Password123!'}
    assert client.post('/api/v1/admin/users/bulk', headers=student, json={'users': [user]}).status_code == 403
    response = client.post('/api/v1/admin/users/bulk', headers=admin, json={'users': [user, user]})
    assert response.status_code == 201
    assert response.json()['created'] == 1
    assert response.json()['failed'] == 1
    assert 'hashed_password' not in response.text
