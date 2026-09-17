# Shared implementation interfaces

This is the coordination contract for Module 2. Routers are mounted by main.py at `/api/v1`; each file exports `router = APIRouter(...)`. Shared foundation files are owned by the parent implementer. Domain implementers own only their assigned router/service/test files. No commits, branch changes, shared-file edits or additional subagents.

## Imports

```python
from app.database import utcnow
from app.models import User, Course, CourseStaff, Enrollment, Session, CheckIn, Device, RiskSignal, AuditLog, RefreshSession, DeviceChallenge
from app.dependencies import get_db, current_user, require_roles, get_settings, ensure_course_access, visible_course_ids
from app.errors import ApiError
from app.serializers import user_data, course_data, session_data, checkin_data, device_data, enrollment_data, page
from app.services.audit import audit_event
```

SQLAlchemy synchronous Session; use `select` and `db.scalars(...)`, `db.get(Model, id)`. Relationships are NOT assumed; join explicitly or db.get. UUID identifiers are Python strings; validate path/request IDs with UUID where useful and convert to str. All timestamps loaded from models are UTC aware, including SQLite. `utcnow()` returns UTC aware now.

`get_db(request)` yields a Session and rolls back on errors. It sets `db.info['settings']` to app settings. `current_user` FastAPI dependency authenticates active users. `require_roles('admin', 'instructor')` produces a dependency returning the user. `get_settings(request)` returns app.state.settings. `ensure_course_access(db, user, course_id, *, allow_student=False)` returns Course or raises 403/404. Admin can access all; instructor/TA via CourseStaff or owned Session; student when explicitly allowed and enrolled. `visible_course_ids(db,user)` returns list[str], including all for admin. For isolated public-test-mode only, staff can access all courses to satisfy fixture setup. `settings.public_test_mode` implies `environment == 'test'` and is false by default.

`ApiError(status_code, detail, code)` normalizes errors globally. `audit_event(db, action, user_id=None, resource_type=None, resource_id=None, details=None, request=None, severity='info')` ADDS an append-only row without committing; mutation owners commit business data and audit together. No body, secrets, images or tokens in details.

Serializers accept `(model, db=None)` and return JSON-friendly dicts excluding passwords/face hashes/key material. `page(items,total,limit,offset)` returns the standard wrapper. `session_data`, `checkin_data`, `enrollment_data` enrich course/user names when db provided. They may be extended locally with calculated fields. Request schemas live in their domain router files, share only `app.schemas.CleanModel` (extra='forbid'), `Name` (str validation and HTML escaping), and `Identifier = UUID` if desired.

## Models and attributes

All models have UUID string `id`. Defaults initialize on database flush, so flush before reading generated IDs. Names match DATABASE-SCHEMA.md unless listed below. Timestamps are timezone aware.

- User: email(unique), full_name, hashed_password, role(student/ta/instructor/admin), is_active, camera_consent, geolocation_consent, face_embedding_hash(nullable), face_enrolled, created_at, updated_at, last_login_at, scheduled_deletion_at.
- Course: code(unique), name, description, semester, is_active, venue_latitude, venue_longitude, venue_name, geofence_radius_meters(default100), require_face_recognition(False), require_device_binding(True), risk_threshold(.5), created_at, updated_at.
- CourseStaff: course_id,user_id (unique pair). Users' role determines permitted actions.
- Enrollment: student_id,course_id(unique pair),is_active,enrolled_at,dropped_at.
- EnrollmentPeriod: enrollment_id,started_at,ended_at. Closed intervals are archived before reactivation, preserving historical attendance rosters.
- Session: course_id,instructor_id(nullable),name,session_type,description,scheduled_start,scheduled_end,checkin_opens_at,checkin_closes_at,status(scheduled/active/closed/cancelled),actual_start,actual_end,venue_latitude,venue_longitude,venue_name,geofence_radius_meters(nullable overrides),require_liveness_check(True),require_face_match(False),risk_threshold(nullable),qr_code_secret,qr_code_expires_at,created_at,updated_at. Session deletion may be hard with cascade to children; closing/cancellation normally uses PATCH.
- Device: user_id,device_fingerprint(unique),device_name,platform,browser,os_version,app_version,public_key(nullable),is_trusted(False),trust_score('low'),is_active,first_seen_at,last_seen_at,total_checkins,public_key_created_at,public_key_expires_at,revoked_at,revocation_reason,attestation_passed(False),last_attestation_at,is_emulator(False),is_rooted_jailbroken(False).
- DeviceChallenge: user_id,device_id,session_id,nonce,expires_at,consumed_at(nullable). Challenge endpoints are optional extensions to current frontend; existing unsigned clients may register devices but cannot be marked trusted. Missing device proof may flag, never claim signature validity. Nonce consumption must be atomic.
- CheckIn: session_id,student_id(unique pair),device_id(nullable),status(pending/approved/flagged/rejected/appealed),checked_in_at,verified_at,latitude,longitude,location_accuracy_meters,distance_from_venue_meters,liveness_passed,liveness_score,liveness_challenge_type,face_match_passed,face_match_score,face_embedding_hash(nullable),risk_score,risk_factors(JSON list),qr_code_verified,reviewed_by_id,reviewed_at,review_notes,appeal_reason,appealed_at,scheduled_deletion_at.
- RiskSignal: checkin_id,signal_type,score,weight,details(JSON),created_at.
- AuditLog: user_id(nullable),action,resource_type,resource_id,details(JSON),ip_address,user_agent,severity,created_at. Append-only except controlled retention/delete of expired related PII.
- RefreshSession: user_id,family_id,token_hash(unique),expires_at,created_at,revoked_at,used_at. Access tokens contain sid=family_id; a family must have an active refresh row.

## Face integration

Services access `request.app.state.face_client`, an injectable `FaceServiceClient` whose sync methods are `enroll(user_id,image,camera_consent)`, `verify(image,reference_template_hash)`, `liveness(image,challenge_type='passive')`, `assess(signals: dict)` and `close()`. Parent creates client from Settings.face_service_url and face_timeout_seconds. Use exact origin/arnav service payloads. Safe 400 image errors / 503 service errors; validate upstream outputs; never log raw responses/images. HTTPX MockTransport is acceptable at this external boundary in tests.

## Test setup

Venv: `/private/tmp/saiv-module2-venv`. Local tests in `module2-backend/tests/` (not root public tests). Parent supplies conftest fixtures: `client` TestClient with isolated SQLite and normal security, `compat_client` isolated explicit public-test mode, `db` Session, `make_user(role='student', **overrides)` returning `(User, bearer_headers)`. It seeds via real bcrypt/token creation. Tests for compat_client should register/login through HTTP. App factory `create_app(settings: Settings | None = None, *, face_client=None)`; settings support database_url, secret_key, environment, public_test_mode, rate_limit_enabled, auto_create_schema, redis_url. Normal tests set rate_limit_enabled=False, auto_create_schema=True; dedicated integration tests exercise Redis.

## Policy decisions

- Normal registration only student. Public-test mode permits fixture roles and missing consent/liveness evidence but records missing evidence as flagged; never fabricate passing scores. It is not enabled by production or development defaults.
- For optional absent liveness/face checks, no face service call is necessary. For required missing evidence outside public-test mode return 400; unavailable required verification returns 503. An image with camera_consent false returns 400. Consent checks precede image processing.
- Required false liveness/face result or distance >2x radius => rejected; outside radius, unverified required device, or risk>=threshold => flagged. Risk>=.7 => rejected per security requirements. Otherwise approved. Preserve all evidence reasons.
- Module 3 only computes geo validity and superficial device heuristics; backend independently enforces venue distance and verifies device evidence. Do not trust client-supplied scores, user ID or reference hash.
- First-party frontends currently send unsigned requests. Registration remains compatible; device-bound check-ins without proof are flagged. Document this honest limitation instead of auto-trusting.
- User-supplied partial PUT /users/me remains accepted for compatibility. PATCH aliases may also be added.
- Public-test mode accommodates documented fixture omissions; dedicated normal-mode tests must verify the stricter policies.
