"""Integration tests for course, session, and enrollment APIs."""

from datetime import timedelta
import uuid

from sqlalchemy import select

from app.database import utcnow
from app.models import AuditLog, Course, Enrollment, Session


def _create_course(client, headers, suffix="101"):
    response = client.post(
        "/api/v1/courses/",
        headers=headers,
        json={
            "code": f"TDD{suffix}",
            "name": "Reliable Attendance",
            "semester": "AY2026-27 Sem 1",
            "venue_name": "TR+1",
            "venue_latitude": 1.3483,
            "venue_longitude": 103.6831,
            "geofence_radius_meters": 75,
            "risk_threshold": 0.4,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _assign_staff(client, admin_headers, course_id, *user_ids):
    response = client.put(
        f"/api/v1/courses/{course_id}/staff",
        headers=admin_headers,
        json={"user_ids": list(user_ids)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_course_crud_staff_assignment_and_soft_delete(client, db, make_user):
    admin, admin_headers = make_user(role="admin")
    instructor, instructor_headers = make_user(role="instructor")
    course = _create_course(client, admin_headers)

    staff = _assign_staff(client, admin_headers, course["id"], instructor.id)
    assert staff["course_id"] == course["id"]
    assert [member["id"] for member in staff["staff"]] == [instructor.id]
    repeated = _assign_staff(client, admin_headers, course["id"], instructor.id)
    assert [member["id"] for member in repeated["staff"]] == [instructor.id]

    updated = client.put(
        f"/api/v1/courses/{course['id']}",
        headers=instructor_headers,
        json={"name": "Reliable Attendance II", "risk_threshold": 0.65},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Reliable Attendance II"
    assert updated.json()["risk_threshold"] == 0.65

    deleted = client.delete(f"/api/v1/courses/{course['id']}", headers=admin_headers)
    assert deleted.status_code == 204
    assert db.get(Course, course["id"]).is_active is False

    actions = set(
        db.scalars(
            select(AuditLog.action).where(AuditLog.resource_id == course["id"])
        ).all()
    )
    assert {"course_created", "course_staff_updated", "course_updated", "course_deleted"} <= actions


def test_course_input_constraints_and_staff_authorization(client, make_user):
    admin, admin_headers = make_user(role="admin")
    outsider, outsider_headers = make_user(role="instructor")
    course = _create_course(client, admin_headers, "102")

    duplicate = client.post(
        "/api/v1/courses/",
        headers=admin_headers,
        json={"code": course["code"].lower(), "name": "Duplicate", "semester": "S1"},
    )
    assert duplicate.status_code == 400

    forbidden = client.put(
        f"/api/v1/courses/{course['id']}",
        headers=outsider_headers,
        json={"name": "Taken Over"},
    )
    assert forbidden.status_code == 403

    invalid = client.put(
        f"/api/v1/courses/{course['id']}",
        headers=admin_headers,
        json={"geofence_radius_meters": 0, "risk_threshold": 1.1},
    )
    assert invalid.status_code == 422
    assert client.put(
        f"/api/v1/courses/{course['id']}",
        headers=admin_headers,
        json={"name": None},
    ).status_code == 422


def test_public_course_listing_is_bounded_and_hides_security_metadata(client, make_user):
    _, admin_headers = make_user(role="admin")
    _create_course(client, admin_headers, "103")

    response = client.get("/api/v1/courses/", params={"limit": 1})
    assert response.status_code == 200, response.text
    assert response.json()["limit"] == 1
    assert len(response.json()["items"]) == 1
    assert "risk_threshold" not in response.json()["items"][0]
    assert client.get("/api/v1/courses/", params={"limit": 101}).status_code == 422

    authenticated = client.get("/api/v1/courses/", headers=admin_headers)
    assert authenticated.status_code == 200
    assert authenticated.json()["items"][0]["risk_threshold"] == 0.4


def test_session_defaults_access_and_state_transitions(client, db, make_user):
    _, admin_headers = make_user(role="admin")
    instructor, instructor_headers = make_user(role="instructor")
    student, student_headers = make_user(role="student")
    course = _create_course(client, admin_headers, "104")
    _assign_staff(client, admin_headers, course["id"], instructor.id)
    start = utcnow() + timedelta(hours=2)

    created = client.post(
        "/api/v1/sessions/",
        headers=instructor_headers,
        json={
            "course_id": course["id"],
            "name": "Lecture 1",
            "session_type": "lecture",
            "scheduled_start": start.isoformat(),
            "scheduled_end": (start + timedelta(hours=1)).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "scheduled"
    assert body["instructor_id"] == instructor.id
    assert body["venue_name"] == "TR+1"
    assert body["geofence_radius_meters"] == 75

    preview = client.get(f"/api/v1/sessions/{body['id']}", headers=student_headers)
    assert preview.status_code == 200
    assert "qr_code_secret" not in preview.json()

    activated = client.patch(
        f"/api/v1/sessions/{body['id']}",
        headers=instructor_headers,
        json={"status": "active"},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["actual_start"] is not None

    closed = client.patch(
        f"/api/v1/sessions/{body['id']}",
        headers=instructor_headers,
        json={"status": "closed"},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["actual_end"] is not None
    assert client.patch(
        f"/api/v1/sessions/{body['id']}",
        headers=instructor_headers,
        json={"status": "active"},
    ).status_code == 400
    assert db.get(Session, body["id"]).status == "closed"


def test_session_rejects_bad_windows_and_only_scheduled_delete(client, db, make_user):
    _, admin_headers = make_user(role="admin")
    instructor, instructor_headers = make_user(role="instructor")
    course = _create_course(client, admin_headers, "105")
    _assign_staff(client, admin_headers, course["id"], instructor.id)
    start = utcnow() + timedelta(hours=1)

    invalid = client.post(
        "/api/v1/sessions/",
        headers=instructor_headers,
        json={
            "course_id": course["id"],
            "name": "Impossible Window",
            "scheduled_start": start.isoformat(),
            "scheduled_end": (start - timedelta(minutes=1)).isoformat(),
        },
    )
    assert invalid.status_code == 400

    valid = client.post(
        "/api/v1/sessions/",
        headers=instructor_headers,
        json={
            "course_id": course["id"],
            "name": "Deletable",
            "scheduled_start": start.isoformat(),
            "scheduled_end": (start + timedelta(hours=1)).isoformat(),
        },
    ).json()
    assert client.patch(
        f"/api/v1/sessions/{valid['id']}",
        headers=instructor_headers,
        json={"name": None},
    ).status_code == 422
    assert client.delete(
        f"/api/v1/sessions/{valid['id']}", headers=instructor_headers
    ).status_code == 204
    assert db.get(Session, valid["id"]) is None

    assert client.delete(
        f"/api/v1/courses/{course['id']}", headers=admin_headers
    ).status_code == 204
    inactive = client.post(
        "/api/v1/sessions/",
        headers=instructor_headers,
        json={
            "course_id": course["id"],
            "name": "Inactive Course Session",
            "scheduled_start": start.isoformat(),
            "scheduled_end": (start + timedelta(hours=1)).isoformat(),
        },
    )
    assert inactive.status_code == 400


def test_enrollment_crud_reactivation_and_course_listing(client, db, make_user):
    _, admin_headers = make_user(role="admin")
    instructor, instructor_headers = make_user(role="instructor")
    student, student_headers = make_user(role="student")
    course = _create_course(client, admin_headers, "106")
    _assign_staff(client, admin_headers, course["id"], instructor.id)

    created = client.post(
        "/api/v1/enrollments/",
        headers=instructor_headers,
        json={"student_id": student.id, "course_id": course["id"]},
    )
    assert created.status_code == 201, created.text
    enrollment_id = created.json()["id"]

    mine = client.get("/api/v1/enrollments/my-enrollments", headers=student_headers)
    assert mine.status_code == 200
    assert [row["course_id"] for row in mine.json()] == [course["id"]]

    roster = client.get(
        f"/api/v1/enrollments/course/{course['id']}", headers=instructor_headers
    )
    assert roster.status_code == 200
    assert roster.json()["total_enrolled"] == 1
    assert roster.json()["students"][0]["student_email"] == student.email

    assert client.delete(
        f"/api/v1/enrollments/{enrollment_id}", headers=instructor_headers
    ).status_code == 204
    dropped = db.get(Enrollment, enrollment_id)
    assert dropped.is_active is False
    assert dropped.dropped_at is not None

    restored = client.post(
        "/api/v1/enrollments/",
        headers=instructor_headers,
        json={"student_id": student.id, "course_id": course["id"]},
    )
    assert restored.status_code == 201
    assert restored.json()["id"] == enrollment_id
    db.expire_all()
    assert db.get(Enrollment, enrollment_id).dropped_at is None


def test_bulk_enrollment_reports_each_email_without_creating_accounts(client, make_user):
    _, admin_headers = make_user(role="admin")
    instructor, instructor_headers = make_user(role="instructor")
    student, _ = make_user(role="student")
    course = _create_course(client, admin_headers, "107")
    _assign_staff(client, admin_headers, course["id"], instructor.id)

    response = client.post(
        "/api/v1/enrollments/bulk",
        headers=instructor_headers,
        json={
            "course_id": course["id"],
            "student_emails": [student.email.upper(), "missing-person@example.com"],
            "create_accounts": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["enrolled"] == 1
    assert response.json()["not_found"] == 1
    assert [row["status"] for row in response.json()["details"]] == [
        "enrolled",
        "not_found",
    ]


def test_students_cannot_manage_academic_records(client, make_user):
    _, admin_headers = make_user(role="admin")
    student, student_headers = make_user(role="student")
    course = _create_course(client, admin_headers, "108")
    start = utcnow() + timedelta(hours=1)

    assert client.post(
        "/api/v1/sessions/",
        headers=student_headers,
        json={
            "course_id": course["id"],
            "name": "Unauthorized",
            "scheduled_start": start.isoformat(),
            "scheduled_end": (start + timedelta(hours=1)).isoformat(),
        },
    ).status_code == 403
    assert client.post(
        "/api/v1/enrollments/",
        headers=student_headers,
        json={"student_id": student.id, "course_id": course["id"]},
    ).status_code == 403


def test_public_test_mode_fixture_instructor_can_create_session(compat_client):
    def register(role):
        email = f"{role}-{uuid.uuid4().hex}@example.com"
        password = "Password123!"
        created = compat_client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": password,
                "full_name": f"Fixture {role.title()}",
                "role": role,
            },
        )
        assert created.status_code == 201, created.text
        token = compat_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        ).json()["access_token"]
        return created.json(), {"Authorization": f"Bearer {token}"}

    _, admin_headers = register("admin")
    _, instructor_headers = register("instructor")
    course = _create_course(compat_client, admin_headers, uuid.uuid4().hex[:5])
    start = utcnow() + timedelta(minutes=5)
    response = compat_client.post(
        "/api/v1/sessions/",
        headers=instructor_headers,
        json={
            "course_id": course["id"],
            "name": "Fixture Session",
            "scheduled_start": start.isoformat(),
            "scheduled_end": (start + timedelta(hours=1)).isoformat(),
            "checkin_opens_at": (start - timedelta(minutes=15)).isoformat(),
            "checkin_closes_at": (start + timedelta(minutes=30)).isoformat(),
        },
    )
    assert response.status_code == 201, response.text
