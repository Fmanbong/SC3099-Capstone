
""" THIS IS TO TEST DASHBOARD WORKFLOWS AGAINST AN ISOLATED TEMP BACKEND
"""


import csv , io , os , sys , requests , socket , tempfile , threading , time , uvicorn
from datetime import timedelta ; from pathlib import Path

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
from streamlit.testing.v1.element_tree import Radio, Selectbox


def serialized_index(widget):
    metadata = widget.root.session_state._state._new_widget_state.widget_metadata.get(widget.id)
    if  metadata:
        return metadata.serializer(widget.value)
    return widget.options.index(str(widget.value)) if widget.value is not None else None
Selectbox.index = property(serialized_index)
Radio.index     = property(serialized_index)

temporary = tempfile.TemporaryDirectory(prefix='saiv-review-')
settings  = Settings(
    database_url=f'sqlite:///{temporary.name}/dashboard.db', secret_key='review-only-secret-at-least-32-characters', environment='test',
    public_test_mode=False, auto_create_schema=True, rate_limit_enabled=False, retention_enabled=False
)

app  = create_app(settings)
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
port = sock.getsockname()[1]
sock.close()
base   = f'http://127.0.0.1:{port}'
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
     course = Course(code='REVIEW1', name='Review course', semester='2026', require_face_recognition=False, require_device_binding=False,
                     venue_latitude=1.3483, venue_longitude=103.6831)
     foreign_course = Course(code='OTHER1', name='Other staff course', semester='2026')
     db.add_all([course, foreign_course]) ; db.flush() ; db.add(CourseStaff(course_id=course.id, user_id=users['instructor'].id))
     db.add(Enrollment(course_id=course.id, student_id=users['student'].id, enrolled_at=now - timedelta(days=1)))

     session = Session(course_id=course.id, instructor_id=users['instructor'].id, name='Review active session', session_type='lecture', status='active',
                       scheduled_start=now - timedelta(minutes=5), scheduled_end=now + timedelta(hours=1),
                       checkin_opens_at=now - timedelta(minutes=10), checkin_closes_at=now + timedelta(minutes=30), require_liveness_check=False, require_face_match=False)

     foreign = Session(course_id=foreign_course.id, instructor_id=users['admin'].id, name='Foreign session', session_type='lecture', status='active',
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
    print('PASS:', message.upper(), flush=True)

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
    passed('STUDENT LOGIN CANNOT ENTER DASHBOARD AND CLEARS CREDENTIALS')
    instructor = dashboard('instructor')
    for page in ['Overview', 'Sessions', 'Check-ins', 'Audit logs', 'Metrics']:
        instructor.radio(key='navigation').set_value(page).run()
        no_crash(instructor, f'INSTRUCTOR {page}')
        passed(f'INSTRUCTOR {page} RENDERS WITH POPULATED BACKEND RECORDS')
    instructor.radio(key='navigation').set_value('Audit logs').run()
    assert any('restricted to administrators' in e.value.lower() for e in instructor.info)
    passed('AUDIT LOGS PAGE REFUSES INSTRUCTOR ACCESS')
    instructor.radio(key='navigation').set_value('Overview').run()
    instructor.button(key='overview_review_queue').click().run()
    no_crash(instructor, 'overview review shortcut')
    assert instructor.radio(key='navigation').value == 'Check-ins'
    assert instructor.checkbox(key='checkins_review_only').value
    assert instructor.selectbox(key='checkins_status').disabled
    passed('OVERVIEW SHORTCUT OPENS THE REVIEW-ONLY QUEUE WITHOUT WIDGET STATE ERRORS')
    client = instructor.session_state['api']
    ids = {row['id'] for row in client.get_all_sessions()}
    assert sid in ids and fid not in ids
    try:
        client.get_session(fid)
        raise AssertionError('FOREIGN SESSION READABLE')
    except APIError as exc:
        assert exc.status == 403
    passed('INSTRUCTOR SESSION SELECTOR AND DIRECT API RESTRICT FOREIGN COURSE')

    try:
        client.get_audit_logs()
        raise AssertionError('INSTRUCTOR COULD READ AUDIT LOGS')
    except APIError as exc:
        assert exc.status == 403
    passed('AUDIT API REFUSES INSTRUCTOR ACCESS')

    course_view = dashboard('instructor')
    course_view.checkbox(key='show_course_stats').check().run()
    no_crash(course_view, 'course attendance')
    assert not course_view.error
    assert course_view.selectbox(key='overview_course').value == cid
    course_view.button(key='prepare_course').click().run()
    no_crash(course_view, 'course CSV')
    assert course_view.session_state['prepared_export']['key'][-1] == cid
    assert any(d.label == 'DOWNLOAD COURSE CSV' for d in course_view.get('download_button'))
    passed('AUTHORIZED COURSE ATTENDANCE AND COURSE CSV RENDER')

    filters_view = dashboard('instructor')
    filters_view.radio(key='navigation').set_value('Check-ins').run()
    filters_view.number_input(key='checkins_page').set_value(2).run()
    assert filters_view.number_input(key='checkins_page').value == 2
    filters_view.checkbox(key='checkins_review_only').check().run()
    no_crash(filters_view, 'review filter resets page')
    assert filters_view.number_input(key='checkins_page').value == 1
    assert filters_view.selectbox(key='checkins_status').disabled
    filters_view.checkbox(key='checkins_review_only').uncheck().run()
    assert not filters_view.selectbox(key='checkins_status').disabled
    passed('REVIEW FILTER RESETS PAGINATION AND RESTORES STATUS CONTROL')

    filters_view.checkbox(key='checkins_use_dates').check().run()
    end_day = filters_view.date_input(key='checkins_end').value
    filters_view.date_input(key='checkins_start').set_value(end_day + timedelta(days=1)).run()
    no_crash(filters_view, 'invalid check-in date range')
    assert any('end date must not precede' in item.value.lower() for item in filters_view.error)
    passed('INVALID CHECK-IN DATE RANGE SHOWS VALIDATION WITHOUT CRASHING')

    filters_view.checkbox(key='checkins_use_dates').uncheck().run()
    filters_view.checkbox(key='checkins_use_risk').check().run()
    filters_view.slider(key='checkins_min_risk').set_value(1.0).run()
    no_crash(filters_view, 'empty risk filter')
    assert not filters_view.error
    assert not any(widget.key == 'selected_checkin' for widget in filters_view.selectbox)
    passed('EMPTY FILTER RESULT RENDERS WITHOUT A RECORD SELECTOR')

    validation = dashboard('instructor')
    validation.radio(key='navigation').set_value('Sessions').run()
    next(button for button in validation.button if button.label == 'CREATE SESSION').click().run()
    no_crash(validation, 'empty session name')
    assert any('session name is required' in item.value.lower() for item in validation.error)
    assert len(client.get_all_sessions()) == 1
    passed('EMPTY SESSION NAME CANNOT CREATE A RECORD')

    instructor.radio(key='navigation').set_value('Check-ins').run()
    instructor.selectbox(key=f'decision_{checkid}').set_value('REQUEST RE-CHECK')
    instructor.text_area(key=f'notes_{checkid}').set_value('PLEASE REDO THE LIVE CHECK-IN')
    instructor.checkbox(key=f'review_confirm_{checkid}').check()
    for button in instructor.button:
        if  button.label == 'SUBMIT REVIEW':
            button.click().run()
            break
    no_crash(instructor, 'REQUEST RE-CHECK')
    assert client.get_checkin(checkid)['status'] == 'pending'
    passed('DASHBOARD REQUEST RE-CHECK SAVES PENDING STATUS IN BACKEND')
    student = APIClient(base)
    student.login('student@review.example.com', 'ReviewPassword123!')
    result  = student.post('/api/v1/checkins/', {'session_id': sid, 'latitude': 1.3483, 'longitude': 103.6831, 'device_fingerprint': 'review-student-device'})
    assert result['id'] == checkid and result['status'] == 'approved'
    assert len(client.get_session_checkins(sid)) == 1
    assert client.get_session_stats(sid)['approved_count'] == 1
    records = list(csv.DictReader(io.StringIO(client.export_session(sid).decode())))
    assert len(records) == 1 and records[0]['status'] == 'approved'
    passed('RE-CHECK API RESUBMISSION UPDATES SAME RECORD, COUNT AND CSV CONSISTENTLY (NO FACE REQUIRED IN FIXTURE)')
    admin   = dashboard('admin')
    admin.radio(key='navigation').set_value('Audit logs').run()
    no_crash(admin, 'administrator audit logs')
    assert any(d.label == 'DOWNLOAD DISPLAYED AUDIT PAGE CSV' for d in admin.get('download_button'))
    passed('ADMINISTRATOR AUDIT EXPLORER RENDERS POPULATED EVENTS AND CSV DOWNLOAD')
    admin.text_input(key='audit_user_id').set_value('invalid-uuid').run()
    no_crash(admin, 'invalid audit UUID')
    assert any('valid uuid' in item.value.lower() for item in admin.error)
    passed('INVALID AUDIT UUID SHOWS VALIDATION WITHOUT REQUESTING A RECORD')
    form    = dashboard('instructor')
    form.radio(key='navigation').set_value('Sessions').run()
    form.text_input(key='create_name').set_value('Created through dashboard')
    next(button for button in form.button if button.label == 'CREATE SESSION').click().run()
    no_crash(form, 'session creation')
    created = next(row for row in client.get_all_sessions() if row['name'] == 'Created through dashboard')
    assert created['status'] == 'scheduled'
    passed('DASHBOARD FORM CREATES A SCHEDULED SESSION THROUGH REAL API')
    created_id = created['id']
    form.selectbox(key='selected_session').set_value(created_id).run()
    form.text_input(key=f'edit_name_{created_id}').set_value('Renamed through dashboard')
    next(button for button in form.button if button.label == 'SAVE SESSION CHANGES').click().run()
    no_crash(form, 'session rename')
    assert client.get_session(created_id)['name'] == 'Renamed through dashboard'
    passed('DASHBOARD EDIT FORM PERSISTS RENAMED SESSION AND PRESERVES SELECTION')
    next(button for button in form.button if button.label == 'UPDATE SESSION STATUS').click().run()
    no_crash(form, 'unconfirmed status change')
    assert client.get_session(created_id)['status'] == 'scheduled'
    assert any('confirm the status change' in item.value.lower() for item in form.error)
    passed('UNCONFIRMED STATUS CHANGE DOES NOT MODIFY THE SESSION')
    form.checkbox(key=f'confirm_status_{created_id}').check()
    next(button for button in form.button if button.label == 'UPDATE SESSION STATUS').click().run()
    no_crash(form, 'session activation')
    assert client.get_session(created_id)['status'] == 'active'
    passed('DASHBOARD CONFIRMED STATUS ACTION ACTIVATES SAVED SESSION')
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
    review.text_area(key=f'notes_{checkid}').set_value('Checked the evidence and approved attendance') ; review.checkbox(key=f'review_confirm_{checkid}').check()
    next(button for button in review.button if button.label == 'SUBMIT REVIEW').click().run()
    no_crash(review, 'manual approval')
    assert client.get_checkin(checkid)['status'] == 'approved'
    assert client.get_session_stats(sid)['approved_count'] == 1
    passed('REVIEW-ONLY FILTER AND MANUAL APPROVAL UPDATE ATTENDANCE')
    review = dashboard('instructor')
    review.radio(key='navigation').set_value('Sessions').run()
    review.selectbox(key='selected_session').set_value(sid).run()
    next(button for button in review.button if button.label == 'PREPARE SESSION CSV').click().run()
    no_crash(review, 'prepare CSV')
    prepared = review.session_state['prepared_export']
    records  = list(csv.DictReader(io.StringIO(prepared['data'].decode())))
    assert len(records) == 1 and records[0]['status'] == 'approved'
    assert any(d.label == 'DOWNLOAD SESSION CSV' for d in review.get('download_button'))
    passed('DASHBOARD PREPARE/DOWNLOAD CONTAINS SAVED APPROVED ATTENDANCE')

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
        options = [value.upper() for value in guard.selectbox(key=f'decision_{checkid}').options]
        assert 'REQUEST RE-CHECK' not in options
        assert options == ['APPROVED', 'REJECTED']
        if  name == 'closed session':
            guard.selectbox(key=f'decision_{checkid}').set_value('approved')
            guard.text_area(key=f'notes_{checkid}').set_value('Attendance confirmed after the class ended')
            guard.checkbox(key=f'review_confirm_{checkid}').check()
            next(b for b in guard.button if b.label == 'SUBMIT REVIEW').click().run()
            no_crash(guard, 'closed session manual approval')
            assert client.get_checkin(checkid)['status'] == 'approved'
        passed(f'RE-CHECK UNAVAILABLE FOR {name}; APPROVAL/REJECTION REMAIN OFFERED')

    fixture_window()
    stale = dashboard('instructor')
    stale.radio(key='navigation').set_value('Check-ins').run()
    stale.selectbox(key=f'decision_{checkid}').set_value('REQUEST RE-CHECK')
    stale.text_area(key=f'notes_{checkid}').set_value('Please repeat while the window is open') ; stale.checkbox(key=f'review_confirm_{checkid}').check()
    fixture_window(timing='expired')
    next(b for b in stale.button if b.label == 'SUBMIT REVIEW').click().run()
    no_crash(stale, 'expired selection')
    assert client.get_checkin(checkid)['status'] == 'flagged'
    assert stale.selectbox(key=f'decision_{checkid}').value is None
    assert not stale.checkbox(key=f'review_confirm_{checkid}').value
    passed('EXPIRED RE-CHECK SELECTION CLEARS DECISION/CONFIRMATION AND CANNOT BECOME APPROVAL')

    fixture_window()
    race = dashboard('instructor')
    race.radio(key='navigation').set_value('Check-ins').run()
    race.selectbox(key=f'decision_{checkid}').set_value('REQUEST RE-CHECK')
    race.text_area(key=f'notes_{checkid}').set_value('Re-check request near the closing time') ; race.checkbox(key=f'review_confirm_{checkid}').check()
    original_get = race.session_state['api'].get_session
    reads = [0]
    def close_during_submit(session_id):
        reads[0] += 1
        if  reads[0] == 2:
            fixture_window(timing='expired')
        return original_get(session_id)
    race.session_state['api'].get_session = close_during_submit
    next(b for b in race.button if b.label == 'SUBMIT REVIEW').click().run()
    no_crash(race, 'submission preflight')
    assert reads[0] == 2
    assert client.get_checkin(checkid)['status'] == 'flagged'
    assert any('window has closed' in item.value.lower() for item in race.warning)
    passed('SUBMIT-TIME PREFLIGHT PREVENTS RE-CHECK IF THE WINDOW CLOSES BETWEEN READS')

    fixture_window('closed', checkin_status='pending')
    pending = dashboard('instructor')
    pending.radio(key='navigation').set_value('Check-ins').run()
    no_crash(pending, 'stranded pending record')
    assert any('no longer open' in item.value.lower() for item in pending.warning)
    passed('EXISTING PENDING RECORD IN A CLOSED SESSION SHOWS AN ACTIONABLE WARNING')

    fixture_window()
    rejected = dashboard('instructor')
    rejected.radio(key='navigation').set_value('Check-ins').run()
    rejected.selectbox(key=f'decision_{checkid}').set_value('rejected')
    rejected.text_area(key=f'notes_{checkid}').set_value('Evidence does not meet attendance requirements')
    next(b for b in rejected.button if b.label == 'SUBMIT REVIEW').click().run()
    no_crash(rejected, 'unconfirmed review')
    assert client.get_checkin(checkid)['status'] == 'flagged'
    assert any('confirm the decision' in item.value.lower() for item in rejected.error)
    passed('UNCONFIRMED REVIEW DOES NOT MODIFY ATTENDANCE')
    rejected.checkbox(key=f'review_confirm_{checkid}').check()
    next(b for b in rejected.button if b.label == 'SUBMIT REVIEW').click().run()
    no_crash(rejected, 'manual rejection')
    assert client.get_checkin(checkid)['status'] == 'rejected'
    assert client.get_session_stats(sid)['approved_count'] == 0
    records = list(csv.DictReader(io.StringIO(client.export_session(sid).decode())))
    assert len(records) == 1 and records[0]['status'] == 'rejected'
    passed('MANUAL REJECTION UPDATES SAVED STATUS, COUNTS AND CSV')

    signout = dashboard('instructor')
    for button in signout.button:
        if  button.label == 'SIGN OUT':
            button.click().run()
            break
    no_crash(signout, 'sign out')
    assert not signout.session_state['api'].token
    assert signout.text_input(key='login_email')
    assert 'prepared_export' not in signout.session_state
    passed('SIGN OUT RETURNS TO LOGIN AND CLEARS AUTHENTICATION AND EXPORTS')
    print(f'SUMMARY: {len(checks)} DASHBOARD CHECKS PASSED', flush=True)
finally:
    server.should_exit = True
    thread.join(timeout=10)
    temporary.cleanup()
