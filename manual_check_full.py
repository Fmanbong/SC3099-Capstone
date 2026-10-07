"""Test every pairwise combination across all sample images to validate the match threshold."""
import base64
import itertools
import httpx

BASE = "http://localhost:8001"

def b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

IMAGES = {
    "obama": "sample_images/obama.jpg",
    "obama2": "sample_images/obama2.jpg",
    "obama3": "sample_images/obama3.jpg",
    "obama_partial": "sample_images/obama_partial_face.jpg",
    "obama_partial2": "sample_images/obama_partial_face2.jpg",
    "biden": "sample_images/biden.jpg",
}
SAME_PERSON = {"obama", "obama2", "obama3", "obama_partial", "obama_partial2"}

client = httpx.Client(timeout=30)
encoded = {name: b64(path) for name, path in IMAGES.items()}

# Enroll each image once to get its hash
hashes = {}
for name, img in encoded.items():
    r = client.post(f"{BASE}/face/enroll", json={
        "user_id": name, "image": img, "camera_consent": True
    })
    if r.status_code != 201:
        print(f"{name}: enroll failed - {r.status_code} {r.text}")
        continue
    hashes[name] = r.json()["face_template_hash"]

print(f"\n{'Pair':<35}{'Expected':<12}{'Match?':<10}{'Score':<10}{'Result'}")
print("-" * 80)

mismatches = []
for a, b in itertools.combinations(hashes.keys(), 2):
    r = client.post(f"{BASE}/face/verify", json={
        "image": encoded[b], "reference_template_hash": hashes[a]
    })
    data = r.json()
    same_person_expected = (a in SAME_PERSON) == (b in SAME_PERSON)
    got_match = data["match_passed"]
    ok = same_person_expected == got_match
    if not ok:
        mismatches.append((a, b))
    label = f"{a} vs {b}"
    expected = "MATCH" if same_person_expected else "NO MATCH"
    print(f"{label:<35}{expected:<12}{str(got_match):<10}{data['match_score']:<10.4f}{'OK' if ok else 'WRONG!'}")

print("-" * 80)
if mismatches:
    print(f"\n{len(mismatches)} WRONG result(s): {mismatches}")
    print("Threshold may need adjusting, or these specific images are edge cases.")
else:
    print(f"\nAll {len(hashes)*(len(hashes)-1)//2} pairs classified correctly at threshold 0.85.")