"""Attendance evidence helpers kept separate from the HTTP boundary."""

from __future__ import annotations

import base64
import binascii
import math
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from sqlalchemy import update
from sqlalchemy.orm import Session as DatabaseSession

from app.database import utcnow
from app.models import Device, DeviceChallenge


SIGNAL_WEIGHTS = {
    "liveness": 0.25,
    "face_match": 0.25,
    "device": 0.20,
    "network": 0.15,
    "geolocation": 0.15,
}


def distance_meters(latitude: float, longitude: float, venue_latitude: float, venue_longitude: float) -> float:
    """Return great-circle distance using the haversine formula."""
    earth_radius = 6_371_000.0
    lat1, lat2 = math.radians(latitude), math.radians(venue_latitude)
    delta_lat = math.radians(venue_latitude - latitude)
    delta_lng = math.radians(venue_longitude - longitude)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    )
    return round(earth_radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value)), 2)


def factor(signal_type: str, score: float, weight: float, severity: str, **details: Any) -> dict[str, Any]:
    result = {"type": signal_type, "score": round(score, 4), "weight": weight, "severity": severity}
    if details:
        result["details"] = details
    return result


def local_risk_score(
    *,
    liveness_score: float | None,
    face_match_score: float | None,
    device_verified: bool | None,
    geolocation_risk: float,
) -> float:
    """Mirror Module 3's neutral defaults when optional evidence is absent."""
    risks = {
        "liveness": 0.5 if liveness_score is None else 1 - liveness_score,
        "face_match": 0.5 if face_match_score is None else 1 - face_match_score,
        "device": 0.5 if device_verified is None else (0.1 if device_verified else 0.7),
        "network": 0.5,
        "geolocation": geolocation_risk,
    }
    return round(sum(SIGNAL_WEIGHTS[name] * risks[name] for name in SIGNAL_WEIGHTS), 4)


def decide_status(
    *,
    risk_score: float,
    risk_threshold: float,
    distance: float | None,
    radius: float | None,
    liveness_required: bool,
    liveness_passed: bool | None,
    face_required: bool,
    face_passed: bool | None,
    device_required: bool,
    device_verified: bool,
    missing_required_evidence: bool = False,
) -> str:
    if liveness_required and liveness_passed is False:
        return "rejected"
    if face_required and face_passed is False:
        return "rejected"
    if distance is not None and radius is not None and distance > 2 * radius:
        return "rejected"
    if risk_score >= 0.7:
        return "rejected"
    if distance is not None and radius is not None and distance > radius:
        return "flagged"
    if missing_required_evidence:
        return "flagged"
    if device_required and not device_verified:
        return "flagged"
    if risk_score >= risk_threshold:
        return "flagged"
    return "approved"


def verify_and_consume_device_proof(
    db: DatabaseSession,
    *,
    device: Device,
    user_id: str,
    session_id: str,
    challenge_id: str | None,
    signature: str | None,
) -> tuple[bool, str | None]:
    """Verify a nonce signature and atomically consume its database row."""
    if not challenge_id or not signature:
        return False, "device_proof_missing"
    if not device.public_key:
        return False, "device_public_key_missing"
    if device.public_key_expires_at and device.public_key_expires_at <= utcnow():
        return False, "device_public_key_expired"

    challenge = db.get(DeviceChallenge, challenge_id)
    if (
        challenge is None
        or challenge.user_id != user_id
        or challenge.device_id != device.id
        or challenge.session_id != session_id
        or challenge.consumed_at is not None
        or challenge.expires_at <= utcnow()
    ):
        return False, "device_challenge_invalid"

    try:
        public_key = serialization.load_pem_public_key(device.public_key.encode("utf-8"))
        signature_bytes = base64.b64decode(signature, validate=True)
        message = challenge.nonce.encode("utf-8")
        if isinstance(public_key, ec.EllipticCurvePublicKey):
            try:
                public_key.verify(signature_bytes, message, ec.ECDSA(hashes.SHA256()))
            except InvalidSignature:
                component_size = (public_key.curve.key_size + 7) // 8
                if len(signature_bytes) != 2 * component_size:
                    raise
                r = int.from_bytes(signature_bytes[:component_size], "big")
                s = int.from_bytes(signature_bytes[component_size:], "big")
                public_key.verify(encode_dss_signature(r, s), message, ec.ECDSA(hashes.SHA256()))
        elif isinstance(public_key, rsa.RSAPublicKey):
            try:
                public_key.verify(
                    signature_bytes,
                    message,
                    padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
                    hashes.SHA256(),
                )
            except InvalidSignature:
                public_key.verify(signature_bytes, message, padding.PKCS1v15(), hashes.SHA256())
        else:
            return False, "device_key_unsupported"
    except (ValueError, TypeError, binascii.Error, InvalidSignature):
        return False, "device_signature_invalid"

    consumed_at = utcnow()
    result = db.execute(
        update(DeviceChallenge)
        .where(
            DeviceChallenge.id == challenge.id,
            DeviceChallenge.user_id == user_id,
            DeviceChallenge.device_id == device.id,
            DeviceChallenge.session_id == session_id,
            DeviceChallenge.consumed_at.is_(None),
            DeviceChallenge.expires_at > consumed_at,
        )
        .values(consumed_at=consumed_at)
    )
    if result.rowcount != 1:
        return False, "device_challenge_consumed"
    return True, None
