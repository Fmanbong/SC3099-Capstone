from datetime import timedelta

from app.database import utcnow


def test_reenrollment_preserves_the_previous_closed_period(client, make_user):
    from sqlalchemy import select
    from app.models import EnrollmentPeriod
    _, admin = make_user('admin')
    student, _ = make_user()
    course = client.post('/api/v1/courses/', headers=admin, json={
        'code': 'HISTORY', 'name': 'History', 'semester': 'AY26',
    }).json()
    body = {'student_id': student.id, 'course_id': course['id']}
    original = client.post('/api/v1/enrollments/', headers=admin, json=body).json()
    assert client.delete(f"/api/v1/enrollments/{original['id']}", headers=admin).status_code == 204
    reopened = client.post('/api/v1/enrollments/', headers=admin, json=body)
    assert reopened.status_code == 201
    assert reopened.json()['id'] == original['id']
    with client.app.state.session_factory() as db:
        periods = db.scalars(select(EnrollmentPeriod).where(EnrollmentPeriod.enrollment_id == original['id'])).all()
        assert len(periods) == 1
        assert periods[0].started_at <= periods[0].ended_at


def test_student_can_preview_session_but_checkin_requires_enrollment(client, make_user):
    _, admin = make_user('admin')
    _, student = make_user(geolocation_consent=True)
    course = client.post('/api/v1/courses/', headers=admin, json={
        'code': 'SC3099', 'name': 'Capstone', 'semester': 'AY26',
    }).json()
    now = utcnow()
    session = client.post('/api/v1/sessions/', headers=admin, json={
        'course_id': course['id'], 'name': 'Preview', 'require_liveness_check': False,
        'scheduled_start': (now + timedelta(minutes=5)).isoformat(),
        'scheduled_end': (now + timedelta(hours=1)).isoformat(),
    }).json()
    assert client.get(f"/api/v1/sessions/{session['id']}", headers=student).status_code == 200
    active = client.patch(f"/api/v1/admin/sessions/{session['id']}/status", headers=admin, json={'status': 'active'})
    assert active.status_code == 200
    attempt = client.post('/api/v1/checkins/', headers=student, json={
        'session_id': session['id'], 'latitude': 1.34, 'longitude': 103.68, 'device_fingerprint': 'preview-device',
    })
    assert attempt.status_code == 403
    assert attempt.json()['code'] == 'ENROLLMENT_REQUIRED'
    assert client.patch(f"/api/v1/admin/sessions/{session['id']}/status", headers=admin, json={'status': 'closed'}).status_code == 200
    assert client.patch(f"/api/v1/admin/sessions/{session['id']}/status", headers=admin, json={'status': 'active'}).status_code == 400
