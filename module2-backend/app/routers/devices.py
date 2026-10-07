from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Literal
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DatabaseSession

from app.database import utcnow
from app.dependencies import current_user, get_db, require_roles
from app.errors import ApiError
from app.models import Device, DeviceChallenge, Enrollment, Session
from app.schemas import CleanModel, Name
from app.serializers import device_data, page
from app.services.audit import audit_event


router = APIRouter(prefix="/devices", tags=["devices"])


class RegisterDevice(CleanModel):
    device_fingerprint: str = Field(min_length=1, max_length=128)
    device_name: Name | None = None
    platform: Literal["ios", "android", "web", "desktop"] | None = None
    browser: str | None = Field(default=None, max_length=100)
    os_version: str | None = Field(default=None, max_length=100)
    app_version: str | None = Field(default=None, max_length=50)
    public_key: str | None = Field(default=None, max_length=8192)


class UpdateDevice(CleanModel):
    device_name: Name | None = None
    is_active: bool | None = None
    is_trusted: bool | None = None
    trust_score: Literal["low", "medium", "high"] | None = None

    @field_validator("is_active", "is_trusted", "trust_score")
    @classmethod
    def required_fields_cannot_be_null(cls, value):
        if value is None:
            raise ValueError("field cannot be null")
        return value


class ChallengeRequest(CleanModel):
    session_id: UUID


def _device_or_404(db: DatabaseSession, device_id: str) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise ApiError(404, "Device not found", "DEVICE_NOT_FOUND")
    return device


def _validate_public_key(value: str) -> None:
    try:
        key = serialization.load_pem_public_key(value.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "Invalid device public key", "DEVICE_KEY_INVALID") from exc
    if not isinstance(key, (ec.EllipticCurvePublicKey, rsa.RSAPublicKey)):
        raise ApiError(400, "Unsupported device public key", "DEVICE_KEY_UNSUPPORTED")


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_device(
    body: RegisterDevice,
    request: Request,
    db: DatabaseSession = Depends(get_db),
    user=Depends(current_user),
):
    if body.public_key:
        _validate_public_key(body.public_key)
    now = utcnow()
    device = Device(
        user_id=user.id,
        device_fingerprint=body.device_fingerprint,
        device_name=body.device_name,
        platform=body.platform,
        browser=body.browser,
        os_version=body.os_version,
        app_version=body.app_version,
        public_key=body.public_key,
        public_key_created_at=now if body.public_key else None,
        is_trusted=False,
        trust_score="low",
        attestation_passed=False,
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(device)
    try:
        db.flush()
    except IntegrityError as exc:
        raise ApiError(400, "Device fingerprint is already registered", "DEVICE_EXISTS") from exc
    audit_event(
        db,
        "device_registered",
        user_id=user.id,
        resource_type="device",
        resource_id=device.id,
        details={"platform": device.platform},
        request=request,
    )
    db.commit()
    return device_data(device)


@router.get("/my-devices")
def my_devices(db: DatabaseSession = Depends(get_db), user=Depends(current_user)):
    devices = db.scalars(
        select(Device).where(Device.user_id == user.id).order_by(Device.first_seen_at.desc())
    ).all()
    return [device_data(device) for device in devices]


@router.get("", include_in_schema=False)
@router.get("/")
def list_devices(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: DatabaseSession = Depends(get_db),
    _=Depends(require_roles("admin")),
):
    total = db.scalar(select(func.count()).select_from(Device)) or 0
    devices = db.scalars(
        select(Device).order_by(Device.first_seen_at.desc()).limit(limit).offset(offset)
    ).all()
    return page([device_data(device) for device in devices], total, limit, offset)


@router.patch("/{device_id}")
def update_device(
    device_id: UUID,
    body: UpdateDevice,
    request: Request,
    db: DatabaseSession = Depends(get_db),
    user=Depends(current_user),
):
    device = _device_or_404(db, str(device_id))
    if device.user_id != user.id and user.role != "admin":
        raise ApiError(403, "You cannot update this device", "DEVICE_ACCESS_DENIED")
    fields = body.model_fields_set
    if ({"is_trusted", "trust_score"} & fields) and user.role != "admin":
        raise ApiError(403, "Only administrators can change device trust", "DEVICE_TRUST_FORBIDDEN")
    if "device_name" in fields:
        device.device_name = body.device_name
    if "is_active" in fields:
        device.is_active = body.is_active
        if body.is_active is False:
            device.revoked_at = utcnow()
            device.revocation_reason = "deactivated_by_user" if user.role != "admin" else "deactivated_by_admin"
        elif body.is_active is True:
            device.revoked_at = None
            device.revocation_reason = None
    if "is_trusted" in fields:
        device.is_trusted = body.is_trusted
    if "trust_score" in fields:
        device.trust_score = body.trust_score
    audit_event(
        db,
        "device_updated",
        user_id=user.id,
        resource_type="device",
        resource_id=device.id,
        details={"fields": sorted(fields)},
        request=request,
    )
    db.commit()
    return device_data(device)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(
    device_id: UUID,
    request: Request,
    db: DatabaseSession = Depends(get_db),
    user=Depends(current_user),
):
    device = _device_or_404(db, str(device_id))
    if device.user_id != user.id and user.role != "admin":
        raise ApiError(403, "You cannot remove this device", "DEVICE_ACCESS_DENIED")
    audit_event(
        db,
        "device_removed",
        user_id=user.id,
        resource_type="device",
        resource_id=device.id,
        request=request,
    )
    db.delete(device)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{device_id}/challenge", status_code=status.HTTP_201_CREATED)
def create_challenge(
    device_id: UUID,
    body: ChallengeRequest,
    request: Request,
    db: DatabaseSession = Depends(get_db),
    user=Depends(current_user),
):
    device = _device_or_404(db, str(device_id))
    if device.user_id != user.id:
        raise ApiError(403, "You cannot challenge this device", "DEVICE_ACCESS_DENIED")
    if not device.is_active:
        raise ApiError(400, "Device is inactive", "DEVICE_INACTIVE")
    if not device.public_key:
        raise ApiError(400, "Device has no public key", "DEVICE_KEY_REQUIRED")
    session = db.get(Session, str(body.session_id))
    if session is None:
        raise ApiError(404, "Session not found", "SESSION_NOT_FOUND")
    if user.role == "student":
        enrolled = db.scalar(
            select(Enrollment.id).where(
                Enrollment.student_id == user.id,
                Enrollment.course_id == session.course_id,
                Enrollment.is_active.is_(True),
            )
        )
        if enrolled is None:
            raise ApiError(403, "You are not enrolled in this course", "ENROLLMENT_REQUIRED")
    now = utcnow()
    challenge = DeviceChallenge(
        user_id=user.id,
        device_id=device.id,
        session_id=session.id,
        nonce=secrets.token_urlsafe(32),
        expires_at=now + timedelta(minutes=5),
    )
    db.add(challenge)
    db.flush()
    audit_event(
        db,
        "device_challenge_created",
        user_id=user.id,
        resource_type="device",
        resource_id=device.id,
        details={"session_id": session.id, "challenge_id": challenge.id},
        request=request,
    )
    db.commit()
    return {
        "id": challenge.id,
        "device_id": device.id,
        "session_id": session.id,
        "nonce": challenge.nonce,
        "expires_at": challenge.expires_at,
    }
