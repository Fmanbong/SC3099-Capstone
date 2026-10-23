import base64
import uuid
from datetime import datetime, timedelta

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from sqlalchemy import select

from app.database import utcnow
from app.models import Course, Device, DeviceChallenge, Enrollment, Session, User
from app.services.checkins import decide_status, face_match_uncertain, verify_and_consume_device_proof


IMAGE = "aGVsbG8="


def attendance_setup(db, student, instructor=None, **course_overrides):
    now = utcnow()
    values = {
        "code": f"C{uuid.uuid4().hex[:8]}",
        "name": "Secure Attendance",
        "semester": "2026-S1",
        "venue_latitude": 1.3483,
        "venue_longitude": 103.6831,
        "geofence_radius_meters": 100,
        "require_device_binding": True,
    }
    values.update(course_overrides)
    course = Course(**values)
    db.add(course)
    db.flush()
    session = Session(
        course_id=course.id,
        instructor_id=instructor.id if instructor else None,
        name="Lecture",
        scheduled_start=now - timedelta(minutes=5),
        scheduled_end=now + timedelta(minutes=55),
        checkin_opens_at=now - timedelta(minutes=15),
        checkin_closes_at=now + timedelta(minutes=30),
        status="active",
        require_liveness_check=True,
    )
    db.add_all([session, Enrollment(student_id=student.id, course_id=course.id, is_active=True)])
    db.commit()
    return course, session


def test_normal_mode_rejects_missing_required_image(client, db, make_user):
    student, headers = make_user(camera_consent=True, geolocation_consent=True)
    _, session = attendance_setup(db, student, require_device_binding=False)

    response = client.post(
        "/api/v1/checkins/",
        headers=headers,
        json={
            "session_id": session.id,
            "latitude": 1.3483,
            "longitude": 103.6831,
            "device_fingerprint": f"device-{uuid.uuid4().hex}",
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "CHECKIN_IMAGE_REQUIRED"


def test_checkin_uses_dedicated_ten_per_minute_limit(client, db, make_user):
    student, headers = make_user(camera_consent=True, geolocation_consent=True)
    _, session = attendance_setup(db, student, require_device_binding=False)
    calls = []

    class Limiter:
        def check(self, identifier, limit, window):
            calls.append((identifier, limit, window))

        def close(self):
            pass

    client.app.state.limiter = Limiter()
    client.post(
        "/api/v1/checkins/",
        headers=headers,
        json={
            "session_id": session.id,
            "latitude": 1.3483,
            "longitude": 103.6831,
            "device_fingerprint": f"device-{uuid.uuid4().hex}",
        },
    )

    assert (f"checkin:{student.id}", 10, 60) in calls


def test_unsigned_required_device_is_flagged_and_not_trusted(client, db, make_user):
    student, headers = make_user(camera_consent=True, geolocation_consent=True)
    _, session = attendance_setup(db, student)
    fingerprint = f"device-{uuid.uuid4().hex}"

    registered = client.post(
        "/api/v1/devices/register",
        headers=headers,
        json={"device_fingerprint": fingerprint, "device_name": "Browser", "platform": "web"},
    )
    assert registered.status_code == 201
    assert registered.json()["is_trusted"] is False

    session.require_liveness_check = False
    db.commit()
    response = client.post(
        "/api/v1/checkins/",
        headers=headers,
        json={
            "session_id": session.id,
            "latitude": 1.3483,
            "longitude": 103.6831,
            "device_fingerprint": fingerprint,
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "flagged"
    assert any(item["type"] == "device_proof_missing" for item in response.json()["risk_factors"])
    devices = client.get("/api/v1/devices/my-devices", headers=headers).json()
    assert devices[0]["is_trusted"] is False


def test_more_than_twice_geofence_radius_is_rejected(client, db, make_user):
    student, headers = make_user(camera_consent=True, geolocation_consent=True)
    _, session = attendance_setup(db, student, require_device_binding=False)
    session.require_liveness_check = False
    db.commit()

    response = client.post(
        "/api/v1/checkins/",
        headers=headers,
        json={
            "session_id": session.id,
            "latitude": 1.36,
            "longitude": 103.6831,
            "device_fingerprint": f"device-{uuid.uuid4().hex}",
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "rejected"
    assert response.json()["distance_from_venue_meters"] > 200


def test_optional_absent_biometrics_do_not_call_unavailable_service(client, db, make_user):
    student, headers = make_user(camera_consent=True, geolocation_consent=True)
    _, session = attendance_setup(db, student, require_device_binding=False)
    session.require_liveness_check = False
    db.commit()

    response = client.post(
        "/api/v1/checkins/",
        headers=headers,
        json={
            "session_id": session.id,
            "latitude": 1.3483,
            "longitude": 103.6831,
            "device_fingerprint": f"device-{uuid.uuid4().hex}",
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "approved"
    assert response.json()["liveness_passed"] is None
    deletion_at = datetime.fromisoformat(response.json()["scheduled_deletion_at"])
    remaining = deletion_at - utcnow()
    assert timedelta(days=29, hours=23) < remaining <= timedelta(days=30)


def test_required_liveness_service_outage_is_503(client, db, make_user):
    student, headers = make_user(camera_consent=True, geolocation_consent=True)
    _, session = attendance_setup(db, student, require_device_binding=False)

    response = client.post(
        "/api/v1/checkins/",
        headers=headers,
        json={
            "session_id": session.id,
            "latitude": 1.3483,
            "longitude": 103.6831,
            "device_fingerprint": f"device-{uuid.uuid4().hex}",
            "liveness_challenge_response": IMAGE,
        },
    )

    assert response.status_code == 503
    assert response.json()["code"] == "FACE_SERVICE_UNAVAILABLE"


def test_public_test_mode_records_missing_evidence_without_fabricating_pass(compat_client):
    email = f"{uuid.uuid4().hex}@example.com"
    password = "Password123!"
    assert compat_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Fixture Student", "role": "student"},
    ).status_code == 201
    token = compat_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    with compat_client.app.state.session_factory() as test_db:
        test_db.info["settings"] = compat_client.app.state.settings
        student = test_db.scalar(select(User).where(User.email == email))
        _, session = attendance_setup(test_db, student)
        session_id = session.id

    response = compat_client.post(
        "/api/v1/checkins/",
        headers=headers,
        json={
            "session_id": session_id,
            "latitude": 1.3483,
            "longitude": 103.6831,
            "device_fingerprint": f"device-{uuid.uuid4().hex}",
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "flagged"
    assert response.json()["liveness_passed"] is None
    assert any(item["type"] == "required_image_missing" for item in response.json()["risk_factors"])


def test_ecdsa_nonce_is_bound_to_user_device_session_and_consumed_once(client, db, make_user):
    student, headers = make_user(camera_consent=True, geolocation_consent=True)
    _, session = attendance_setup(db, student)
    session.require_liveness_check = False
    db.commit()
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    fingerprint = f"device-{uuid.uuid4().hex}"
    device = client.post(
        "/api/v1/devices/register",
        headers=headers,
        json={"device_fingerprint": fingerprint, "platform": "web", "public_key": public_key},
    ).json()
    challenge = client.post(
        f"/api/v1/devices/{device['id']}/challenge",
        headers=headers,
        json={"session_id": session.id},
    )
    assert challenge.status_code == 201
    der_signature = private_key.sign(challenge.json()["nonce"].encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_signature)
    signature = base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()

    response = client.post(
        "/api/v1/checkins/",
        headers=headers,
        json={
            "session_id": session.id,
            "latitude": 1.3483,
            "longitude": 103.6831,
            "device_fingerprint": fingerprint,
            "device_challenge_id": challenge.json()["id"],
            "device_signature": signature,
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "approved"
    db.expire_all()
    stored_challenge = db.get(DeviceChallenge, challenge.json()["id"])
    stored_device = db.get(Device, device["id"])
    assert stored_challenge.consumed_at is not None
    verified_again, reason = verify_and_consume_device_proof(
        db,
        device=stored_device,
        user_id=student.id,
        session_id=session.id,
        challenge_id=stored_challenge.id,
        signature=signature,
    )
    assert verified_again is False
    assert reason == "device_challenge_invalid"
    second = client.post(
        f"/api/v1/devices/{device['id']}/challenge",
        headers=headers,
        json={"session_id": str(uuid.uuid4())},
    )
    assert second.status_code == 404


def test_rsa_nonce_signature_satisfies_device_binding(client, db, make_user):
    student, headers = make_user(camera_consent=True, geolocation_consent=True)
    _, session = attendance_setup(db, student)
    session.require_liveness_check = False
    db.commit()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    fingerprint = f"device-{uuid.uuid4().hex}"
    device = client.post(
        "/api/v1/devices/register",
        headers=headers,
        json={"device_fingerprint": fingerprint, "platform": "web", "public_key": public_key},
    ).json()
    challenge = client.post(
        f"/api/v1/devices/{device['id']}/challenge", headers=headers, json={"session_id": session.id}
    ).json()
    signature = base64.b64encode(
        private_key.sign(challenge["nonce"].encode(), padding.PKCS1v15(), hashes.SHA256())
    ).decode()

    response = client.post(
        "/api/v1/checkins/",
        headers=headers,
        json={
            "session_id": session.id,
            "latitude": 1.3483,
            "longitude": 103.6831,
            "device_fingerprint": fingerprint,
            "device_challenge_id": challenge["id"],
            "device_signature": signature,
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "approved"


def test_flagged_queue_appeal_review_and_visibility(client, db, make_user):
    student, student_headers = make_user(camera_consent=True, geolocation_consent=True)
    instructor, instructor_headers = make_user(role="instructor")
    _, session = attendance_setup(db, student, instructor)
    session.require_liveness_check = False
    db.commit()
    checkin = client.post(
        "/api/v1/checkins/",
        headers=student_headers,
        json={
            "session_id": session.id,
            "latitude": 1.3483,
            "longitude": 103.6831,
            "device_fingerprint": f"device-{uuid.uuid4().hex}",
        },
    ).json()

    appeal = client.post(
        f"/api/v1/checkins/{checkin['id']}/appeal",
        headers=student_headers,
        json={"appeal_reason": "My browser could not access its signing key during class."},
    )
    assert appeal.status_code == 200
    assert appeal.json()["status"] == "appealed"
    queue = client.get("/api/v1/checkins/flagged?limit=1&offset=0", headers=instructor_headers)
    assert queue.status_code == 200
    assert queue.json()["total"] == 1
    assert queue.json()["items"][0]["status"] == "appealed"

    review = client.post(
        f"/api/v1/checkins/{checkin['id']}/review",
        headers=instructor_headers,
        json={"status": "approved", "review_notes": "Attendance independently confirmed."},
    )
    assert review.status_code == 200
    assert review.json()["status"] == "approved"
    assert review.json()["reviewed_by_id"] == instructor.id
    assert client.get(f"/api/v1/checkins/{checkin['id']}", headers=student_headers).status_code == 200
    assert len(client.get("/api/v1/checkins/my-checkins", headers=student_headers).json()) == 1


def test_reverification_lets_student_redo_flagged_checkin_once(client, db, make_user):
    student, student_headers = make_user(camera_consent=True, geolocation_consent=True)
    instructor, instructor_headers = make_user(role="instructor")
    _, session = attendance_setup(db, student, instructor)
    session.require_liveness_check = False
    db.commit()
    body = {"session_id": session.id, "latitude": 1.3483, "longitude": 103.6831,
            "device_fingerprint": f"device-{uuid.uuid4().hex}"}
    first = client.post("/api/v1/checkins/", headers=student_headers, json=body).json()
    assert first["status"] == "flagged"  # unsigned device on a device-bound course

    # Students cannot send their own check-in back, and a normal resubmission is refused.
    assert client.post(f"/api/v1/checkins/{first['id']}/reverify", headers=student_headers,
                       json={"review_notes": "x"}).status_code == 403
    assert client.post("/api/v1/checkins/", headers=student_headers, json=body).status_code == 400

    sent_back = client.post(f"/api/v1/checkins/{first['id']}/reverify", headers=instructor_headers,
                            json={"review_notes": "Face match uncertain, please redo."})
    assert sent_back.status_code == 200
    assert sent_back.json()["status"] == "pending"

    redo = client.post("/api/v1/checkins/", headers=student_headers, json=body)
    assert redo.status_code == 201
    assert redo.json()["id"] == first["id"]  # same record, fresh evidence
    assert redo.json()["status"] == "flagged"
    assert redo.json()["reviewed_by_id"] is None
    # Exactly one redo: the check-in is no longer pending.
    assert client.post("/api/v1/checkins/", headers=student_headers, json=body).status_code == 400
    assert len(client.get("/api/v1/checkins/my-checkins", headers=student_headers).json()) == 1


def test_reverification_only_applies_to_checkins_awaiting_review(client, db, make_user):
    student, student_headers = make_user(camera_consent=True, geolocation_consent=True)
    instructor, instructor_headers = make_user(role="instructor")
    _, session = attendance_setup(db, student, instructor)
    session.require_liveness_check = False
    db.commit()
    checkin = client.post("/api/v1/checkins/", headers=student_headers, json={
        "session_id": session.id, "latitude": 1.3483, "longitude": 103.6831,
        "device_fingerprint": f"device-{uuid.uuid4().hex}"}).json()
    client.post(f"/api/v1/checkins/{checkin['id']}/review", headers=instructor_headers,
                json={"status": "approved", "review_notes": "Confirmed in class."})
    again = client.post(f"/api/v1/checkins/{checkin['id']}/reverify", headers=instructor_headers,
                        json={"review_notes": "Second thoughts."})
    assert again.status_code == 400
    assert again.json()["code"] == "REVERIFY_NOT_ALLOWED"


def _face(score, passed=True, threshold=0.85):
    return {"match_passed": passed, "match_score": score, "match_threshold": threshold}


def _status(**overrides):
    values = dict(risk_score=0.1, risk_threshold=0.5, distance=10.0, radius=100.0,
                  liveness_required=False, liveness_passed=None, face_required=True,
                  face_passed=True, device_required=False, device_verified=True)
    values.update(overrides)
    return decide_status(**values)


def test_face_match_just_above_threshold_is_flagged_for_review():
    # 0.856 is a measured different-person score that passed the 0.85 threshold.
    assert face_match_uncertain(_face(0.856))
    assert _status(face_uncertain=face_match_uncertain(_face(0.856))) == "flagged"


def test_clear_face_match_is_still_approved():
    assert not face_match_uncertain(_face(0.918))
    assert _status(face_uncertain=face_match_uncertain(_face(0.918))) == "approved"


def test_failed_face_match_is_rejected_not_flagged():
    assert not face_match_uncertain(_face(0.77, passed=False))
    assert _status(face_passed=False, face_uncertain=False) == "rejected"


def test_review_band_follows_face_service_threshold():
    assert face_match_uncertain(_face(0.93, threshold=0.90))
    assert not face_match_uncertain(_face(0.93, threshold=0.85))
    assert not face_match_uncertain(None)
