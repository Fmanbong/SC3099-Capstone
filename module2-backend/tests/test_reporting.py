from datetime import timedelta

import pytest
from sqlalchemy import select

from app.database import Base, build_database
from app.database import utcnow
from app.models import (
    AuditLog,
    CheckIn,
    Course,
    CourseStaff,
    Device,
    DeviceChallenge,
    Enrollment,
    EnrollmentPeriod,
    RefreshSession,
    RiskSignal,
    Session,
    User,
)


@pytest.fixture
def report_db():
    engine, session_factory = build_database("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def make_report_user(report_db):
    counter = 0

    def make(role="student", **overrides):
        nonlocal counter
        counter += 1
        user = User(
            email=overrides.pop("email", f"report-{counter}@example.com"),
            full_name=overrides.pop("full_name", f"Report User {counter}"),
            hashed_password="unused-in-service-tests",
            role=role,
            **overrides,
        )
        report_db.add(user)
        report_db.flush()
        return user

    return make


def _course(db, suffix="A"):
    course = Course(
        code=f"REP{suffix}",
        name=f"Reporting {suffix}",
        semester="2026/27 S1",
    )
    db.add(course)
    db.flush()
    return course


def _session(db, course, instructor, *, name="Lecture 1", start=None):
    start = start or (utcnow() - timedelta(hours=1))
    session = Session(
        course_id=course.id,
        instructor_id=instructor.id,
        name=name,
        scheduled_start=start,
        scheduled_end=start + timedelta(hours=1),
        checkin_opens_at=start - timedelta(minutes=15),
        checkin_closes_at=start + timedelta(minutes=30),
        status="closed",
    )
    db.add(session)
    db.flush()
    return session


def test_session_statistics_counts_only_approved_attendance(report_db, make_report_user):
    from app.services.reporting import session_statistics

    db = report_db
    instructor = make_report_user(role="instructor")
    approved_student = make_report_user()
    flagged_student = make_report_user()
    absent_student = make_report_user()
    course = _course(db)
    for student in (approved_student, flagged_student, absent_student):
        db.add(Enrollment(student_id=student.id, course_id=course.id, is_active=True,
                          enrolled_at=utcnow() - timedelta(days=60)))
    session = _session(db, course, instructor)
    db.add_all([
        CheckIn(
            session_id=session.id,
            student_id=approved_student.id,
            status="approved",
            risk_score=0.1,
            checked_in_at=session.scheduled_start + timedelta(minutes=2),
        ),
        CheckIn(
            session_id=session.id,
            student_id=flagged_student.id,
            status="flagged",
            risk_score=0.6,
            checked_in_at=session.scheduled_start + timedelta(minutes=7),
        ),
    ])
    db.flush()

    result = session_statistics(db, session.id)

    assert result["total_enrolled"] == 3
    assert result["checked_in"] == 2
    assert result["checked_in_count"] == 2
    assert result["approved_count"] == 1
    assert result["flagged_count"] == 1
    assert result["attendance_rate"] == 1 / 3
    assert result["risk_distribution"] == {"low": 1, "medium": 0, "high": 1}


def test_session_export_includes_absent_students_and_neutralizes_csv_formulas(
    report_db, make_report_user
):
    from app.services.reporting import attendance_csv, session_attendance_export

    db = report_db
    instructor = make_report_user(role="instructor")
    present = make_report_user(full_name="Present Student")
    absent = make_report_user(full_name="=HYPERLINK(\"https://bad.example\")")
    course = _course(db, "B")
    db.add_all([
        Enrollment(student_id=present.id, course_id=course.id, is_active=True,
                   enrolled_at=utcnow() - timedelta(days=60)),
        Enrollment(student_id=absent.id, course_id=course.id, is_active=True,
                   enrolled_at=utcnow() - timedelta(days=60)),
    ])
    session = _session(db, course, instructor)
    db.add(CheckIn(
        session_id=session.id,
        student_id=present.id,
        status="approved",
        risk_score=0.2,
        checked_in_at=session.scheduled_start + timedelta(minutes=1),
    ))
    db.flush()

    payload = session_attendance_export(db, session.id)

    assert payload["summary"] == {
        "total_enrolled": 2,
        "checked_in": 1,
        "approved": 1,
        "absent": 1,
        "attendance_rate": 0.5,
    }
    assert {record["student_name"]: record["status"] for record in payload["records"]} == {
        "Present Student": "approved",
        '=HYPERLINK("https://bad.example")': "absent",
    }
    csv_text = attendance_csv(payload["records"])
    assert "'=HYPERLINK" in csv_text
    assert "\r\n" in csv_text
    payload["records"][0]["student_name"] = "\tunsafe"
    assert "'\tunsafe" in attendance_csv(payload["records"])


def test_overview_statistics_respect_visible_course_scope_and_aliases(
    report_db, make_report_user
):
    from app.services.reporting import overview_statistics

    db = report_db
    instructor = make_report_user(role="instructor")
    first = make_report_user()
    second = make_report_user()
    outside = make_report_user()
    visible_course = _course(db, "C")
    hidden_course = _course(db, "D")
    db.add_all([
        Enrollment(student_id=first.id, course_id=visible_course.id, is_active=True,
                   enrolled_at=utcnow() - timedelta(days=60)),
        Enrollment(student_id=second.id, course_id=visible_course.id, is_active=True,
                   enrolled_at=utcnow() - timedelta(days=60)),
        Enrollment(student_id=outside.id, course_id=hidden_course.id, is_active=True,
                   enrolled_at=utcnow() - timedelta(days=60)),
    ])
    visible_session = _session(db, visible_course, instructor)
    hidden_session = _session(db, hidden_course, instructor, name="Hidden")
    visible_session.status = "active"
    db.add_all([
        CheckIn(session_id=visible_session.id, student_id=first.id, status="approved",
                risk_score=0.1, checked_in_at=utcnow()),
        CheckIn(session_id=visible_session.id, student_id=second.id, status="flagged",
                risk_score=0.8, checked_in_at=utcnow()),
        CheckIn(session_id=hidden_session.id, student_id=outside.id, status="approved",
                risk_score=0.0, checked_in_at=utcnow()),
    ])
    db.flush()

    result = overview_statistics(db, [visible_course.id], days=7)

    assert result["total_courses"] == 1
    assert result["total_students"] == 2
    assert result["total_sessions"] == 1
    assert result["active_sessions"] == 1
    assert result["total_checkins_today"] == result["today_checkins"] == 2
    assert result["flagged_pending_review"] == result["flagged_pending"] == 1
    assert result["approval_rate"] == 0.5
    assert result["average_attendance_rate"] == 0.5
    assert result["high_risk_checkins_today"] == 1


def test_course_statistics_apply_date_range_and_approved_only_attendance(
    report_db, make_report_user
):
    from app.services.reporting import course_statistics

    db = report_db
    instructor = make_report_user(role="instructor")
    present = make_report_user(full_name="Present")
    flagged = make_report_user(full_name="Flagged")
    course = _course(db, "E")
    db.add_all([
        Enrollment(student_id=present.id, course_id=course.id, is_active=True,
                   enrolled_at=utcnow() - timedelta(days=60)),
        Enrollment(student_id=flagged.id, course_id=course.id, is_active=True,
                   enrolled_at=utcnow() - timedelta(days=60)),
    ])
    recent = _session(db, course, instructor, start=utcnow() - timedelta(hours=2))
    old = _session(db, course, instructor, name="Old", start=utcnow() - timedelta(days=30))
    db.add_all([
        CheckIn(session_id=recent.id, student_id=present.id, status="approved", risk_score=0.2),
        CheckIn(session_id=recent.id, student_id=flagged.id, status="flagged", risk_score=0.6),
        CheckIn(session_id=old.id, student_id=present.id, status="approved", risk_score=0.1),
    ])
    db.flush()

    result = course_statistics(
        db,
        course.id,
        start_date=utcnow() - timedelta(days=1),
        end_date=utcnow() + timedelta(days=1),
    )

    assert result["total_sessions"] == 1
    assert result["overall_attendance_rate"] == result["average_attendance_rate"] == 0.5
    assert result["flagged_checkins"] == 1
    by_student = {item["student_name"]: item for item in result["student_attendance"]}
    assert by_student["Present"]["sessions_attended"] == 1
    assert by_student["Flagged"]["sessions_attended"] == 0
    assert result["low_attendance_alerts"][0]["student_name"] == "Flagged"


def test_student_statistics_use_visible_enrollments_and_public_aliases(
    report_db, make_report_user
):
    from app.services.reporting import student_statistics

    db = report_db
    instructor = make_report_user(role="instructor")
    student = make_report_user(full_name="Student Summary")
    visible = _course(db, "F")
    hidden = _course(db, "G")
    db.add_all([
        Enrollment(student_id=student.id, course_id=visible.id, is_active=True,
                   enrolled_at=utcnow() - timedelta(days=60)),
        Enrollment(student_id=student.id, course_id=hidden.id, is_active=True,
                   enrolled_at=utcnow() - timedelta(days=60)),
    ])
    attended = _session(db, visible, instructor, name="Attended")
    _session(db, visible, instructor, name="Missed")
    hidden_session = _session(db, hidden, instructor, name="Hidden")
    db.add_all([
        CheckIn(session_id=attended.id, student_id=student.id, status="approved", risk_score=0.2),
        CheckIn(session_id=hidden_session.id, student_id=student.id, status="approved", risk_score=0.1),
    ])
    db.flush()

    result = student_statistics(db, student.id, [visible.id])

    assert result["total_enrolled_courses"] == 1
    assert result["total_sessions"] == 2
    assert result["attended_sessions"] == 1
    assert result["attendance_rate"] == 0.5
    assert len(result["courses"]) == 1
    assert result["courses"][0]["course_id"] == visible.id
    assert result["recent_sessions"] == result["recent_checkins"]


def test_course_export_has_one_record_for_every_student_session_pair(
    report_db, make_report_user
):
    from app.services.reporting import course_attendance_export

    db = report_db
    instructor = make_report_user(role="instructor")
    first = make_report_user()
    second = make_report_user()
    course = _course(db, "H")
    db.add_all([
        Enrollment(student_id=first.id, course_id=course.id, is_active=True,
                   enrolled_at=utcnow() - timedelta(days=60)),
        Enrollment(student_id=second.id, course_id=course.id, is_active=True,
                   enrolled_at=utcnow() - timedelta(days=60)),
    ])
    first_session = _session(db, course, instructor, name="First")
    _session(db, course, instructor, name="Second")
    db.add(CheckIn(session_id=first_session.id, student_id=first.id,
                   status="approved", risk_score=0.1))
    db.flush()

    payload = course_attendance_export(db, course.id)

    assert len(payload["records"]) == 4
    assert payload["summary"] == {
        "total_enrolled": 2,
        "total_sessions": 2,
        "total_records": 4,
        "checked_in": 1,
        "approved": 1,
        "absent": 3,
        "attendance_rate": 0.25,
    }


def test_course_export_default_end_date_includes_rest_of_today(report_db, make_report_user):
    from app.services.reporting import course_attendance_export

    db = report_db
    instructor = make_report_user(role="instructor")
    student = make_report_user()
    course = _course(db, "L")
    db.add(Enrollment(student_id=student.id, course_id=course.id, is_active=True,
                      enrolled_at=utcnow() - timedelta(days=60)))
    _session(db, course, instructor, start=utcnow() + timedelta(hours=1))
    db.flush()

    payload = course_attendance_export(db, course.id)

    assert payload["summary"]["total_sessions"] == 1
    assert payload["records"][0]["status"] == "absent"


def test_historical_rosters_keep_dropped_students_and_exclude_later_enrollments(
    report_db, make_report_user
):
    from app.services.reporting import (
        course_attendance_export,
        course_statistics,
        session_attendance_export,
    )

    db = report_db
    instructor = make_report_user(role="instructor")
    dropped = make_report_user(full_name="Dropped Later")
    enrolled_later = make_report_user(full_name="Enrolled Later")
    evidence = make_report_user(full_name="Historical Evidence")
    course = _course(db, "N")
    session_start = utcnow() - timedelta(days=10)
    session = _session(db, course, instructor, start=session_start)
    db.add_all([
        Enrollment(
            student_id=dropped.id,
            course_id=course.id,
            is_active=False,
            enrolled_at=session_start - timedelta(days=10),
            dropped_at=session_start + timedelta(days=1),
        ),
        Enrollment(
            student_id=enrolled_later.id,
            course_id=course.id,
            is_active=True,
            enrolled_at=session_start + timedelta(days=1),
        ),
        # A reactivated enrollment loses its first membership interval. The
        # check-in remains authoritative evidence for that historical session.
        Enrollment(
            student_id=evidence.id,
            course_id=course.id,
            is_active=True,
            enrolled_at=session_start + timedelta(days=1),
        ),
    ])
    db.add_all([
        CheckIn(session_id=session.id, student_id=dropped.id,
                status="approved", risk_score=0.1),
        CheckIn(session_id=session.id, student_id=evidence.id,
                status="approved", risk_score=0.2),
    ])
    db.flush()

    session_payload = session_attendance_export(db, session.id)
    course_payload = course_attendance_export(db, course.id)
    stats = course_statistics(db, course.id)

    assert {row["student_name"] for row in session_payload["records"]} == {
        "Dropped Later", "Historical Evidence"
    }
    assert {row["student_name"] for row in course_payload["records"]} == {
        "Dropped Later", "Historical Evidence"
    }
    assert session_payload["summary"]["total_enrolled"] == 2
    assert course_payload["summary"]["total_records"] == 2
    assert stats["sessions"][0]["attendance_rate"] == 1.0
    assert {row["student_name"] for row in stats["student_attendance"]} == {
        "Dropped Later", "Historical Evidence"
    }


def test_reactivated_enrollment_uses_archived_periods_without_filling_drop_gap(
    report_db, make_report_user
):
    from app.services.reporting import (
        course_attendance_export,
        course_statistics,
        student_statistics,
    )

    db = report_db
    instructor = make_report_user(role="instructor")
    student = make_report_user(full_name="Rejoined Student")
    course = _course(db, "O")
    old_start = utcnow() - timedelta(days=30)
    gap_start = utcnow() - timedelta(days=20)
    rejoin_start = utcnow() - timedelta(days=5)
    current_start = utcnow() - timedelta(days=1)
    old_session = _session(db, course, instructor, name="Old Membership", start=old_start)
    _session(db, course, instructor, name="Dropped Gap", start=gap_start)
    current_session = _session(db, course, instructor, name="Current Membership", start=current_start)
    enrollment = Enrollment(
        student_id=student.id,
        course_id=course.id,
        is_active=True,
        enrolled_at=rejoin_start,
    )
    db.add(enrollment)
    db.flush()
    db.add(EnrollmentPeriod(
        enrollment_id=enrollment.id,
        started_at=old_start - timedelta(days=5),
        ended_at=old_start + timedelta(days=5),
    ))
    db.flush()

    payload = course_attendance_export(db, course.id)
    stats = course_statistics(db, course.id)
    student_stats = student_statistics(db, student.id, [course.id])

    assert {row["session_id"] for row in payload["records"]} == {
        old_session.id, current_session.id
    }
    assert all(row["status"] == "absent" for row in payload["records"])
    assert payload["summary"]["total_records"] == 2
    assert stats["student_attendance"][0]["attendance_rate"] == 0
    assert stats["low_attendance_alerts"][0]["sessions_missed"] == 2
    assert student_stats["total_sessions"] == 2
    assert student_stats["courses"][0]["total_sessions"] == 2


def test_audit_routes_filter_paginate_summarize_and_redact_secrets(client, db, make_user):
    admin, headers = make_user(role="admin")
    db.add(AuditLog(
        user_id=admin.id,
        action="data_exported",
        resource_type="course",
        resource_id="00000000-0000-0000-0000-000000000001",
        severity="info",
        details={
            "record_count": 2,
            "token": "do-not-return",
            "nested": {
                "face_embedding_hash": "do-not-return-either",
                "student_email": "private@example.com",
            },
        },
    ))
    db.add(AuditLog(action="login_failed", severity="warning", details={}))
    db.commit()

    response = client.get("/api/v1/audit/", params={"action": "data_exported"}, headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["limit"] == 100
    assert payload["items"][0]["action"] == "data_exported"
    assert payload["items"][0]["success"] is True
    assert payload["items"][0]["timestamp"]
    assert "do-not-return" not in response.text
    assert "face_embedding_hash" not in response.text
    assert "private@example.com" not in response.text

    failures = client.get("/api/v1/audit/", params={"success": False}, headers=headers)
    assert failures.status_code == 200
    assert [item["action"] for item in failures.json()["items"]] == ["login_failed"]
    assert failures.json()["items"][0]["success"] is False

    summary = client.get("/api/v1/audit/summary", params={"days": 7}, headers=headers)
    assert summary.status_code == 200
    assert summary.json()["period_days"] == 7
    assert summary.json()["by_action"]["data_exported"] == 1


def test_stats_routes_expose_spec_and_dashboard_aliases(client, db, make_user):
    instructor, headers = make_user(role="instructor")
    student, _ = make_user()
    course = _course(db, "I")
    db.add(CourseStaff(course_id=course.id, user_id=instructor.id))
    db.add(Enrollment(student_id=student.id, course_id=course.id, is_active=True,
                      enrolled_at=utcnow() - timedelta(days=60)))
    session = _session(db, course, instructor)
    db.add(CheckIn(session_id=session.id, student_id=student.id,
                   status="approved", risk_score=0.1))
    db.commit()

    overview = client.get("/api/v1/stats/overview", headers=headers)
    session_response = client.get(f"/api/v1/stats/sessions/{session.id}", headers=headers)
    course_response = client.get(f"/api/v1/stats/courses/{course.id}", headers=headers)
    student_response = client.get(f"/api/v1/stats/students/{student.id}", headers=headers)

    assert overview.status_code == 200
    assert overview.json()["today_checkins"] == overview.json()["total_checkins_today"]
    assert session_response.status_code == 200
    assert session_response.json()["checked_in_count"] == 1
    assert course_response.status_code == 200
    assert "average_attendance_rate" in course_response.json()
    assert student_response.status_code == 200
    assert student_response.json()["attended_sessions"] == 1


def test_student_stats_authorization_accepts_historical_scoped_enrollment(
    client, db, make_user
):
    instructor, headers = make_user(role="instructor")
    student, _ = make_user()
    course = _course(db, "P")
    db.add(CourseStaff(course_id=course.id, user_id=instructor.id))
    db.add(Enrollment(
        student_id=student.id,
        course_id=course.id,
        is_active=False,
        enrolled_at=utcnow() - timedelta(days=10),
        dropped_at=utcnow() - timedelta(days=1),
    ))
    db.commit()

    response = client.get(f"/api/v1/stats/students/{student.id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["total_enrolled_courses"] == 1


def test_export_routes_return_json_and_csv_and_append_audit_events(client, db, make_user):
    instructor, headers = make_user(role="instructor")
    student, _ = make_user()
    course = _course(db, "J")
    db.add(CourseStaff(course_id=course.id, user_id=instructor.id))
    db.add(Enrollment(student_id=student.id, course_id=course.id, is_active=True,
                      enrolled_at=utcnow() - timedelta(days=60)))
    session = _session(db, course, instructor)
    db.commit()

    json_response = client.get(
        f"/api/v1/export/session/{session.id}", params={"format": "json"}, headers=headers
    )
    csv_response = client.get(
        f"/api/v1/export/attendance/{course.id}", params={"format": "csv"}, headers=headers
    )

    assert json_response.status_code == 200
    assert json_response.json()["records"][0]["status"] == "absent"
    assert json_response.json()["summary"]["attendance_rate"] == 0
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "attachment;" in csv_response.headers["content-disposition"]
    assert "student_id,student_name,student_email" in csv_response.text
    db.expire_all()
    events = db.scalars(select(AuditLog).where(AuditLog.action == "data_exported")).all()
    assert len(events) == 2
    assert all(set(event.details) <= {"format", "record_count"} for event in events)


def test_retention_deletes_expired_records_and_due_users_but_preserves_audit(
    db, make_user
):
    from app.services.retention import cleanup_expired_records

    now = utcnow()
    instructor, _ = make_user(role="instructor")
    retained, _ = make_user()
    due, _ = make_user(scheduled_deletion_at=now - timedelta(seconds=1), is_active=False)
    course = _course(db, "K")
    db.add_all([
        Enrollment(student_id=retained.id, course_id=course.id, is_active=True),
        Enrollment(student_id=due.id, course_id=course.id, is_active=True),
    ])
    session = _session(db, course, instructor)
    expired_checkin = CheckIn(
        session_id=session.id, student_id=retained.id, status="approved", risk_score=0.1,
        scheduled_deletion_at=now - timedelta(seconds=1),
    )
    due_user_checkin = CheckIn(
        session_id=session.id, student_id=due.id, status="flagged", risk_score=0.8,
        scheduled_deletion_at=now + timedelta(days=1),
    )
    db.add_all([expired_checkin, due_user_checkin])
    db.flush()
    db.add_all([
        RiskSignal(checkin_id=expired_checkin.id, signal_type="test", score=0.1, weight=0.1),
        RiskSignal(checkin_id=due_user_checkin.id, signal_type="test", score=0.8, weight=0.5),
        AuditLog(user_id=due.id, action="user_deletion_scheduled", resource_type="user",
                 resource_id=due.id, details={"status": "scheduled"}),
    ])
    db.commit()

    counts = cleanup_expired_records(db, now=now)

    assert counts["checkins_deleted"] == 2
    assert counts["risk_signals_deleted"] == 2
    assert counts["users_deleted"] == 1
    assert db.get(User, due.id) is None
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "user_deletion_scheduled"))
    assert audit is not None
    assert audit.user_id is None
    assert audit.resource_id == due.id
    assert audit.details == {"status": "scheduled"}


def test_retention_removes_expired_ephemeral_rows_but_keeps_unexpired_used_refresh(
    report_db, make_report_user
):
    from app.services.retention import cleanup_expired_records

    db = report_db
    now = utcnow()
    instructor = make_report_user(role="instructor")
    student = make_report_user()
    course = _course(db, "M")
    session = _session(db, course, instructor)
    device = Device(user_id=student.id, device_fingerprint="retention-device")
    db.add(device)
    db.flush()
    db.add_all([
        DeviceChallenge(user_id=student.id, device_id=device.id, session_id=session.id,
                        nonce="expired", expires_at=now - timedelta(seconds=1)),
        DeviceChallenge(user_id=student.id, device_id=device.id, session_id=session.id,
                        nonce="current", expires_at=now + timedelta(hours=1)),
        RefreshSession(user_id=student.id, family_id="expired", token_hash="expired",
                       expires_at=now - timedelta(seconds=1)),
        RefreshSession(user_id=student.id, family_id="used", token_hash="used",
                       expires_at=now + timedelta(hours=1), used_at=now),
    ])
    db.commit()

    counts = cleanup_expired_records(db, now=now)

    assert counts["device_challenges_deleted"] == 1
    assert counts["refresh_sessions_deleted"] == 1
    assert list(db.scalars(select(DeviceChallenge.nonce))) == ["current"]
    assert list(db.scalars(select(RefreshSession.token_hash))) == ["used"]
