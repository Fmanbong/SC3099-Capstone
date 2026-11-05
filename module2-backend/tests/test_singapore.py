"""Country gates use bundled data and run even without biometric verification."""
import gzip
import json
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import select

from app.errors import ApiError
from app.models import CheckIn
from test_attendance import attendance_setup


@pytest.mark.parametrize(('address', 'allowed'), [
    ('127.0.0.1', True), ('192.168.1.5', True), ('10.2.3.4', True),
    ('172.16.0.2', True), ('169.254.1.2', True), ('::1', True), ('fd00::1', True),
    ('fe80::1', True), ('::ffff:192.168.1.4', True),
    ('128.199.64.1', True), ('139.59.192.1', True), ('2404:6800:4003:c00::64', True),
    ('8.8.8.8', False), ('1.1.1.1', False), ('2606:4700:4700::1111', False),
    ('::ffff:8.8.8.8', False), ('0.0.0.0', False), ('224.0.0.1', False),
    ('192.0.2.1', False), ('100.64.1.1', False), ('not-an-ip', False),
])
def test_public_country_ranges_and_local_addresses(address, allowed):
    from app.services.singapore import default_policy
    policy = default_policy()
    assert policy.allows_ip(address) is allowed


@pytest.mark.parametrize(('latitude', 'longitude', 'allowed'), [
    (1.3483, 103.6831, True),  # NTU
    (1.3138, 103.8159, True),  # Botanic Gardens
    (1.2494, 103.8303, True),  # Sentosa
    (1.4105, 103.9555, True),  # Pulau Ubin
    (1.4655, 103.7578, False),  # Johor Bahru, within a naive SG bounding rectangle
    (1.1301, 104.0529, False),  # Batam
    (3.1390, 101.6869, False), (-33.8688, 151.2093, False),
    (float('nan'), 103.6831, False), (1.3483, float('inf'), False),
])
def test_land_boundary_includes_islands_but_not_nearby_countries(latitude, longitude, allowed):
    from app.services.singapore import default_policy
    policy = default_policy()
    assert policy.allows_coordinates(latitude, longitude) is allowed


@pytest.mark.parametrize(('forwarded', 'lat', 'lng', 'expected'), [
    ('8.8.8.8, 127.0.0.1', 1.3483, 103.6831, 403),
    ('2606:4700:4700::1111', 1.3483, 103.6831, 403),
    ('127.0.0.1', 1.4655, 103.7578, 403),
    ('128.199.64.1', 1.4655, 103.7578, 403),
    ('128.199.64.1', 1.3483, 103.6831, 201),
    ('2404:6800:4003:c00::64', 1.3483, 103.6831, 201),
    ('192.168.1.5, 8.8.8.8', 1.3483, 103.6831, 201),
])
def test_checkin_country_policy_without_face_service(client, db, make_user, forwarded, lat, lng, expected):
    student, headers = make_user(geolocation_consent=True)
    _, session = attendance_setup(db, student, require_device_binding=False)
    session.require_liveness_check = False
    db.commit()
    response = client.post('/api/v1/checkins/', headers={**headers, 'X-Forwarded-For': forwarded}, json={
        'session_id': session.id, 'latitude': lat, 'longitude': lng,
        'device_fingerprint': 'country-device',
    })
    assert response.status_code == expected, response.text
    if expected == 403:
        assert db.scalar(select(CheckIn.id).where(CheckIn.student_id == student.id)) is None


def test_public_test_mode_does_not_bypass_country_policy(compat_client):
    # Reject before resource/face processing; register and log in through real APIs.
    compat_client.post('/api/v1/auth/register', json={
        'email': 'country-test@example.com', 'password': 'Password123!', 'full_name': 'Student',
    })
    token = compat_client.post('/api/v1/auth/login', json={
        'email': 'country-test@example.com', 'password': 'Password123!',
    }).json()['access_token']
    response = compat_client.post('/api/v1/checkins/', headers={
        'Authorization': f'Bearer {token}', 'X-Forwarded-For': '8.8.8.8',
    }, json={'session_id': '00000000-0000-0000-0000-000000000001',
             'latitude': 1.3483, 'longitude': 103.6831, 'device_fingerprint': 'country-test'})
    assert response.status_code == 403


def test_missing_offline_data_fails_closed(tmp_path):
    from app.services.singapore import SingaporePolicy
    with pytest.raises(ApiError) as failure:
        SingaporePolicy(tmp_path)
    assert failure.value.status_code == 503


def build_fixture_data(tmp_path):
    source = tmp_path / 'country.csv.gz'
    with gzip.open(source, 'wt') as stream:
        stream.write('8.8.8.0,8.8.8.255,US\n128.199.64.0,128.199.64.255,SG\n'
                     '2404:6800:4003::,2404:6800:4003:ffff:ffff:ffff:ffff:ffff,SG\n')
    boundary = tmp_path / 'boundary.geojson'
    # An outer square, an excluded interior square, plus a separate island.
    boundary.write_text(json.dumps({'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {}, 'geometry': {'type': 'MultiPolygon', 'coordinates': [
            [[[103, 1], [104, 1], [104, 2], [103, 2], [103, 1]],
             [[103.4, 1.4], [103.6, 1.4], [103.6, 1.6], [103.4, 1.6], [103.4, 1.4]]],
            [[[104.1, 1], [104.2, 1], [104.2, 1.1], [104.1, 1.1], [104.1, 1]]],
        ]}},
    ]}))
    output = tmp_path / 'data'
    result = subprocess.run([
        sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/update_singapore_data.py'),
        '--country-csv', str(source), '--boundary', str(boundary), '--release', '2026-11',
        '--output', str(output),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return output


def test_update_command_builds_offline_ip_and_polygon_data(tmp_path):
    from app.services.singapore import SingaporePolicy
    output = build_fixture_data(tmp_path)
    local = SingaporePolicy(output)
    assert local.allows_ip('128.199.64.10')
    assert not local.allows_ip('8.8.8.8')
    assert local.allows_ip('2404:6800:4003:c00::64')
    assert local.allows_coordinates(1.2, 103.2)
    assert local.allows_coordinates(1.05, 104.15)
    assert not local.allows_coordinates(1.5, 103.5)  # Hole
    assert local.allows_coordinates(1.0, 103.5)  # Land boundary edge
    assert not local.allows_coordinates(0.9, 103.5)


def test_tampered_offline_data_fails_closed(tmp_path):
    from app.services.singapore import SingaporePolicy
    output = build_fixture_data(tmp_path)
    (output / 'singapore-ip-ranges.json').write_text('{}')
    with pytest.raises(ApiError) as failure:
        SingaporePolicy(output)
    assert failure.value.status_code == 503
