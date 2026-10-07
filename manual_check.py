"""Quick manual sanity check for Module 3 - run against the running service."""
import base64
import httpx

BASE = "http://localhost:8001"

def b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

obama = b64("sample_images/obama.jpg")
obama2 = b64("sample_images/obama2.jpg")
biden = b64("sample_images/biden.jpg")

client = httpx.Client(timeout=30)

print("== enroll (obama.jpg) ==")
r = client.post(f"{BASE}/face/enroll", json={
    "user_id": "demo", "image": obama, "camera_consent": True
})
print(r.status_code, r.json())
ref_hash = r.json()["face_template_hash"]

print("\n== verify same person, different photo (obama2.jpg) ==")
r = client.post(f"{BASE}/face/verify", json={
    "image": obama2, "reference_template_hash": ref_hash
})
print(r.status_code, r.json())

print("\n== verify different person (biden.jpg) ==")
r = client.post(f"{BASE}/face/verify", json={
    "image": biden, "reference_template_hash": ref_hash
})
print(r.status_code, r.json())

print("\n== liveness passive check (obama.jpg) ==")
r = client.post(f"{BASE}/liveness/check", json={
    "challenge_response": obama, "challenge_type": "passive"
})
print(r.status_code, r.json())

print("\n== risk assess (good signals) ==")
r = client.post(f"{BASE}/risk/assess", json={
    "liveness_score": 0.9, "face_match_score": 0.9,
    "ip_address": "8.8.8.8", "user_agent": "Mozilla/5.0"
})
print(r.status_code, r.json())