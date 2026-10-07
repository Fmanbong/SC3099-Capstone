from __future__ import annotations

import html
import secrets
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import AwareDatetime, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DatabaseSession

from app.database import utcnow
from app.dependencies import current_user, ensure_course_access, get_db, require_roles, visible_course_ids
from app.errors import ApiError
from app.models import CheckIn, Course, Device, Enrollment, RiskSignal, Session
from app.schemas import CleanModel
from app.serializers import checkin_data, page
from app.services.audit import audit_event
from app.services.checkins import (
    decide_status,
    distance_meters,
    factor,
    local_risk_score,
    verify_and_consume_device_proof,
)


router = APIRouter(prefix="/checkins", tags=["check-ins"])


class SubmitCheckIn(CleanModel):
    session_id: UUID
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    location_accuracy_meters: float | None = Field(default=None, ge=0, le=50_000)
    device_fingerprint: str = Field(min_length=1, max_length=128)
    liveness_challenge_response: str | None = Field(default=None, min_length=1, max_length=7 * 1024 * 1024)
    face_image: str | None = Field(default=None, min_length=1, max_length=7 * 1024 * 1024)
    liveness_challenge_type: Literal["passive", "blink", "head_turn"] = "passive"
    qr_code: str | None = Field(default=None, max_length=512)
    device_challenge_id: UUID | None = None
    device_signature: str | None = Field(default=None, min_length=1, max_length=8192)


class AppealCheckIn(CleanModel):
    appeal_reason: str = Field(min_length=10, max_length=2000)

    @field_validator("appeal_reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 10:
            raise ValueError("Appeal reason must be at least 10 characters")
        return html.escape(value, quote=True)


class ReviewCheckIn(CleanModel):
    status: Literal["approved", "rejected"]
    review_notes: str = Field(min_length=1, max_length=2000)

    @field_validator("review_notes")
    @classmethod
    def clean_notes(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Review notes must not be blank")
        return html.escape(value, quote=True)


def _checkin_or_404(db: DatabaseSession, checkin_id: str) -> CheckIn:
    checkin = db.get(CheckIn, checkin_id)
    if checkin is None:
        raise ApiError(404, "Check-in not found", "CHECKIN_NOT_FOUND")
    return checkin


def _session_course(db: DatabaseSession, session_id: str) -> tuple[Session, Course]:
    session = db.get(Session, session_id)
    if session is None:
        raise ApiError(404, "Session not found", "SESSION_NOT_FOUND")
    course = db.get(Course, session.course_id)
    if course is None:
        raise ApiError(404, "Course not found", "COURSE_NOT_FOUND")
    return session, course


def _severity(score: float) -> str:
    if score >= 0.7:
        return "critical"
    if score >= 0.5:
        return "high"
    if score >= 0.3:
        return "medium"
    return "low"


@router.post("/", status_code=status.HTTP_201_CREATED)
@router.post("", status_code=status.HTTP_201_CREATED, include_in_schema=False)
def submit_checkin(
    body: SubmitCheckIn,
    request: Request,
    db: DatabaseSession = Depends(get_db),
    user=Depends(require_roles("student")),
):
    settings = request.app.state.settings
    now = utcnow()
    request.app.state.limiter.check(f"checkin:{user.id}", 10, 60)
    session, course = _session_course(db, str(body.session_id))
    if not course.is_active:
        raise ApiError(400, "Course is inactive", "COURSE_INACTIVE")
    if session.status != "active":
        raise ApiError(400, "Session is not active", "SESSION_NOT_ACTIVE")
    if now < session.checkin_opens_at or now > session.checkin_closes_at:
        raise ApiError(400, "Check-in window is closed", "CHECKIN_WINDOW_CLOSED")
    enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.student_id == user.id,
            Enrollment.course_id == course.id,
            Enrollment.is_active.is_(True),
        )
    )
    if enrollment is None:
        raise ApiError(403, "Active enrollment is required", "ENROLLMENT_REQUIRED")
    if db.scalar(select(CheckIn.id).where(CheckIn.session_id == session.id, CheckIn.student_id == user.id)):
        raise ApiError(400, "You have already checked in to this session", "CHECKIN_EXISTS")

    image = body.face_image or body.liveness_challenge_response
    liveness_required = bool(session.require_liveness_check)
    face_required = bool(session.require_face_match or course.require_face_recognition)
    factors: list[dict] = []
    missing_required_evidence = False

    if not user.geolocation_consent:
        if not settings.public_test_mode:
            raise ApiError(400, "Geolocation consent is required", "GEOLOCATION_CONSENT_REQUIRED")
        factors.append(factor("geolocation_consent_missing", 0.8, 0.15, "high"))
        missing_required_evidence = True
    if image and not user.camera_consent:
        if not settings.public_test_mode:
            raise ApiError(400, "Camera consent is required", "CAMERA_CONSENT_REQUIRED")
        factors.append(factor("camera_consent_missing", 0.8, 0.25, "high"))
        image = None
        missing_required_evidence = True
    if (liveness_required or face_required) and not image:
        if not settings.public_test_mode:
            raise ApiError(400, "An image is required for this session", "CHECKIN_IMAGE_REQUIRED")
        factors.append(factor("required_image_missing", 0.8, 0.25, "high"))
        missing_required_evidence = True
    if face_required and (not user.face_enrolled or not user.face_embedding_hash):
        if not settings.public_test_mode:
            raise ApiError(400, "Face enrollment is required", "FACE_ENROLLMENT_REQUIRED")
        factors.append(factor("face_enrollment_missing", 0.8, 0.25, "high"))
        missing_required_evidence = True

    venue_lat = session.venue_latitude if session.venue_latitude is not None else course.venue_latitude
    venue_lng = session.venue_longitude if session.venue_longitude is not None else course.venue_longitude
    radius = (
        session.geofence_radius_meters
        if session.geofence_radius_meters is not None
        else course.geofence_radius_meters
    )
    distance = None
    geolocation_risk = 0.5
    if venue_lat is not None and venue_lng is not None:
        distance = distance_meters(body.latitude, body.longitude, venue_lat, venue_lng)
        if radius and distance > 2 * radius:
            geolocation_risk = 1.0
            factors.append(factor("geo_far_out_of_bounds", 1.0, 0.15, "critical", distance_meters=distance))
        elif radius and distance > radius:
            geolocation_risk = 0.7
            factors.append(factor("geo_out_of_bounds", 0.7, 0.15, "high", distance_meters=distance))
        elif body.location_accuracy_meters is not None and body.location_accuracy_meters > max(radius or 100, 100):
            geolocation_risk = 0.6
            factors.append(factor("geolocation_inaccurate", 0.6, 0.15, "high"))
        else:
            geolocation_risk = 0.05
    else:
        factors.append(factor("venue_location_missing", 0.5, 0.15, "medium"))

    device = db.scalar(
        select(Device).where(
            Device.user_id == user.id,
            Device.device_fingerprint == body.device_fingerprint,
            Device.is_active.is_(True),
        )
    )
    if device is None:
        existing_device = db.scalar(select(Device).where(Device.device_fingerprint == body.device_fingerprint))
        if existing_device is None:
            device = Device(
                user_id=user.id,
                device_fingerprint=body.device_fingerprint,
                device_name="Unverified device",
                platform="web",
                is_trusted=False,
                trust_score="low",
                attestation_passed=False,
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(device)
            db.flush()
            device_reason = "device_unknown"
        elif existing_device.user_id == user.id:
            device_reason = "device_inactive"
        else:
            device_reason = "device_owner_mismatch"
    else:
        device_reason = None

    device_verified = False
    device_context_reason = device_reason
    if device is not None:
        device_verified, proof_reason = verify_and_consume_device_proof(
            db,
            device=device,
            user_id=user.id,
            session_id=session.id,
            challenge_id=str(body.device_challenge_id) if body.device_challenge_id else None,
            signature=body.device_signature,
        )
        device_reason = proof_reason
        device.last_seen_at = now
        device.total_checkins = (device.total_checkins or 0) + 1
    if not device_verified:
        if device_context_reason:
            factors.append(factor(device_context_reason, 0.7, 0.20, "high"))
        reason = device_reason or "device_proof_missing"
        if reason != device_context_reason:
            factors.append(factor(reason, 0.7, 0.20, "high"))

    liveness_result = None
    face_result = None
    processed_image = bool(image and user.camera_consent)
    if processed_image and (liveness_required or body.liveness_challenge_response):
        liveness_result = request.app.state.face_client.liveness(image, body.liveness_challenge_type)
        if not liveness_result["liveness_passed"]:
            factors.append(
                factor(
                    "liveness_failed",
                    1 - liveness_result["liveness_score"],
                    0.25,
                    "critical",
                )
            )
    if processed_image and face_required and user.face_embedding_hash:
        face_result = request.app.state.face_client.verify(image, user.face_embedding_hash)
        if not face_result["match_passed"]:
            factors.append(
                factor("face_match_failed", 1 - face_result["match_score"], 0.25, "critical")
            )

    liveness_score = liveness_result["liveness_score"] if liveness_result else None
    face_score = face_result["match_score"] if face_result else None
    local_score = local_risk_score(
        liveness_score=liveness_score,
        face_match_score=face_score,
        device_verified=device_verified if course.require_device_binding else None,
        geolocation_risk=geolocation_risk,
    )
    risk_score = local_score
    if liveness_result is not None or face_result is not None:
        client_host = request.client.host if request.client else None
        assessed = request.app.state.face_client.assess(
            {
                "liveness_score": liveness_score,
                "face_match_score": face_score,
                "device_signature": body.device_signature if device_verified else None,
                "device_public_key": device.public_key if device_verified and device else None,
                "ip_address": client_host,
                "user_agent": request.headers.get("user-agent"),
                "geolocation": {
                    "latitude": body.latitude,
                    "longitude": body.longitude,
                    "accuracy": body.location_accuracy_meters or 0,
                },
            }
        )
        risk_score = max(local_score, assessed["risk_score"])
    risk_score = round(min(max(risk_score, 0.0), 1.0), 4)
    threshold = session.risk_threshold if session.risk_threshold is not None else course.risk_threshold

    qr_verified = False
    if session.qr_code_secret:
        qr_verified = bool(body.qr_code) and secrets.compare_digest(body.qr_code, session.qr_code_secret)
        if session.qr_code_expires_at and session.qr_code_expires_at < now:
            qr_verified = False
        if not qr_verified:
            factors.append(factor("qr_code_unverified", 0.7, 0.10, "high"))

    checkin_status = decide_status(
        risk_score=risk_score,
        risk_threshold=threshold,
        distance=distance,
        radius=radius,
        liveness_required=liveness_required or liveness_result is not None,
        liveness_passed=liveness_result["liveness_passed"] if liveness_result else None,
        face_required=face_required,
        face_passed=face_result["match_passed"] if face_result else None,
        device_required=bool(course.require_device_binding),
        device_verified=device_verified,
        missing_required_evidence=missing_required_evidence or bool(session.qr_code_secret and not qr_verified),
    )
    template_hash = None
    if face_result:
        template_hash = face_result.get("current_template_hash") or None
    elif liveness_result:
        template_hash = liveness_result.get("face_embedding_hash") or None
    checkin = CheckIn(
        session_id=session.id,
        student_id=user.id,
        device_id=device.id if device else None,
        status=checkin_status,
        checked_in_at=now,
        verified_at=now if checkin_status == "approved" else None,
        latitude=body.latitude,
        longitude=body.longitude,
        location_accuracy_meters=body.location_accuracy_meters,
        distance_from_venue_meters=distance,
        liveness_passed=liveness_result["liveness_passed"] if liveness_result else None,
        liveness_score=liveness_score,
        liveness_challenge_type=body.liveness_challenge_type if liveness_result else None,
        face_match_passed=face_result["match_passed"] if face_result else None,
        face_match_score=face_score,
        face_embedding_hash=template_hash,
        risk_score=risk_score,
        risk_factors=factors,
        qr_code_verified=qr_verified,
        scheduled_deletion_at=now + timedelta(days=settings.retention_days),
    )
    db.add(checkin)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(400, "You have already checked in to this session", "CHECKIN_EXISTS") from exc
    for item in factors:
        db.add(
            RiskSignal(
                checkin_id=checkin.id,
                signal_type=item["type"],
                score=item["score"],
                weight=item["weight"],
                details={"severity": item["severity"], **item.get("details", {})},
            )
        )
    audit_event(
        db,
        "checkin_attempted",
        user_id=user.id,
        resource_type="checkin",
        resource_id=checkin.id,
        details={"session_id": session.id},
        request=request,
    )
    audit_event(
        db,
        f"checkin_{checkin_status}",
        user_id=user.id,
        resource_type="checkin",
        resource_id=checkin.id,
        details={"session_id": session.id, "status": checkin_status},
        request=request,
        severity="warning" if checkin_status in {"flagged", "rejected"} else "info",
    )
    db.commit()
    return checkin_data(checkin, db)


def _staff_query(db: DatabaseSession, user, query):
    course_ids = visible_course_ids(db, user)
    return query.join(Session, Session.id == CheckIn.session_id).where(Session.course_id.in_(course_ids))


@router.get("/")
@router.get("", include_in_schema=False)
def list_checkins(
    session_id: UUID | None = None,
    course_id: UUID | None = None,
    student_id: UUID | None = None,
    checkin_status: Literal["pending", "approved", "flagged", "rejected", "appealed"] | None = Query(None, alias="status"),
    min_risk_score: float | None = Query(None, ge=0, le=1),
    max_risk_score: float | None = Query(None, ge=0, le=1),
    start_date: AwareDatetime | None = None,
    end_date: AwareDatetime | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: DatabaseSession = Depends(get_db),
    user=Depends(require_roles("admin", "instructor", "ta")),
):
    if start_date and end_date and start_date > end_date:
        raise ApiError(400, "start_date must not be after end_date", "INVALID_DATE_RANGE")
    if min_risk_score is not None and max_risk_score is not None and min_risk_score > max_risk_score:
        raise ApiError(400, "min_risk_score must not exceed max_risk_score", "INVALID_RISK_RANGE")
    query = _staff_query(db, user, select(CheckIn))
    if session_id:
        query = query.where(CheckIn.session_id == str(session_id))
    if course_id:
        query = query.where(Session.course_id == str(course_id))
    if student_id:
        query = query.where(CheckIn.student_id == str(student_id))
    if checkin_status:
        query = query.where(CheckIn.status == checkin_status)
    if min_risk_score is not None:
        query = query.where(CheckIn.risk_score >= min_risk_score)
    if max_risk_score is not None:
        query = query.where(CheckIn.risk_score <= max_risk_score)
    if start_date:
        query = query.where(CheckIn.checked_in_at >= start_date)
    if end_date:
        query = query.where(CheckIn.checked_in_at <= end_date)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(CheckIn.checked_in_at.desc()).limit(limit).offset(offset)).all()
    return page([checkin_data(row, db) for row in rows], total, limit, offset)


@router.get("/my-checkins")
@router.get("/me", include_in_schema=False)
def my_checkins(
    course_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    db: DatabaseSession = Depends(get_db),
    user=Depends(require_roles("student")),
):
    query = select(CheckIn).where(CheckIn.student_id == user.id)
    if course_id:
        query = query.join(Session, Session.id == CheckIn.session_id).where(Session.course_id == str(course_id))
    rows = db.scalars(query.order_by(CheckIn.checked_in_at.desc()).limit(limit)).all()
    return [checkin_data(row, db) for row in rows]


@router.get("/session/{session_id}")
def session_checkins(
    session_id: UUID,
    db: DatabaseSession = Depends(get_db),
    user=Depends(require_roles("admin", "instructor", "ta")),
):
    session, _ = _session_course(db, str(session_id))
    ensure_course_access(db, user, session.course_id)
    rows = db.scalars(
        select(CheckIn).where(CheckIn.session_id == session.id).order_by(CheckIn.checked_in_at.desc())
    ).all()
    result = []
    for row in rows:
        item = checkin_data(row, db)
        device = db.get(Device, row.device_id) if row.device_id else None
        item["device_trusted"] = bool(device and device.is_trusted)
        result.append(item)
    return result


@router.get("/flagged")
def flagged_checkins(
    course_id: UUID | None = None,
    session_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: DatabaseSession = Depends(get_db),
    user=Depends(require_roles("admin", "instructor", "ta")),
):
    query = _staff_query(db, user, select(CheckIn)).where(CheckIn.status.in_(("flagged", "appealed")))
    if course_id:
        query = query.where(Session.course_id == str(course_id))
    if session_id:
        query = query.where(CheckIn.session_id == str(session_id))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(CheckIn.checked_in_at).limit(limit).offset(offset)).all()
    return page([checkin_data(row, db) for row in rows], total, limit, offset)


@router.get("/{checkin_id}")
def get_checkin(
    checkin_id: UUID,
    db: DatabaseSession = Depends(get_db),
    user=Depends(current_user),
):
    checkin = _checkin_or_404(db, str(checkin_id))
    if user.role == "student":
        if checkin.student_id != user.id:
            raise ApiError(403, "You cannot view this check-in", "CHECKIN_ACCESS_DENIED")
    else:
        session, _ = _session_course(db, checkin.session_id)
        ensure_course_access(db, user, session.course_id)
    result = checkin_data(checkin, db)
    signals = db.scalars(select(RiskSignal).where(RiskSignal.checkin_id == checkin.id)).all()
    result["risk_signals"] = [
        {"type": item.signal_type, "score": item.score, "weight": item.weight, "details": item.details}
        for item in signals
    ]
    return result


@router.post("/{checkin_id}/appeal")
def appeal_checkin(
    checkin_id: UUID,
    body: AppealCheckIn,
    request: Request,
    db: DatabaseSession = Depends(get_db),
    user=Depends(require_roles("student")),
):
    checkin = _checkin_or_404(db, str(checkin_id))
    if checkin.student_id != user.id:
        raise ApiError(403, "You cannot appeal this check-in", "CHECKIN_ACCESS_DENIED")
    if checkin.appealed_at is not None or checkin.status == "appealed":
        raise ApiError(400, "This check-in has already been appealed", "APPEAL_EXISTS")
    if checkin.status not in {"flagged", "rejected"}:
        raise ApiError(400, "Only flagged or rejected check-ins may be appealed", "APPEAL_NOT_ALLOWED")
    if checkin.checked_in_at < utcnow() - timedelta(days=7):
        raise ApiError(400, "The appeal window has closed", "APPEAL_WINDOW_CLOSED")
    checkin.status = "appealed"
    checkin.appeal_reason = body.appeal_reason
    checkin.appealed_at = utcnow()
    audit_event(
        db,
        "checkin_appealed",
        user_id=user.id,
        resource_type="checkin",
        resource_id=checkin.id,
        request=request,
    )
    db.commit()
    return checkin_data(checkin, db)


@router.post("/{checkin_id}/review")
def review_checkin(
    checkin_id: UUID,
    body: ReviewCheckIn,
    request: Request,
    db: DatabaseSession = Depends(get_db),
    user=Depends(require_roles("admin", "instructor", "ta")),
):
    checkin = _checkin_or_404(db, str(checkin_id))
    session, _ = _session_course(db, checkin.session_id)
    ensure_course_access(db, user, session.course_id)
    if checkin.status not in {"flagged", "appealed"}:
        raise ApiError(400, "Only flagged or appealed check-ins may be reviewed", "REVIEW_NOT_ALLOWED")
    checkin.status = body.status
    checkin.review_notes = body.review_notes
    checkin.reviewed_by_id = user.id
    checkin.reviewed_at = utcnow()
    checkin.verified_at = checkin.reviewed_at if body.status == "approved" else None
    audit_event(
        db,
        "checkin_reviewed",
        user_id=user.id,
        resource_type="checkin",
        resource_id=checkin.id,
        details={"status": body.status},
        request=request,
    )
    db.commit()
    return checkin_data(checkin, db)
