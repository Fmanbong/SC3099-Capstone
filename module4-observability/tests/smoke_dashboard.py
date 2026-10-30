
""" commentary
- this file is for testing dashboard workflows against a temp backend
"""

import csv , io , os , sys , requests , socket , tempfile , threading , time , uvicorn
from datetime     import timedelta ; from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'module2-backend'))
sys.path.insert(0, str(ROOT / 'module4-observability/app'))

from app.config   import Settings
from api_client   import APIClient, APIError
from app.models   import User, Course, CourseStaff, Enrollment, Session, CheckIn
from app.main     import create_app
from app.database import utcnow
from app.security import hash_password
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import Selectbox


def serialized_index(widget):
    metadata = widget.root.session_state._state._new_widget_state.widget_metadata.get(widget.id)
    if metadata:
        return metadata.serializer(widget.value)
    return widget.options.index(str(widget.value)) if widget.value is not None else None
Selectbox.index = property(serialized_index)

temporary = tempfile.TemporaryDirectory(prefix='saiv-review-')
settings  = Settings(database_url=f'sqlite:///{temporary.name}/dashboard.db',
                    secret_key='review-only-secret-at-least-32-characters', environment='test', public_test_mode=False,
                    auto_create_schema=True, rate_limit_enabled=False, retention_enabled=False)

app  = create_app(settings)
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
port = sock.getsockname()[1]
sock.close()
base = f'http://127.0.0.1:{port}'
server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
for _ in range(100):
    if  server.started:
        break
    time.sleep(.05)
assert server.started
os.environ['BACKEND_URL'] = base
os.environ['PROMETHEUS_URL'] = 'http://127.0.0.1:1'
os.environ.pop('OTEL_EXPORTER_OTLP_ENDPOINT', None)

now = utcnow()
with app.state.session_factory() as db:
    users = {}
    for role in ['admin', 'instructor', 'student']:
        users[role] = User(email=f'{role}@review.example.com', full_name=f'Review {role}',
                           hashed_password=hash_password('ReviewPassword123!'), role=role,
                           camera_consent=True, geolocation_consent=True)
        db.add(users[role])
    db.flush()
    course = Course(code='REVIEW1', name='Review course', semester='2026',
                    require_face_recognition=False, require_device_binding=False,
                    venue_latitude=1.3483, venue_longitude=103.6831)
    foreign_course = Course(code='OTHER1', name='Other staff course', semester='2026')
    db.add_all([course, foreign_course]); db.flush()
    db.add(CourseStaff(course_id=course.id, user_id=users['instructor'].id))
    db.add(Enrollment(course_id=course.id, student_id=users['student'].id, enrolled_at=now - timedelta(days=1)))
    session = Session(course_id=course.id, instructor_id=users['instructor'].id,
                      name='Review active session', session_type='lecture', status='active',
                      scheduled_start=now - timedelta(minutes=5), scheduled_end=now + timedelta(hours=1),
                      checkin_opens_at=now - timedelta(minutes=10), checkin_closes_at=now + timedelta(minutes=30),
                      require_liveness_check=False, require_face_match=False)
    foreign = Session(course_id=foreign_course.id, instructor_id=users['admin'].id,
                      name='Foreign session', session_type='lecture', status='active',
                      scheduled_start=now - timedelta(minutes=5), scheduled_end=now + timedelta(hours=1),
                      checkin_opens_at=now - timedelta(minutes=10), checkin_closes_at=now + timedelta(minutes=30))
    db.add_all([session, foreign]); db.flush()
    flagged = CheckIn(session_id=session.id, student_id=users['student'].id,
                      checked_in_at=now, status='flagged', risk_score=.55,
                      risk_factors=[{'type': 'face_match_uncertain', 'score': .55}])
    db.add(flagged); db.commit()
    sid, fid, cid, checkid = session.id, foreign.id, course.id, flagged.id

checks = []
def passed(message):
    checks.append(message)
    print('PASS:', message, flush=True)

def no_crash(at, label):
    assert not at.exception, f'{label}: {[e.message for e in at.exception]}'

def dashboard(role):
    at = AppTest.from_file(str(ROOT / 'module4-observability/app/main.py'), default_timeout=30).run()
    no_crash(at, 'login page')
    at.text_input(key='login_email').set_value(f'{role}@review.example.com')
    at.text_input(key='login_password').set_value('ReviewPassword123!')
    at.button[0].click().run()
    no_crash(at, f'{role} login')
    return at

try:
    at = dashboard('student')
    assert any('instructor or administrator' in e.value.lower() for e in at.error)
    assert not at.session_state['api'].token
    passed('Student login cannot enter dashboard and clears credentials')
    instructor = dashboard('instructor')
    for page in ['Overview', 'Sessions', 'Check-ins', 'Audit logs', 'Metrics']:
        instructor.radio(key='navigation').set_value(page).run()
        no_crash(instructor, f'instructor {page}')
        passed(f'Instructor {page} renders with populated backend records')
    instructor.radio(key='navigation').set_value('Audit logs').run()
    assert any('restricted to administrators' in e.value for e in instructor.info)
    passed('Audit logs page refuses instructor access')
    instructor.radio(key='navigation').set_value('Overview').run()
    instructor.button(key='overview_review_queue').click().run()
    no_crash(instructor, 'overview review shortcut')
    assert instructor.radio(key='navigation').value == 'Check-ins'
    assert instructor.checkbox(key='checkins_review_only').value
    assert instructor.selectbox(key='checkins_status').disabled
    passed('Overview shortcut opens the review-only queue without widget state errors')
    client = instructor.session_state['api']
    ids = {row['id'] for row in client.get_all_sessions()}
    assert sid in ids and fid not in ids
    try:
        client.get_session(fid)
        raise AssertionError('Foreign session readable')
    except APIError as exc:
        assert exc.status == 403
    passed('Instructor session selector and direct API restrict foreign course')
    instructor.radio(key='navigation').set_value('Check-ins').run()
    instructor.selectbox(key=f'decision_{checkid}').set_value('request re-check')
    instructor.text_area(key=f'notes_{checkid}').set_value('Please redo the live check-in')
    instructor.checkbox(key=f'review_confirm_{checkid}').check()
    for button in instructor.button:
        if  button.label == 'Submit review':
            button.click().run()
            break
    no_crash(instructor, 'request re-check')
    assert client.get_checkin(checkid)['status'] == 'pending'
    passed('Dashboard request re-check saves pending status in backend')
    student = APIClient(base)
    student.login('student@review.example.com', 'ReviewPassword123!')
    result = student.post('/api/v1/checkins/', {'session_id': sid, 'latitude': 1.3483,
                          'longitude': 103.6831, 'device_fingerprint': 'review-student-device'})
    assert result['id'] == checkid and result['status'] == 'approved'
    assert len(client.get_session_checkins(sid)) == 1
    assert client.get_session_stats(sid)['approved_count'] == 1
    records = list(csv.DictReader(io.StringIO(client.export_session(sid).decode())))
    assert len(records) == 1 and records[0]['status'] == 'approved'
    passed('Re-check API resubmission updates same record, count and CSV consistently (no face required in fixture)')
    admin = dashboard('admin')
    admin.radio(key='navigation').set_value('Audit logs').run()
    no_crash(admin, 'administrator audit logs')
    assert any(d.label == 'Download displayed audit page CSV' for d in admin.get('download_button'))
    passed('Administrator audit explorer renders populated events and CSV download')
    form = dashboard('instructor')
    form.radio(key='navigation').set_value('Sessions').run()
    form.text_input(key='create_name').set_value('Created through dashboard')
    next(button for button in form.button if button.label == 'Create session').click().run()
    no_crash(form, 'session creation')
    created = next(row for row in client.get_all_sessions() if row['name'] == 'Created through dashboard')
    assert created['status'] == 'scheduled'
    passed('Dashboard form creates a scheduled session through real API')
    created_id = created['id']
    form.selectbox(key='selected_session').set_value(created_id).run()
    form.text_input(key=f'edit_name_{created_id}').set_value('Renamed through dashboard')
    next(button for button in form.button if button.label == 'Save session changes').click().run()
    no_crash(form, 'session rename')
    assert client.get_session(created_id)['name'] == 'Renamed through dashboard'
    passed('Dashboard edit form persists renamed session and preserves selection')
    form.checkbox(key=f'confirm_status_{created_id}').check()
    next(button for button in form.button if button.label == 'Update session status').click().run()
    no_crash(form, 'session activation')
    assert client.get_session(created_id)['status'] == 'active'
    passed('Dashboard confirmed status action activates saved session')
    with app.state.session_factory() as db:
        checkin = db.get(CheckIn, checkid)
        checkin.status = 'flagged'
        db.commit()
    review = dashboard('instructor')
    review.radio(key='navigation').set_value('Check-ins').run()
    review.checkbox(key='checkins_review_only').check().run()
    no_crash(review, 'review-only filter')
    assert review.selectbox(key='checkins_status').disabled
    review.selectbox(key=f'decision_{checkid}').set_value('approved')
    review.text_area(key=f'notes_{checkid}').set_value('Checked the evidence and approved attendance')
    review.checkbox(key=f'review_confirm_{checkid}').check()
    next(button for button in review.button if button.label == 'Submit review').click().run()
    no_crash(review, 'manual approval')
    assert client.get_checkin(checkid)['status'] == 'approved'
    assert client.get_session_stats(sid)['approved_count'] == 1
    passed('Review-only filter and manual approval update attendance')
    review = dashboard('instructor')
    review.radio(key='navigation').set_value('Sessions').run()
    review.selectbox(key='selected_session').set_value(sid).run()
    next(button for button in review.button if button.label == 'Prepare Session CSV').click().run()
    no_crash(review, 'prepare CSV')
    prepared = review.session_state['prepared_export']
    records = list(csv.DictReader(io.StringIO(prepared['data'].decode())))
    assert len(records) == 1 and records[0]['status'] == 'approved'
    assert any(d.label == 'Download Session CSV' for d in review.get('download_button'))
    passed('Dashboard prepare/download contains saved approved attendance')

    def fixture_window(status="active", timing="open", *, course_active=True, checkin_status="flagged"):
        current = utcnow()
        with app.state.session_factory() as db:
            item = db.get(Session, sid)
            item.status = status
            item.checkin_opens_at  = current + timedelta(minutes=5) if timing == "future"  else current - timedelta(minutes=10)
            item.checkin_closes_at = current - timedelta(minutes=1) if timing == "expired" else current + timedelta(minutes=30)
            db.get(Course, cid).is_active = course_active
            record = db.get(CheckIn, checkid)
            record.status = checkin_status
            record.review_notes = None
            db.commit()

    for name, status, timing, active in [
        ("closed session",    "closed",    "open",    True),
        ("cancelled session", "cancelled", "open",    True),
        ("scheduled session", "scheduled", "open",    True),
        ("future window",     "active",    "future",  True),
        ("expired window",    "active",    "expired", True),
        ("inactive course",   "active",    "open",    False),
    ]:
        fixture_window(status, timing, course_active=active)
        guard = dashboard('instructor')
        guard.radio(key='navigation').set_value('Check-ins').run()
        no_crash(guard, name)
        options = guard.selectbox(key=f'decision_{checkid}').options
        assert 'Request re-check' not in options
        assert options == ['Approved', 'Rejected']
        if  name == 'closed session':
            guard.selectbox(key=f'decision_{checkid}').set_value('approved')
            guard.text_area(key=f'notes_{checkid}').set_value('Attendance confirmed after the class ended')
            guard.checkbox(key=f'review_confirm_{checkid}').check()
            next(b for b in guard.button if b.label == 'Submit review').click().run()
            no_crash(guard, 'closed session manual approval')
            assert client.get_checkin(checkid)['status'] == 'approved'
        passed(f'Re-check unavailable for {name}; approval/rejection remain offered')

    fixture_window()
    stale = dashboard('instructor')
    stale.radio(key='navigation').set_value('Check-ins').run()
    stale.selectbox(key=f'decision_{checkid}').set_value('request re-check')
    stale.text_area(key=f'notes_{checkid}').set_value('Please repeat while the window is open')
    stale.checkbox(key=f'review_confirm_{checkid}').check()
    fixture_window(timing='expired')
    next(b for b in stale.button if b.label == 'Submit review').click().run()
    no_crash(stale, 'expired selection')
    assert client.get_checkin(checkid)['status'] == 'flagged'
    assert stale.selectbox(key=f'decision_{checkid}').value is None
    assert not stale.checkbox(key=f'review_confirm_{checkid}').value
    passed('Expired re-check selection clears decision/confirmation and cannot become approval')

    fixture_window()
    race = dashboard('instructor')
    race.radio(key='navigation').set_value('Check-ins').run()
    race.selectbox(key=f'decision_{checkid}').set_value('request re-check')
    race.text_area(key=f'notes_{checkid}').set_value('Re-check request near the closing time')
    race.checkbox(key=f'review_confirm_{checkid}').check()
    original_get = race.session_state['api'].get_session
    reads = [0]
    def close_during_submit(session_id):
        reads[0] += 1
        if  reads[0] == 2:
            fixture_window(timing='expired')
        return original_get(session_id)
    race.session_state['api'].get_session = close_during_submit
    next(b for b in race.button if b.label == 'Submit review').click().run()
    no_crash(race, 'submission preflight')
    assert reads[0] == 2
    assert client.get_checkin(checkid)['status'] == 'flagged'
    assert any('window has closed' in item.value for item in race.warning)
    passed('Submit-time preflight prevents re-check if the window closes between reads')

    fixture_window('closed', checkin_status='pending')
    pending = dashboard('instructor')
    pending.radio(key='navigation').set_value('Check-ins').run()
    no_crash(pending, 'stranded pending record')
    assert any('no longer open' in item.value for item in pending.warning)
    passed('Existing pending record in a closed session shows an actionable warning')

    print(f'SUMMARY: {len(checks)} dashboard checks passed', flush=True)
finally:
    server.should_exit = True
    thread.join(timeout=10)
    temporary.cleanup()
