"""
SAIV Face Recognition & Risk Service - Module 3

Endpoints:
- POST /face/enroll     Enroll a face -> privacy-safe SimHash template
- POST /face/verify      Verify a face against an enrolled template
- POST /face/match       Legacy alias for /face/verify
- POST /liveness/check   Liveness / anti-spoofing check
- POST /risk/assess      Multi-signal risk fusion

Privacy design:
- Raw images are decoded in memory and NEVER written to disk or returned.
- Identity is represented as a 128-d face embedding (face_recognition / dlib),
  which is immediately compressed into a 256-bit SimHash (Locality-Sensitive
  Hash). Only the SimHash (64 hex chars) ever leaves this function's scope.
  SimHash preserves similarity (Hamming distance ~ cosine distance of the
  original embedding) so we can still tell "same person, different photo"
  apart from "different person" WITHOUT storing anything reversible.
  (Plain SHA-256 of the embedding would NOT work here: due to the avalanche
  effect, two photos of the same person produce completely unrelated
  SHA-256 hashes, making matching impossible. See module slides, "Approach 2".)
"""

import base64
import io
import ipaddress
from typing import Optional, Dict, List, Any, Tuple

import numpy as np
import cv2
from PIL import Image
import mediapipe as mp
import face_recognition

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(
    title="SAIV Face Recognition Service",
    description="Face enrollment, verification, liveness detection, and risk scoring service",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================================================================
# CONSTANTS  (values taken from docs/SECURITY-REQUIREMENTS.md - authoritative)
# =============================================================================

FACE_MATCH_THRESHOLD = 0.85
LIVENESS_THRESHOLD = 0.60
RISK_THRESHOLD = 0.50
MIN_DETECTION_CONFIDENCE = 0.5

SIMHASH_BITS = 256                 # -> 64 hex chars, same length as SHA-256
EMBEDDING_DIM = 128                # face_recognition output size

SIGNAL_WEIGHTS = {
    "liveness": 0.25,
    "face_match": 0.25,
    "device": 0.20,
    "network": 0.15,
    "geolocation": 0.15,
}
NEUTRAL_DEFAULT_RISK = 0.5          # used when a signal is not provided

# Fixed random hyperplanes for SimHash - MUST be deterministic and constant
# across the life of the service (and across restarts!), otherwise a hash
# generated at enroll time could never be reproduced at verify time.
_rng = np.random.RandomState(1337)
_SIMHASH_PLANES = _rng.randn(SIMHASH_BITS, EMBEDDING_DIM)

# MediaPipe solutions (loaded once, reused across requests)
_mp_face_detection = mp.solutions.face_detection
_mp_face_mesh = mp.solutions.face_mesh

# MediaPipe FaceMesh landmark indices used for liveness signals
NOSE_TIP = 1
LEFT_EYE_EAR_IDX = [33, 160, 158, 133, 153, 144]
RIGHT_EYE_EAR_IDX = [263, 387, 385, 362, 380, 373]


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class FaceEnrollRequest(BaseModel):
    user_id: str
    image: str
    camera_consent: bool = False


class FaceEnrollResponse(BaseModel):
    enrollment_successful: bool
    face_template_hash: str
    quality_score: float
    details: Dict[str, Any]


class FaceVerifyRequest(BaseModel):
    image: str
    reference_template_hash: str


class FaceVerifyResponse(BaseModel):
    match_passed: bool
    match_score: float
    match_threshold: float
    face_detected: bool
    current_template_hash: str


class LivenessRequest(BaseModel):
    challenge_response: str
    challenge_type: str = "blink"


class LivenessResponse(BaseModel):
    liveness_passed: bool
    liveness_score: float
    liveness_threshold: float
    face_embedding_hash: str
    details: Dict[str, Any]


class GeolocationData(BaseModel):
    latitude: float
    longitude: float
    accuracy: float


class RiskAssessRequest(BaseModel):
    liveness_score: Optional[float] = None
    face_match_score: Optional[float] = None
    device_signature: Optional[str] = None
    device_public_key: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    geolocation: Optional[GeolocationData] = None


class RiskAssessResponse(BaseModel):
    risk_score: float
    risk_level: str
    pass_threshold: bool
    risk_threshold: float
    signal_breakdown: Dict[str, float]
    recommendations: List[str]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def decode_base64_image(base64_string: str) -> np.ndarray:
    """Decode a base64 string (optionally with a data: URI prefix) to an RGB numpy array."""
    try:
        if "," in base64_string and base64_string.strip().startswith("data:"):
            base64_string = base64_string.split(",", 1)[1]
        image_bytes = base64.b64decode(base64_string)
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        return np.array(image)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image data: {exc}")


def detect_face(image_rgb: np.ndarray):
    """
    Detect the largest face using MediaPipe FaceDetection.
    Returns (bbox_css, confidence) where bbox_css is (top, right, bottom, left)
    in the format face_recognition expects, or (None, 0.0) if no face found.
    """
    h, w = image_rgb.shape[:2]
    with _mp_face_detection.FaceDetection(
        model_selection=1, min_detection_confidence=MIN_DETECTION_CONFIDENCE
    ) as detector:
        results = detector.process(image_rgb)

    if not results.detections:
        return None, 0.0

    # Pick the detection with the highest confidence (in case of multiple faces)
    best = max(results.detections, key=lambda d: d.score[0])
    confidence = float(best.score[0])
    box = best.location_data.relative_bounding_box

    xmin = int(box.xmin * w)
    ymin = int(box.ymin * h)
    bw = int(box.width * w)
    bh = int(box.height * h)

    top = max(0, ymin)
    left = max(0, xmin)
    bottom = min(h, ymin + bh)
    right = min(w, xmin + bw)

    if bottom <= top or right <= left:
        return None, 0.0

    return (top, right, bottom, left), confidence


def extract_face_embedding(image_rgb: np.ndarray, bbox_css) -> Optional[np.ndarray]:
    """Extract a 128-d identity embedding for the given bounding box using face_recognition/dlib."""
    encodings = face_recognition.face_encodings(
        image_rgb, known_face_locations=[bbox_css], num_jitters=1
    )
    if not encodings:
        return None
    return encodings[0]  # numpy array, shape (128,)


def generate_face_hash(embedding: np.ndarray) -> str:
    """
    Turn a 128-d embedding into a 256-bit SimHash, hex-encoded (64 chars).
    Similar embeddings (same person) -> hashes differing in few bits.
    Dissimilar embeddings (different person) -> hashes differing in many bits.
    The embedding itself is discarded after this call; only the hex string
    returned here is ever stored or transmitted.
    """
    projections = _SIMHASH_PLANES @ embedding
    bits = (projections > 0).astype(np.uint8)
    bit_string = "".join(str(b) for b in bits)
    return format(int(bit_string, 2), f"0{SIMHASH_BITS // 4}x")


def hash_to_bits(hex_hash: str) -> np.ndarray:
    """Inverse of generate_face_hash's bit-packing, for Hamming distance comparison."""
    value = int(hex_hash, 16)
    bin_string = format(value, f"0{SIMHASH_BITS}b")
    return np.array([int(c) for c in bin_string], dtype=np.uint8)


def hamming_match_score(hash_a: str, hash_b: str) -> float:
    """1.0 = identical hashes, 0.0 = maximally different (all bits differ)."""
    bits_a = hash_to_bits(hash_a)
    bits_b = hash_to_bits(hash_b)
    hamming_distance = int(np.sum(bits_a != bits_b))
    return 1.0 - (hamming_distance / SIMHASH_BITS)


def compute_quality_score(confidence: float, bbox_css, image_shape) -> float:
    """Blend detection confidence with face-size-relative-to-image as a quality proxy."""
    top, right, bottom, left = bbox_css
    face_area = (bottom - top) * (right - left)
    image_area = image_shape[0] * image_shape[1]
    size_ratio = min(face_area / image_area, 1.0) if image_area > 0 else 0.0
    # A face that's too tiny in the frame should reduce quality; ~sweet spot around 5-60% of frame
    size_score = min(size_ratio / 0.05, 1.0) if size_ratio < 0.5 else 1.0
    quality = 0.7 * confidence + 0.3 * size_score
    return round(float(min(max(quality, 0.0), 1.0)), 3)


def analyze_face_mesh(image_rgb: np.ndarray) -> Optional[Dict[str, Any]]:
    """Run MediaPipe FaceMesh and return landmarks + depth/EAR-derived signals."""
    with _mp_face_mesh.FaceMesh(
        static_image_mode=True,
        max_num_faces=1,
        refine_landmarks=False,
        min_detection_confidence=MIN_DETECTION_CONFIDENCE,
    ) as mesh:
        results = mesh.process(image_rgb)

    if not results.multi_face_landmarks:
        return None

    landmarks = results.multi_face_landmarks[0].landmark
    pts = np.array([[p.x, p.y, p.z] for p in landmarks])

    nose_tip_z = float(pts[NOSE_TIP][2])
    # Real faces show meaningful depth variation; flat photos/screens do not.
    depth_variance = float(np.std(pts[:, 2]))

    if abs(nose_tip_z) > 0.03 and depth_variance > 0.02:
        depth_quality = "good"
    elif abs(nose_tip_z) > 0.015:
        depth_quality = "moderate"
    else:
        depth_quality = "poor"

    left_ear = eye_aspect_ratio(pts, LEFT_EYE_EAR_IDX)
    right_ear = eye_aspect_ratio(pts, RIGHT_EYE_EAR_IDX)
    avg_ear = (left_ear + right_ear) / 2.0

    # Rough yaw estimate: how far the nose sits from the midpoint between the eyes,
    # relative to inter-eye distance. ~0 = frontal, larger = turned.
    left_eye_center = pts[LEFT_EYE_EAR_IDX].mean(axis=0)
    right_eye_center = pts[RIGHT_EYE_EAR_IDX].mean(axis=0)
    eye_midpoint_x = (left_eye_center[0] + right_eye_center[0]) / 2.0
    interocular = abs(right_eye_center[0] - left_eye_center[0]) + 1e-6
    yaw_ratio = abs(pts[NOSE_TIP][0] - eye_midpoint_x) / interocular

    return {
        "face_mesh_complete": len(pts) >= 468,
        "landmark_count": int(len(pts)),
        "nose_tip_z": nose_tip_z,
        "depth_variance": depth_variance,
        "depth_quality": depth_quality,
        "eye_aspect_ratio": float(avg_ear),
        "yaw_ratio": float(yaw_ratio),
    }


def eye_aspect_ratio(pts: np.ndarray, idx: List[int]) -> float:
    """
    Standard 6-point Eye Aspect Ratio (EAR). Lower value = eye more closed.
    idx order: [outer_corner, top1, top2, inner_corner, bottom2, bottom1]
    """
    p = pts[idx][:, :2]  # use x,y only
    vertical_1 = np.linalg.norm(p[1] - p[5])
    vertical_2 = np.linalg.norm(p[2] - p[4])
    horizontal = np.linalg.norm(p[0] - p[3]) + 1e-6
    return float((vertical_1 + vertical_2) / (2.0 * horizontal))


def detect_vpn_proxy(ip_address: Optional[str], user_agent: Optional[str]) -> Tuple[bool, float]:
    """Heuristic VPN/proxy/private-network detection. Returns (is_suspicious, confidence)."""
    suspicious = False
    confidence = 0.1

    if ip_address:
        try:
            ip_obj = ipaddress.ip_address(ip_address)
            if ip_obj.is_private or ip_obj.is_loopback:
                suspicious = True
                confidence = max(confidence, 0.7)
        except ValueError:
            suspicious = True
            confidence = max(confidence, 0.5)

    if user_agent:
        ua_lower = user_agent.lower()
        for keyword in ("vpn", "proxy", "tor"):
            if keyword in ua_lower:
                suspicious = True
                confidence = max(confidence, 0.85)

    return suspicious, confidence


# =============================================================================
# HEALTH & ROOT ENDPOINTS
# =============================================================================

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "face-recognition"}


@app.get("/")
async def root():
    return {
        "service": "SAIV Face Recognition & Risk Service",
        "version": "1.0.0",
        "endpoints": [
            "/health",
            "/face/enroll",
            "/face/verify",
            "/face/match",
            "/liveness/check",
            "/risk/assess",
        ],
    }


# =============================================================================
# FACE ENROLLMENT
# =============================================================================

@app.post("/face/enroll", response_model=FaceEnrollResponse, status_code=201)
async def enroll_face(request: FaceEnrollRequest):
    if not request.camera_consent:
        raise HTTPException(status_code=400, detail="camera_consent is required for enrollment")

    image_rgb = decode_base64_image(request.image)
    bbox_css, confidence = detect_face(image_rgb)

    if bbox_css is None:
        raise HTTPException(status_code=400, detail="No face detected in image")

    embedding = extract_face_embedding(image_rgb, bbox_css)
    if embedding is None:
        raise HTTPException(status_code=400, detail="Could not extract face features")

    face_hash = generate_face_hash(embedding)
    quality = compute_quality_score(confidence, bbox_css, image_rgb.shape)

    # Discard raw image / embedding references explicitly (privacy hygiene)
    del image_rgb, embedding

    return FaceEnrollResponse(
        enrollment_successful=True,
        face_template_hash=face_hash,
        quality_score=quality,
        details={"detection_confidence": round(confidence, 3)},
    )


# =============================================================================
# FACE VERIFICATION
# =============================================================================

@app.post("/face/verify", response_model=FaceVerifyResponse)
async def verify_face(request: FaceVerifyRequest):
    image_rgb = decode_base64_image(request.image)
    bbox_css, confidence = detect_face(image_rgb)

    if bbox_css is None:
        return FaceVerifyResponse(
            match_passed=False,
            match_score=0.0,
            match_threshold=FACE_MATCH_THRESHOLD,
            face_detected=False,
            current_template_hash="",
        )

    embedding = extract_face_embedding(image_rgb, bbox_css)
    if embedding is None:
        return FaceVerifyResponse(
            match_passed=False,
            match_score=0.0,
            match_threshold=FACE_MATCH_THRESHOLD,
            face_detected=True,
            current_template_hash="",
        )

    current_hash = generate_face_hash(embedding)
    del image_rgb, embedding

    try:
        score = hamming_match_score(current_hash, request.reference_template_hash)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid reference_template_hash")

    return FaceVerifyResponse(
        match_passed=score >= FACE_MATCH_THRESHOLD,
        match_score=round(score, 4),
        match_threshold=FACE_MATCH_THRESHOLD,
        face_detected=True,
        current_template_hash=current_hash,
    )


@app.post("/face/match")
async def match_face(request: FaceVerifyRequest):
    """Legacy alias, kept for backwards compatibility."""
    return await verify_face(request)


# =============================================================================
# LIVENESS DETECTION
# =============================================================================

@app.post("/liveness/check", response_model=LivenessResponse)
async def check_liveness(request: LivenessRequest):
    image_rgb = decode_base64_image(request.challenge_response)
    bbox_css, confidence = detect_face(image_rgb)

    if bbox_css is None:
        return LivenessResponse(
            liveness_passed=False,
            liveness_score=0.0,
            liveness_threshold=LIVENESS_THRESHOLD,
            face_embedding_hash="",
            details={"reason": "no_face_detected"},
        )

    mesh_info = analyze_face_mesh(image_rgb)
    embedding = extract_face_embedding(image_rgb, bbox_css)
    face_hash = generate_face_hash(embedding) if embedding is not None else ""
    del image_rgb
    if embedding is not None:
        del embedding

    # REQUIRED baseline signals (always contribute)
    detection_component = confidence  # 0..1

    if mesh_info is None:
        # Fall back to detection-confidence-only score if mesh fails
        liveness_score = round(0.6 * detection_component, 3)
        details = {"reason": "face_mesh_unavailable", "detection_confidence": round(confidence, 3)}
    else:
        depth_component = {"good": 1.0, "moderate": 0.6, "poor": 0.15}[mesh_info["depth_quality"]]

        challenge_component = 0.5  # neutral default
        challenge_type = request.challenge_type
        if challenge_type == "blink":
            # A genuinely captured blink apex frame has a low EAR (eyes closed).
            # Since we only receive a single frame, treat low EAR as evidence
            # the requested blink challenge frame was captured correctly.
            ear = mesh_info["eye_aspect_ratio"]
            challenge_component = 1.0 if ear < 0.20 else max(0.3, 1.0 - ear)
        elif challenge_type == "head_turn":
            yaw = mesh_info["yaw_ratio"]
            challenge_component = min(yaw / 0.35, 1.0)  # more deviation = more turned
        elif challenge_type == "passive":
            challenge_component = depth_component

        liveness_score = round(
            0.35 * detection_component + 0.40 * depth_component + 0.25 * challenge_component, 3
        )
        details = {
            "detection_confidence": round(confidence, 3),
            "depth_quality": mesh_info["depth_quality"],
            "nose_tip_z": round(mesh_info["nose_tip_z"], 4),
            "eye_aspect_ratio": round(mesh_info["eye_aspect_ratio"], 3),
            "yaw_ratio": round(mesh_info["yaw_ratio"], 3),
            "challenge_type": request.challenge_type,
        }

    liveness_score = min(max(liveness_score, 0.0), 1.0)

    return LivenessResponse(
        liveness_passed=liveness_score >= LIVENESS_THRESHOLD,
        liveness_score=liveness_score,
        liveness_threshold=LIVENESS_THRESHOLD,
        face_embedding_hash=face_hash,
        details=details,
    )


# =============================================================================
# RISK ASSESSMENT
# =============================================================================

@app.post("/risk/assess", response_model=RiskAssessResponse)
async def assess_risk(request: RiskAssessRequest):
    signal_breakdown: Dict[str, float] = {}
    recommendations: List[str] = []

    # --- Liveness ---
    if request.liveness_score is not None:
        liveness_risk = 1.0 - request.liveness_score
    else:
        liveness_risk = NEUTRAL_DEFAULT_RISK
    signal_breakdown["liveness"] = round(liveness_risk, 3)
    if liveness_risk > 0.5:
        recommendations.append("Liveness score is low — consider re-prompting the liveness challenge")

    # --- Face match ---
    if request.face_match_score is not None:
        face_risk = 1.0 - request.face_match_score
    else:
        face_risk = NEUTRAL_DEFAULT_RISK
    signal_breakdown["face_match"] = round(face_risk, 3)
    if face_risk > 0.5:
        recommendations.append("Face match score is below threshold — flag for manual review")

    # --- Device ---
    has_device = bool(request.device_signature or request.device_public_key)
    if has_device:
        looks_valid = len(request.device_signature or request.device_public_key or "") >= 16
        device_risk = 0.1 if looks_valid else 0.7
    else:
        device_risk = NEUTRAL_DEFAULT_RISK
    signal_breakdown["device"] = round(device_risk, 3)
    if device_risk > 0.5:
        recommendations.append("Device not recognized or unverifiable — consider device binding")

    # --- Network (VPN/proxy) ---
    is_suspicious, net_confidence = detect_vpn_proxy(request.ip_address, request.user_agent)
    if request.ip_address is None and request.user_agent is None:
        network_risk = NEUTRAL_DEFAULT_RISK
    else:
        network_risk = net_confidence if is_suspicious else 0.05
    signal_breakdown["network"] = round(network_risk, 3)
    if network_risk > 0.5:
        recommendations.append("Request appears to originate from a VPN/proxy or private network")

    # --- Geolocation ---
    if request.geolocation is not None:
        acc = request.geolocation.accuracy
        lat_ok = -90 <= request.geolocation.latitude <= 90
        lng_ok = -180 <= request.geolocation.longitude <= 180
        if not (lat_ok and lng_ok):
            geo_risk = 0.9
        elif acc < 3 or acc > 5000:
            geo_risk = 0.6  # suspiciously precise or far too inaccurate
        else:
            geo_risk = 0.05
    else:
        geo_risk = NEUTRAL_DEFAULT_RISK
    signal_breakdown["geolocation"] = round(geo_risk, 3)
    if geo_risk > 0.5:
        recommendations.append("Location signal is missing or has unusual accuracy")

    risk_score = (
        SIGNAL_WEIGHTS["liveness"] * liveness_risk
        + SIGNAL_WEIGHTS["face_match"] * face_risk
        + SIGNAL_WEIGHTS["device"] * device_risk
        + SIGNAL_WEIGHTS["network"] * network_risk
        + SIGNAL_WEIGHTS["geolocation"] * geo_risk
    )
    risk_score = round(min(max(risk_score, 0.0), 1.0), 4)

    if risk_score < 0.3:
        risk_level = "LOW"
    elif risk_score < 0.5:
        risk_level = "MEDIUM"
    elif risk_score < 0.7:
        risk_level = "HIGH"
    else:
        risk_level = "CRITICAL"

    return RiskAssessResponse(
        risk_score=risk_score,
        risk_level=risk_level,
        pass_threshold=risk_score < RISK_THRESHOLD,
        risk_threshold=RISK_THRESHOLD,
        signal_breakdown=signal_breakdown,
        recommendations=recommendations,
    )