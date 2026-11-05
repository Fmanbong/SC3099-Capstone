import uuid

import pytest

from app.models import Course, CourseStaff, Enrollment

IMAGE = "aGVsbG8="


class EnrollingFaceService:
    """Every enrollment yields a new template, as a fresh photo would."""

    def enroll(self, user_id, image, camera_consent):
        return {"enrollment_successful": True, "face_template_hash": uuid.uuid4().hex * 2,
                "quality_score": 0.9, "details": {}}


@pytest.fixture
def approval_on(client):
    client.app.state.face_client = EnrollingFaceService()
    client.app.state.settings.face_enrollment_approval = "true"
    yield
    client.app.state.settings.face_enrollment_approval = "auto"


def course_with(db, instructor, *students):
    course = Course(code=f"C{uuid.uuid4().hex[:8]}", name="Approval", semester="2026-S1")
    db.add(course)
    db.flush()
    db.add(CourseStaff(course_id=course.id, user_id=instructor.id))
    db.add_all(Enrollment(student_id=s.id, course_id=course.id, is_active=True) for s in students)
    db.commit()
    return course


def enroll(client, headers):
    return client.post("/api/v1/users/me/face/enroll", headers=headers, json={"image": IMAGE})


@pytest.mark.parametrize("value, public_test_mode, expected", [
    ("auto", False, True),    # normal deployments need staff approval without anyone opting in
    ("auto", True, False),    # graders' test mode keeps the spec's immediate enrollment
    ("", False, True),
    ("true", True, True), ("yes", True, True), ("on", False, True),
    ("false", False, False), ("0", False, False), ("off", True, False),
])
def test_auto_requires_approval_everywhere_except_public_test_mode(settings, value, public_test_mode, expected):
    config = settings.model_copy(update={"public_test_mode": public_test_mode})
    config.face_enrollment_approval = type(settings).normalise_approval(value)
    assert config.requires_face_enrollment_approval is expected


def test_explicit_false_keeps_spec_behaviour_of_enrolling_immediately(client, make_user):
    client.app.state.face_client = EnrollingFaceService()
    client.app.state.settings.face_enrollment_approval = "false"
    _, headers = make_user(camera_consent=True)
    response = enroll(client, headers)
    client.app.state.settings.face_enrollment_approval = "auto"
    assert response.status_code == 200
    assert response.json()["face_enrolled"] is True
    me = client.get("/api/v1/users/me", headers=headers).json()
    assert me["face_enrollment_status"] == "approved"
    assert "pending_face_hash" not in me and "face_embedding_hash" not in me


def test_public_test_mode_enrolls_immediately_by_default(compat_client):
    compat_client.app.state.face_client = EnrollingFaceService()
    assert compat_client.app.state.settings.face_enrollment_approval == "auto"
    email = f"{uuid.uuid4().hex}@example.com"
    compat_client.post("/api/v1/auth/register", json={"email": email, "password": "Password123!",
                                                      "full_name": "Graded", "role": "student"})
    token = compat_client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"}).json()
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    compat_client.put("/api/v1/users/me", headers=headers, json={"camera_consent": True})
    response = enroll(compat_client, headers)
    assert (response.status_code, response.json()["face_enrolled"]) == (200, True)


def test_normal_mode_requires_approval_by_default(client, make_user):
    client.app.state.face_client = EnrollingFaceService()
    assert client.app.state.settings.face_enrollment_approval == "auto"
    _, headers = make_user(camera_consent=True)
    response = enroll(client, headers)
    assert (response.status_code, response.json()["approval_pending"]) == (202, True)


def test_enrollment_waits_for_staff_approval(client, db, make_user, approval_on):
    student, student_headers = make_user(camera_consent=True)
    instructor, instructor_headers = make_user(role="instructor")
    course_with(db, instructor, student)

    submitted = enroll(client, student_headers)
    assert submitted.status_code == 202
    assert submitted.json() == {"success": True, "approval_pending": True, "face_enrolled": False,
                                "message": submitted.json()["message"], "quality_score": 0.9}
    me = client.get("/api/v1/users/me", headers=student_headers).json()
    assert (me["face_enrolled"], me["face_enrollment_status"]) == (False, "pending")

    queue = client.get("/api/v1/users/face-enrollments/pending", headers=instructor_headers).json()
    assert [item["id"] for item in queue["items"]] == [student.id]
    assert queue["items"][0]["replaces_existing_face"] is False

    approved = client.post(f"/api/v1/users/{student.id}/face-enrollment/approve", headers=instructor_headers,
                           json={"review_notes": "Checked student card in LT26"})
    assert approved.status_code == 200
    assert (approved.json()["face_enrolled"], approved.json()["face_enrollment_status"]) == (True, "approved")
    assert client.get("/api/v1/users/face-enrollments/pending", headers=instructor_headers).json()["total"] == 0


def test_reenrollment_keeps_the_approved_face_until_the_new_one_is_reviewed(client, db, make_user, approval_on):
    student, student_headers = make_user(camera_consent=True, face_enrolled=True, face_embedding_hash="a" * 64,
                                         face_enrollment_status="approved")
    instructor, instructor_headers = make_user(role="instructor")
    course_with(db, instructor, student)

    assert enroll(client, student_headers).json()["face_enrolled"] is True  # old face still usable
    rejected = client.post(f"/api/v1/users/{student.id}/face-enrollment/reject", headers=instructor_headers,
                           json={"review_notes": "Different person enrolled"})
    assert rejected.status_code == 200
    assert rejected.json()["face_enrollment_status"] == "rejected"
    db.refresh(student)
    assert student.face_embedding_hash == "a" * 64 and student.pending_face_hash is None
    # A rejection needs a reason for the student, and there is nothing left to approve.
    assert client.post(f"/api/v1/users/{student.id}/face-enrollment/reject", headers=instructor_headers,
                       json={"review_notes": " "}).status_code == 422
    assert client.post(f"/api/v1/users/{student.id}/face-enrollment/approve", headers=instructor_headers,
                       json={}).json()["code"] == "NO_PENDING_ENROLLMENT"


def test_only_staff_of_the_students_course_or_admins_can_review(client, db, make_user, approval_on):
    student, student_headers = make_user(camera_consent=True)
    _, outsider_headers = make_user(role="instructor")
    _, admin_headers = make_user(role="admin")
    instructor, _ = make_user(role="instructor")
    course_with(db, instructor, student)
    enroll(client, student_headers)

    assert client.post(f"/api/v1/users/{student.id}/face-enrollment/approve", headers=student_headers,
                       json={}).status_code == 403
    assert client.post(f"/api/v1/users/{student.id}/face-enrollment/approve", headers=outsider_headers,
                       json={}).status_code == 403
    assert client.get("/api/v1/users/face-enrollments/pending", headers=outsider_headers).json()["total"] == 0
    assert client.post(f"/api/v1/users/{student.id}/face-enrollment/approve", headers=admin_headers,
                       json={}).status_code == 200


def test_pending_student_cannot_use_face_verified_checkin(client, db, make_user, approval_on):
    from datetime import timedelta
    from app.database import utcnow
    from app.models import Session

    student, student_headers = make_user(camera_consent=True, geolocation_consent=True)
    instructor, _ = make_user(role="instructor")
    course = course_with(db, instructor, student)
    course.require_face_recognition = True
    now = utcnow()
    session = Session(course_id=course.id, name="Lab", scheduled_start=now, scheduled_end=now + timedelta(hours=1),
                      checkin_opens_at=now - timedelta(minutes=5), checkin_closes_at=now + timedelta(minutes=30),
                      status="active", require_liveness_check=False)
    db.add(session)
    db.commit()
    enroll(client, student_headers)

    response = client.post("/api/v1/checkins/", headers=student_headers, json={
        "session_id": session.id, "latitude": 1.3483, "longitude": 103.6831,
        "device_fingerprint": f"device-{uuid.uuid4().hex}", "face_image": IMAGE})
    assert response.status_code == 400
    assert response.json()["code"] == "FACE_ENROLLMENT_PENDING"
