"""Aggregate attendance reporting with approved check-ins as attendance."""
from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable

from sqlalchemy import select

from app.database import utcnow
from app.models import CheckIn, Course, Enrollment, EnrollmentPeriod, Session, User


ATTENDED_STATUS = "approved"
CHECKIN_STATUSES = ("approved", "flagged", "rejected", "pending", "appealed")
ATTENDANCE_CSV_COLUMNS = (
    "student_id",
    "student_name",
    "student_email",
    "session_date",
    "session_name",
    "status",
    "checked_in_at",
    "risk_score",
)


def _mean(values: Iterable[float | None]) -> float:
    present = [float(value) for value in values if value is not None]
    return sum(present) / len(present) if present else 0.0


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _date_start(value: date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _date_end(value: date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.combine(value, time.max, tzinfo=timezone.utc)


def _today_end() -> datetime:
    return datetime.combine(utcnow().date(), time.max, tzinfo=timezone.utc)


def _periods_by_enrollment(db, enrollments: list[Enrollment]) -> dict[str, list[EnrollmentPeriod]]:
    enrollment_ids = [enrollment.id for enrollment in enrollments]
    periods = db.scalars(select(EnrollmentPeriod).where(
        EnrollmentPeriod.enrollment_id.in_(enrollment_ids)
    )).all() if enrollment_ids else []
    grouped: dict[str, list[EnrollmentPeriod]] = {}
    for period in periods:
        grouped.setdefault(period.enrollment_id, []).append(period)
    return grouped


def _session_roster(
    session: Session,
    enrollments: list[Enrollment],
    periods: dict[str, list[EnrollmentPeriod]],
    evidence_student_ids: Iterable[str] = (),
) -> set[str]:
    """Return students enrolled when the session began, plus retained evidence."""
    at = session.scheduled_start
    roster = set(evidence_student_ids)
    for enrollment in enrollments:
        in_current_interval = (
            enrollment.enrolled_at <= at
            and (enrollment.dropped_at is None or enrollment.dropped_at >= at)
        )
        in_archived_interval = any(
            period.started_at <= at <= period.ended_at
            for period in periods.get(enrollment.id, ())
        )
        if in_current_interval or in_archived_interval:
            roster.add(enrollment.student_id)
    return roster


def session_statistics(db, session_id: str) -> dict:
    session = db.get(Session, str(session_id))
    if session is None:
        return {}
    course = db.get(Course, session.course_id)
    checkins = db.scalars(select(CheckIn).where(CheckIn.session_id == session.id)).all()
    enrollments = db.scalars(select(Enrollment).where(
        Enrollment.course_id == session.course_id,
    )).all()
    roster = _session_roster(
        session,
        enrollments,
        _periods_by_enrollment(db, enrollments),
        (checkin.student_id for checkin in checkins),
    )
    total_enrolled = len(roster)
    by_status = {status: 0 for status in CHECKIN_STATUSES}
    for checkin in checkins:
        by_status[checkin.status] = by_status.get(checkin.status, 0) + 1
    approved = by_status[ATTENDED_STATUS]
    risk_distribution = {"low": 0, "medium": 0, "high": 0}
    timeline = Counter()
    elapsed_minutes = []
    for checkin in checkins:
        risk = float(checkin.risk_score or 0.0)
        risk_distribution["low" if risk < 0.3 else "medium" if risk < 0.5 else "high"] += 1
        delta = (checkin.checked_in_at - session.scheduled_start).total_seconds() / 60
        elapsed_minutes.append(delta)
        timeline[max(0, int(delta // 5) * 5)] += 1
    return {
        "session_id": session.id,
        "session_name": session.name,
        "course_code": course.code if course else None,
        "scheduled_start": session.scheduled_start,
        "status": session.status,
        "total_enrolled": total_enrolled,
        "checked_in": len(checkins),
        "checked_in_count": len(checkins),
        "approved_count": approved,
        "flagged_count": by_status["flagged"],
        "rejected_count": by_status["rejected"],
        "pending_count": by_status["pending"],
        "appealed_count": by_status["appealed"],
        "attendance_rate": _rate(approved, total_enrolled),
        "by_status": by_status,
        "average_risk_score": _mean(checkin.risk_score for checkin in checkins),
        "average_distance_meters": _mean(checkin.distance_from_venue_meters for checkin in checkins),
        "average_checkin_time_minutes": _mean(elapsed_minutes),
        "risk_distribution": risk_distribution,
        "checkin_timeline": [
            {"minute": minute, "count": timeline[minute]} for minute in sorted(timeline)
        ],
    }


def overview_statistics(db, course_ids: list[str], *, days: int = 7) -> dict:
    course_ids = [str(course_id) for course_id in course_ids]
    now = utcnow()
    today_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    week_start = now - timedelta(days=7)
    if course_ids:
        courses = db.scalars(select(Course).where(Course.id.in_(course_ids))).all()
        enrollments = db.scalars(select(Enrollment).where(
            Enrollment.course_id.in_(course_ids)
        )).all()
        sessions = db.scalars(select(Session).where(Session.course_id.in_(course_ids))).all()
    else:
        courses, enrollments, sessions = [], [], []
    session_ids = [session.id for session in sessions]
    checkins = db.scalars(select(CheckIn).where(CheckIn.session_id.in_(session_ids))).all() if session_ids else []
    today_checkins = [checkin for checkin in checkins if checkin.checked_in_at >= today_start]
    approved = sum(checkin.status == ATTENDED_STATUS for checkin in checkins)
    flagged_pending = sum(checkin.status in {"flagged", "appealed"} for checkin in checkins)
    periods = _periods_by_enrollment(db, enrollments)
    checkins_by_session: dict[str, list[CheckIn]] = {}
    for checkin in checkins:
        checkins_by_session.setdefault(checkin.session_id, []).append(checkin)
    enrollments_by_course: dict[str, list[Enrollment]] = {}
    for enrollment in enrollments:
        enrollments_by_course.setdefault(enrollment.course_id, []).append(enrollment)
    eligible_sessions = [session for session in sessions if session.status != "cancelled"]
    rosters = {
        session.id: _session_roster(
            session,
            enrollments_by_course.get(session.course_id, []),
            periods,
            (checkin.student_id for checkin in checkins_by_session.get(session.id, [])),
        )
        for session in eligible_sessions
    }
    attendance_denominator = sum(len(rosters[session.id]) for session in eligible_sessions)
    trend_start = today_start - timedelta(days=days - 1)
    trends_checkins = []
    trends_attendance = []
    for offset in range(days):
        day_start = trend_start + timedelta(days=offset)
        day_end = day_start + timedelta(days=1)
        day_checkins = [
            checkin for checkin in checkins if day_start <= checkin.checked_in_at < day_end
        ]
        day_sessions = [
            session for session in eligible_sessions
            if day_start <= session.scheduled_start < day_end
        ]
        day_denominator = sum(len(rosters[session.id]) for session in day_sessions)
        day_session_ids = {session.id for session in day_sessions}
        day_approved = sum(
            checkin.status == ATTENDED_STATUS and checkin.session_id in day_session_ids
            for checkin in checkins
        )
        trends_checkins.append({"date": day_start.date().isoformat(), "count": len(day_checkins)})
        trends_attendance.append({
            "date": day_start.date().isoformat(),
            "rate": _rate(day_approved, day_denominator),
        })
    result = {
        "total_sessions": len(sessions),
        "active_sessions": sum(session.status == "active" for session in sessions),
        "total_courses": len(courses),
        "total_students": len({
            enrollment.student_id for enrollment in enrollments if enrollment.is_active
        }),
        "total_checkins_today": len(today_checkins),
        "total_checkins_week": sum(checkin.checked_in_at >= week_start for checkin in checkins),
        "average_attendance_rate": _rate(approved, attendance_denominator),
        "flagged_pending_review": flagged_pending,
        "approval_rate": _rate(approved, len(checkins)),
        "average_risk_score": _mean(checkin.risk_score for checkin in checkins),
        "high_risk_checkins_today": sum(
            float(checkin.risk_score or 0.0) >= 0.7 for checkin in today_checkins
        ),
        "trends": {
            "checkins_by_day": trends_checkins,
            "attendance_rate_by_day": trends_attendance,
        },
    }
    result["today_checkins"] = result["total_checkins_today"]
    result["flagged_pending"] = result["flagged_pending_review"]
    return result


def course_statistics(
    db,
    course_id: str,
    *,
    start_date: date | datetime | None = None,
    end_date: date | datetime | None = None,
) -> dict:
    course = db.get(Course, str(course_id))
    if course is None:
        return {}
    start = _date_start(start_date)
    end = _date_end(end_date) or _today_end()
    sessions = db.scalars(select(Session).where(
        Session.course_id == course.id,
        Session.status != "cancelled",
        *([Session.scheduled_start >= start] if start else []),
        Session.scheduled_start <= end,
    ).order_by(Session.scheduled_start)).all()
    enrollments = db.scalars(select(Enrollment).where(
        Enrollment.course_id == course.id,
    )).all()
    session_ids = [session.id for session in sessions]
    checkins = db.scalars(select(CheckIn).where(
        CheckIn.session_id.in_(session_ids)
    )).all() if session_ids else []
    periods = _periods_by_enrollment(db, enrollments)
    checkins_by_session: dict[str, list[CheckIn]] = {}
    checkins_by_student: dict[str, list[CheckIn]] = {}
    for checkin in checkins:
        checkins_by_session.setdefault(checkin.session_id, []).append(checkin)
        checkins_by_student.setdefault(checkin.student_id, []).append(checkin)
    rosters = {
        session.id: _session_roster(
            session,
            enrollments,
            periods,
            (checkin.student_id for checkin in checkins_by_session.get(session.id, [])),
        )
        for session in sessions
    }
    roster_student_ids = set().union(*rosters.values()) if rosters else {
        enrollment.student_id for enrollment in enrollments if enrollment.is_active
    }
    students = {
        student.id: student for student in db.scalars(
            select(User).where(User.id.in_(roster_student_ids))
        ).all()
    } if roster_student_ids else {}
    session_rows = []
    for session in sessions:
        session_checkins = checkins_by_session.get(session.id, [])
        approved = sum(checkin.status == ATTENDED_STATUS for checkin in session_checkins)
        session_rows.append({
            "session_id": session.id,
            "name": session.name,
            "date": session.scheduled_start.date().isoformat(),
            "attendance_rate": _rate(approved, len(rosters[session.id])),
            "checked_in": len(session_checkins),
            "approved": approved,
        })
    student_rows = []
    alerts = []
    for student_id in roster_student_ids:
        student = students.get(student_id)
        if student is None:
            continue
        eligible_session_ids = {
            session.id for session in sessions if student.id in rosters[session.id]
        }
        student_checkins = [
            checkin for checkin in checkins_by_student.get(student.id, [])
            if checkin.session_id in eligible_session_ids
        ]
        attended = sum(checkin.status == ATTENDED_STATUS for checkin in student_checkins)
        attendance_rate = _rate(attended, len(eligible_session_ids))
        row = {
            "student_id": student.id,
            "student_name": student.full_name,
            "sessions_attended": attended,
            "attendance_rate": attendance_rate,
            "average_risk_score": _mean(checkin.risk_score for checkin in student_checkins),
        }
        student_rows.append(row)
        if eligible_session_ids and attendance_rate < 0.75:
            alerts.append({
                "student_id": student.id,
                "student_name": student.full_name,
                "attendance_rate": attendance_rate,
                "sessions_missed": len(eligible_session_ids) - attended,
            })
    student_rows.sort(key=lambda row: (row["student_name"].casefold(), row["student_id"]))
    alerts.sort(key=lambda row: (row["attendance_rate"], row["student_name"].casefold()))
    approved_total = sum(checkin.status == ATTENDED_STATUS for checkin in checkins)
    overall_rate = _rate(approved_total, sum(len(roster) for roster in rosters.values()))
    return {
        "course_id": course.id,
        "course_code": course.code,
        "course_name": course.name,
        "total_sessions": len(sessions),
        "total_enrolled": sum(enrollment.is_active for enrollment in enrollments),
        "overall_attendance_rate": overall_rate,
        "average_attendance_rate": overall_rate,
        "flagged_checkins": sum(checkin.status in {"flagged", "appealed"} for checkin in checkins),
        "sessions": session_rows,
        "student_attendance": student_rows,
        "low_attendance_alerts": alerts,
    }


def student_statistics(db, student_id: str, course_ids: list[str]) -> dict:
    student = db.get(User, str(student_id))
    if student is None:
        return {}
    course_ids = [str(course_id) for course_id in course_ids]
    enrollments = db.scalars(select(Enrollment).where(
        Enrollment.student_id == student.id,
        Enrollment.course_id.in_(course_ids),
    )).all() if course_ids else []
    enrolled_course_ids = [enrollment.course_id for enrollment in enrollments]
    courses = {
        course.id: course for course in db.scalars(
            select(Course).where(Course.id.in_(enrolled_course_ids))
        ).all()
    } if enrolled_course_ids else {}
    all_sessions = db.scalars(select(Session).where(
        Session.course_id.in_(enrolled_course_ids),
        Session.status != "cancelled",
        Session.scheduled_start <= utcnow(),
    )).all() if enrolled_course_ids else []
    all_session_ids = [session.id for session in all_sessions]
    all_checkins = db.scalars(select(CheckIn).where(
        CheckIn.student_id == student.id,
        CheckIn.session_id.in_(all_session_ids),
    ).order_by(CheckIn.checked_in_at.desc())).all() if all_session_ids else []
    evidence_session_ids = {checkin.session_id for checkin in all_checkins}
    periods = _periods_by_enrollment(db, enrollments)
    enrollments_by_course = {enrollment.course_id: [enrollment] for enrollment in enrollments}
    sessions = [
        session for session in all_sessions
        if student.id in _session_roster(
            session,
            enrollments_by_course.get(session.course_id, []),
            periods,
            (student.id,) if session.id in evidence_session_ids else (),
        )
    ]
    session_ids = {session.id for session in sessions}
    checkins = [checkin for checkin in all_checkins if checkin.session_id in session_ids]
    sessions_by_course: dict[str, list[Session]] = {}
    session_by_id = {}
    for session in sessions:
        sessions_by_course.setdefault(session.course_id, []).append(session)
        session_by_id[session.id] = session
    checkins_by_course: dict[str, list[CheckIn]] = {}
    for checkin in checkins:
        session = session_by_id.get(checkin.session_id)
        if session:
            checkins_by_course.setdefault(session.course_id, []).append(checkin)
    course_rows = []
    for enrollment in enrollments:
        course = courses.get(enrollment.course_id)
        if course is None:
            continue
        course_sessions = sessions_by_course.get(course.id, [])
        course_checkins = checkins_by_course.get(course.id, [])
        attended = sum(checkin.status == ATTENDED_STATUS for checkin in course_checkins)
        course_rows.append({
            "course_id": course.id,
            "course_code": course.code,
            "attendance_rate": _rate(attended, len(course_sessions)),
            "sessions_attended": attended,
            "total_sessions": len(course_sessions),
            "average_risk_score": _mean(checkin.risk_score for checkin in course_checkins),
        })
    recent = []
    for checkin in checkins[:10]:
        session = session_by_id.get(checkin.session_id)
        course = courses.get(session.course_id) if session else None
        recent.append({
            "session_id": session.id if session else checkin.session_id,
            "session_name": session.name if session else None,
            "course_code": course.code if course else None,
            "checked_in_at": checkin.checked_in_at,
            "status": checkin.status,
            "risk_score": checkin.risk_score,
        })
    attended_total = sum(checkin.status == ATTENDED_STATUS for checkin in checkins)
    return {
        "student_id": student.id,
        "student_name": student.full_name,
        "student_email": student.email,
        "courses": course_rows,
        "recent_checkins": recent,
        "total_enrolled_courses": len(enrollments),
        "total_sessions": len(sessions),
        "attended_sessions": attended_total,
        "attendance_rate": _rate(attended_total, len(sessions)),
        "recent_sessions": recent,
    }


def _export_record(student: User, session: Session, checkin: CheckIn | None) -> dict:
    return {
        "student_id": student.id,
        "student_name": student.full_name,
        "student_email": student.email,
        "session_id": session.id,
        "session_date": session.scheduled_start.date().isoformat(),
        "session_name": session.name,
        "status": checkin.status if checkin else "absent",
        "checked_in_at": checkin.checked_in_at if checkin else None,
        "risk_score": checkin.risk_score if checkin else None,
    }


def _export_summary(records: list[dict], enrolled: int) -> dict:
    checked_in = sum(record["status"] != "absent" for record in records)
    approved = sum(record["status"] == ATTENDED_STATUS for record in records)
    return {
        "total_enrolled": enrolled,
        "checked_in": checked_in,
        "approved": approved,
        "absent": enrolled - checked_in,
        "attendance_rate": _rate(approved, enrolled),
    }


def session_attendance_export(db, session_id: str) -> dict:
    session = db.get(Session, str(session_id))
    if session is None:
        return {}
    enrollments = db.scalars(select(Enrollment).where(
        Enrollment.course_id == session.course_id,
    )).all()
    checkins = {
        checkin.student_id: checkin
        for checkin in db.scalars(select(CheckIn).where(CheckIn.session_id == session.id)).all()
    }
    roster = _session_roster(
        session,
        enrollments,
        _periods_by_enrollment(db, enrollments),
        checkins,
    )
    students = {
        student.id: student
        for student in db.scalars(select(User).where(
            User.id.in_(roster)
        )).all()
    } if roster else {}
    records = [
        _export_record(students[student_id], session, checkins.get(student_id))
        for student_id in roster
        if student_id in students
    ]
    records.sort(key=lambda record: (record["student_name"].casefold(), record["student_id"]))
    course = db.get(Course, session.course_id)
    return {
        "session_id": session.id,
        "session_name": session.name,
        "course_id": session.course_id,
        "course_code": course.code if course else None,
        "summary": _export_summary(records, len(records)),
        "records": records,
    }


def course_attendance_export(
    db,
    course_id: str,
    *,
    start_date: date | datetime | None = None,
    end_date: date | datetime | None = None,
) -> dict:
    course = db.get(Course, str(course_id))
    if course is None:
        return {}
    start = _date_start(start_date)
    end = _date_end(end_date) or _today_end()
    sessions = db.scalars(select(Session).where(
        Session.course_id == course.id,
        Session.status != "cancelled",
        *([Session.scheduled_start >= start] if start else []),
        Session.scheduled_start <= end,
    ).order_by(Session.scheduled_start)).all()
    enrollments = db.scalars(select(Enrollment).where(
        Enrollment.course_id == course.id,
    )).all()
    session_ids = [session.id for session in sessions]
    checkins = db.scalars(select(CheckIn).where(
        CheckIn.session_id.in_(session_ids)
    )).all() if session_ids else []
    periods = _periods_by_enrollment(db, enrollments)
    checkin_by_pair = {(checkin.session_id, checkin.student_id): checkin for checkin in checkins}
    evidence_by_session: dict[str, set[str]] = {}
    for checkin in checkins:
        evidence_by_session.setdefault(checkin.session_id, set()).add(checkin.student_id)
    rosters = {
        session.id: _session_roster(
            session, enrollments, periods, evidence_by_session.get(session.id, ())
        )
        for session in sessions
    }
    roster_student_ids = set().union(*rosters.values()) if rosters else set()
    students = {
        student.id: student for student in db.scalars(select(User).where(
            User.id.in_(roster_student_ids)
        )).all()
    } if roster_student_ids else {}
    records = []
    for session in sessions:
        for student_id in rosters[session.id]:
            student = students.get(student_id)
            if student:
                records.append(_export_record(
                    student, session, checkin_by_pair.get((session.id, student.id))
                ))
    checked_in = sum(record["status"] != "absent" for record in records)
    approved = sum(record["status"] == ATTENDED_STATUS for record in records)
    total_records = len(records)
    return {
        "course_id": course.id,
        "course_code": course.code,
        "course_name": course.name,
        "summary": {
            "total_enrolled": len(roster_student_ids),
            "total_sessions": len(sessions),
            "total_records": total_records,
            "checked_in": checked_in,
            "approved": approved,
            "absent": total_records - checked_in,
            "attendance_rate": _rate(approved, total_records),
        },
        "records": records,
    }


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.isoformat()
    if isinstance(value, str) and (
        value.startswith(("\t", "\r")) or value.lstrip().startswith(("=", "+", "-", "@"))
    ):
        return "'" + value
    return value


def attendance_csv(records: list[dict]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(ATTENDANCE_CSV_COLUMNS)
    for record in records:
        writer.writerow([_csv_value(record.get(column)) for column in ATTENDANCE_CSV_COLUMNS])
    return output.getvalue()
