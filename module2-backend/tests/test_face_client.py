import json

import httpx
import pytest

from app.errors import ApiError
from app.services.face import FaceServiceClient

IMAGE = "aGVsbG8="

def make_client(handler):
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://face.test")
    return FaceServiceClient("http://face.test", timeout_seconds=0.25, client=http)


def test_face_client_uses_module3_paths_and_payloads():
    requests = []

    def handler(request):
        requests.append((request.url.path, json.loads(request.content)))
        responses = {
            "/face/enroll": {
                "enrollment_successful": True,
                "face_template_hash": "a" * 64,
                "quality_score": 0.8,
                "details": {"face_detected": True},
            },
            "/face/verify": {
                "match_passed": True,
                "match_score": 0.91,
                "match_threshold": 0.85,
                "face_detected": True,
                "current_template_hash": "b" * 64,
            },
            "/liveness/check": {
                "liveness_passed": True,
                "liveness_score": 0.77,
                "liveness_threshold": 0.6,
                "face_embedding_hash": "c" * 64,
                "details": {"challenge_type": "blink"},
            },
            "/risk/assess": {
                "risk_score": 0.2,
                "risk_level": "LOW",
                "pass_threshold": True,
                "risk_threshold": 0.5,
                "signal_breakdown": {"device": 0.1},
                "recommendations": [],
            },
        }
        return httpx.Response(201 if request.url.path == "/face/enroll" else 200, json=responses[request.url.path])

    client = make_client(handler)
    assert client.enroll("user-1", IMAGE, True)["face_template_hash"] == "a" * 64
    assert client.verify(IMAGE, "f" * 64)["match_score"] == 0.91
    assert client.liveness(IMAGE, "blink")["liveness_score"] == 0.77
    assert client.assess({"liveness_score": 0.77})["risk_score"] == 0.2

    assert requests == [
        ("/face/enroll", {"user_id": "user-1", "image": IMAGE, "camera_consent": True}),
        ("/face/verify", {"image": IMAGE, "reference_template_hash": "f" * 64}),
        ("/liveness/check", {"challenge_response": IMAGE, "challenge_type": "blink"}),
        ("/risk/assess", {"liveness_score": 0.77}),
    ]


@pytest.mark.parametrize(
    "response",
    [
        {"match_passed": "yes", "match_score": 0.9, "match_threshold": 0.85,
         "face_detected": True, "current_template_hash": "a" * 64},
        {"match_passed": True, "match_score": 1.5, "match_threshold": 0.85,
         "face_detected": True, "current_template_hash": "a" * 64},
        {"match_passed": True, "match_score": 0.9, "match_threshold": 0.85,
         "face_detected": False, "current_template_hash": ""},
        {"match_passed": True, "match_score": 0.9},
    ],
)
def test_face_client_rejects_malformed_upstream_responses(response):
    client = make_client(lambda request: httpx.Response(200, json=response))

    with pytest.raises(ApiError) as error:
        client.verify(IMAGE, "f" * 64)

    assert error.value.status_code == 503
    assert "image-data" not in str(error.value)


def test_face_client_maps_safe_image_errors_without_echoing_upstream_body():
    client = make_client(
        lambda request: httpx.Response(400, json={"detail": "invalid image: secret-image-material"})
    )

    with pytest.raises(ApiError) as error:
        client.liveness(IMAGE)

    assert error.value.status_code == 400
    assert "secret-image-material" not in str(error.value)


def test_face_client_maps_transport_failure_to_service_unavailable():
    def unavailable(request):
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(unavailable)

    with pytest.raises(ApiError) as error:
        client.assess({"liveness_score": 0.5})

    assert error.value.status_code == 503


def test_face_client_rejects_decoded_images_over_its_limit():
    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        base_url="http://face.test",
    )
    client = FaceServiceClient("http://face.test", client=http, max_image_bytes=2)

    with pytest.raises(ApiError) as error:
        client.verify("YWJj", "f" * 64)

    assert error.value.status_code == 400
