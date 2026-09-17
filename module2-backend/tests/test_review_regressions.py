def test_checkin_filters_reject_naive_and_reversed_ranges(client, make_user):
    _, admin = make_user('admin')
    response = client.get('/api/v1/checkins/', headers=admin, params={'start_date': '2026-01-01T00:00:00'})
    assert response.status_code == 422
    assert response.json()['code'] == 'VALIDATION_ERROR'
    for params in ({'start_date': '2026-02-01T00:00:00Z', 'end_date': '2026-01-01T00:00:00Z'},
                   {'min_risk_score': 0.8, 'max_risk_score': 0.2}):
        assert client.get('/api/v1/checkins/', headers=admin, params=params).status_code == 400


def test_admin_device_trust_does_not_fabricate_attestation(client, make_user):
    _, student = make_user()
    _, admin = make_user('admin')
    device = client.post('/api/v1/devices/register', headers=student, json={'device_fingerprint': 'review-device'}).json()
    response = client.patch(f"/api/v1/devices/{device['id']}", headers=admin, json={'is_trusted': True})
    assert response.status_code == 200
    assert response.json()['is_trusted'] is True
    assert response.json()['attestation_passed'] is False
    assert response.json()['last_attestation_at'] is None
    assert client.patch(f"/api/v1/devices/{device['id']}", headers=admin, json={'is_active': None}).status_code == 422
