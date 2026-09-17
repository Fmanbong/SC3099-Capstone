"""Transactional retention cleanup used by the scheduler and maintenance CLI.

Audit rows remain append-only. Deleting a user only clears ``AuditLog.user_id``
through its database ``ON DELETE SET NULL`` constraint; opaque resource IDs and
the original safe audit metadata are deliberately retained.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select, update

from app.database import utcnow
from app.models import (
    CheckIn,
    CourseStaff,
    Device,
    DeviceChallenge,
    Enrollment,
    RefreshSession,
    RiskSignal,
    Session,
    User,
)
from app.services.audit import audit_event


def _delete(db, model, *conditions) -> int:
    result = db.execute(
        delete(model).where(*conditions).execution_options(synchronize_session="fetch")
    )
    return int(result.rowcount or 0)


def cleanup_expired_records(db, *, now: datetime | None = None, commit: bool = True) -> dict:
    """Delete expired privacy records atomically and return affected-row counts."""
    now = now or utcnow()
    counts = {
        "risk_signals_deleted": 0,
        "checkins_deleted": 0,
        "device_challenges_deleted": 0,
        "refresh_sessions_deleted": 0,
        "devices_deleted": 0,
        "enrollments_deleted": 0,
        "course_staff_deleted": 0,
        "session_instructors_cleared": 0,
        "reviewer_links_cleared": 0,
        "users_deleted": 0,
    }

    # Challenges no longer prove freshness after expiry. Expired refresh rows
    # are also unusable; unexpired used rows remain for token-reuse detection.
    counts["device_challenges_deleted"] += _delete(
        db, DeviceChallenge, DeviceChallenge.expires_at <= now
    )
    counts["refresh_sessions_deleted"] += _delete(
        db, RefreshSession, RefreshSession.expires_at <= now
    )

    expired_checkin_ids = list(db.scalars(select(CheckIn.id).where(
        CheckIn.scheduled_deletion_at.is_not(None),
        CheckIn.scheduled_deletion_at <= now,
    )))
    if expired_checkin_ids:
        counts["risk_signals_deleted"] += _delete(
            db, RiskSignal, RiskSignal.checkin_id.in_(expired_checkin_ids)
        )
        counts["checkins_deleted"] += _delete(
            db, CheckIn, CheckIn.id.in_(expired_checkin_ids)
        )

    due_user_ids = list(db.scalars(select(User.id).where(
        User.scheduled_deletion_at.is_not(None),
        User.scheduled_deletion_at <= now,
    )))
    if due_user_ids:
        user_checkin_ids = list(db.scalars(select(CheckIn.id).where(
            CheckIn.student_id.in_(due_user_ids)
        )))
        if user_checkin_ids:
            counts["risk_signals_deleted"] += _delete(
                db, RiskSignal, RiskSignal.checkin_id.in_(user_checkin_ids)
            )
            counts["checkins_deleted"] += _delete(
                db, CheckIn, CheckIn.id.in_(user_checkin_ids)
            )
        counts["device_challenges_deleted"] += _delete(
            db, DeviceChallenge, DeviceChallenge.user_id.in_(due_user_ids)
        )
        counts["refresh_sessions_deleted"] += _delete(
            db, RefreshSession, RefreshSession.user_id.in_(due_user_ids)
        )
        counts["devices_deleted"] = _delete(db, Device, Device.user_id.in_(due_user_ids))
        counts["enrollments_deleted"] = _delete(
            db, Enrollment, Enrollment.student_id.in_(due_user_ids)
        )
        counts["course_staff_deleted"] = _delete(
            db, CourseStaff, CourseStaff.user_id.in_(due_user_ids)
        )
        counts["session_instructors_cleared"] = int(db.execute(
            update(Session).where(Session.instructor_id.in_(due_user_ids)).values(instructor_id=None)
        ).rowcount or 0)
        counts["reviewer_links_cleared"] = int(db.execute(
            update(CheckIn).where(CheckIn.reviewed_by_id.in_(due_user_ids)).values(reviewed_by_id=None)
        ).rowcount or 0)
        counts["users_deleted"] = _delete(db, User, User.id.in_(due_user_ids))

    if any(counts.values()):
        audit_event(db, "retention_cleanup", resource_type="retention", details=counts.copy())
    if commit:
        db.commit()
    else:
        db.flush()
    return counts
