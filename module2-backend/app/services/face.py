"""Small, privacy-safe client for the Module 3 face service."""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any

import httpx

from app.errors import ApiError


_HASH = re.compile(r"^[0-9a-fA-F]{64}$")


class FaceServiceClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 5.0,
        *,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        max_image_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self._max_image_bytes = max_image_bytes
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds, transport=transport
        )

    def close(self) -> None:
        self._client.close()

    def enroll(self, user_id: str, image: str, camera_consent: bool) -> dict[str, Any]:
        self._validate_image(image)
        data = self._request(
            "/face/enroll",
            {"user_id": user_id, "image": image, "camera_consent": camera_consent},
        )
        self._require_bool(data, "enrollment_successful")
        self._require_hash(data, "face_template_hash")
        self._require_score(data, "quality_score")
        self._require_dict(data, "details")
        return data

    def verify(self, image: str, reference_template_hash: str) -> dict[str, Any]:
        self._validate_image(image)
        data = self._request(
            "/face/verify",
            {"image": image, "reference_template_hash": reference_template_hash},
        )
        match_passed = self._require_bool(data, "match_passed")
        self._require_score(data, "match_score")
        self._require_score(data, "match_threshold")
        face_detected = self._require_bool(data, "face_detected")
        current_hash = self._require_hash(data, "current_template_hash", allow_empty=True)
        if match_passed and (not face_detected or not current_hash):
            self._malformed()
        return data

    def liveness(self, image: str, challenge_type: str = "passive") -> dict[str, Any]:
        self._validate_image(image)
        data = self._request(
            "/liveness/check",
            {"challenge_response": image, "challenge_type": challenge_type},
        )
        self._require_bool(data, "liveness_passed")
        self._require_score(data, "liveness_score")
        self._require_score(data, "liveness_threshold")
        self._require_hash(data, "face_embedding_hash", allow_empty=True)
        self._require_dict(data, "details")
        return data

    def assess(self, signals: dict[str, Any]) -> dict[str, Any]:
        data = self._request("/risk/assess", signals)
        self._require_score(data, "risk_score")
        self._require_score(data, "risk_threshold")
        self._require_bool(data, "pass_threshold")
        if data.get("risk_level") not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            self._malformed()
        breakdown = self._require_dict(data, "signal_breakdown")
        if any(not self._is_score(value) for value in breakdown.values()):
            self._malformed()
        recommendations = data.get("recommendations")
        if not isinstance(recommendations, list) or any(
            not isinstance(item, str) for item in recommendations
        ):
            self._malformed()
        return data

    def _request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise ApiError(503, "Face verification service unavailable", "FACE_SERVICE_UNAVAILABLE") from exc

        if response.status_code == 400:
            raise ApiError(400, "Face image was rejected", "FACE_IMAGE_INVALID")
        if response.status_code < 200 or response.status_code >= 300:
            raise ApiError(503, "Face verification service unavailable", "FACE_SERVICE_UNAVAILABLE")
        try:
            data = response.json()
        except ValueError as exc:
            raise ApiError(503, "Face verification service returned invalid data", "FACE_SERVICE_INVALID") from exc
        if not isinstance(data, dict):
            self._malformed()
        return data

    def _validate_image(self, image: str) -> None:
        if not isinstance(image, str) or not image:
            raise ApiError(400, "A valid face image is required", "FACE_IMAGE_INVALID")
        encoded = image.split(",", 1)[1] if image.lstrip().startswith("data:") and "," in image else image
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ApiError(400, "A valid base64 face image is required", "FACE_IMAGE_INVALID") from exc
        if not decoded or len(decoded) > self._max_image_bytes:
            raise ApiError(400, "Face image size is invalid", "FACE_IMAGE_INVALID")

    @staticmethod
    def _is_score(value: Any) -> bool:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and 0 <= value <= 1

    @classmethod
    def _require_score(cls, data: dict[str, Any], field: str) -> float:
        value = data.get(field)
        if not cls._is_score(value):
            cls._malformed()
        return float(value)

    @classmethod
    def _require_hash(cls, data: dict[str, Any], field: str, *, allow_empty: bool = False) -> str:
        value = data.get(field)
        if not isinstance(value, str) or (not (allow_empty and value == "") and not _HASH.fullmatch(value)):
            cls._malformed()
        return value

    @classmethod
    def _require_bool(cls, data: dict[str, Any], field: str) -> bool:
        value = data.get(field)
        if not isinstance(value, bool):
            cls._malformed()
        return value

    @classmethod
    def _require_dict(cls, data: dict[str, Any], field: str) -> dict[str, Any]:
        value = data.get(field)
        if not isinstance(value, dict):
            cls._malformed()
        return value

    @staticmethod
    def _malformed() -> None:
        raise ApiError(503, "Face verification service returned invalid data", "FACE_SERVICE_INVALID")
