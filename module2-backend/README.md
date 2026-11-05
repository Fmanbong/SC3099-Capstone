# Module 2 — Backend API

FastAPI + SQLAlchemy + PostgreSQL + Redis implementation of the existing
[API specification](../docs/API-SPECIFICATION.md). All application routes use
`/api/v1`; interactive documentation is available at `/docs` and the schema at
`/openapi.json`.

## Run with Docker

From the repository root, generate a local signing secret and start the backend:

```bash
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
docker compose up -d --build postgres redis backend
docker compose exec backend python -m app.cli create-admin --email admin@example.com --name Administrator
```

The administrator command prompts for a password. For unattended provisioning,
it also accepts `SAIV_BOOTSTRAP_PASSWORD` from the environment. It never upgrades
an existing account. Public registration creates students only; an administrator
can create instructors/TAs with `POST /api/v1/admin/users/bulk`.

The backend listens on port **8000**. Docker startup runs `alembic upgrade head`
before Uvicorn. Keep the same secret in your local, ignored root `.env` if you
want sessions to survive restarts. Do not use the example/test signing secrets.

The current branch does not contain `module3-face-recognition/`. Its implementation
is available in the local `origin/arnav` ref. After that module is integrated,
start it with `docker compose up -d --build face-recognition`. Backend identity,
course management, reporting and image-free sessions work independently; required
face verification returns **503** while Module 3 is unavailable.

## Run locally

Python 3.11+ is required. Start PostgreSQL and Redis from the root compose file;
their local ports are **5434** and **6380**, respectively.

```bash
docker compose up -d postgres redis
cd module2-backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set `SECRET_KEY` in `.env` to a random value of at least 32 characters, then run:

```bash
alembic upgrade head
python -m app.cli create-admin --email admin@example.com
uvicorn app.main:app --reload --port 8000
```

Docker uses `http://face-recognition:8001`; local development uses
`FACE_SERVICE_URL=http://localhost:8001`. CORS defaults match the existing student
PWA (`localhost:3000`) and dashboard (`localhost:8501`).

## Implemented resources

There are **59 documented application method/path combinations**, including
compatible PUT/PATCH variants, plus `/health` and `/metrics`. Slash aliases are
omitted from this count.

| Resource | Methods | Behavior |
| --- | ---: | --- |
| Authentication | 4 | Register, login, rotate refresh token, logout/revoke session |
| Users | 8 | Own profile/consent, face enrollment, admin user queries/updates, deletion request |
| Administration | 5 | Activate/deactivate, bulk users, session status, enrollment |
| Courses | 7 | Queries, creation, updates, soft deletion, teaching staff assignment |
| Sessions | 8 | CRUD, active/my sessions, status transitions and check-in windows |
| Enrollments | 5 | Own/course lists, create/reactivate, bulk import, soft removal |
| Devices | 6 | Register/list/update/revoke, one-use signing challenges |
| Check-ins | 8 | Submit, own/session/filtered lists, flagged queue, detail, appeal, review |
| Statistics | 4 | Overview, session, course and student attendance |
| Audit | 2 | Filtered immutable history and summary |
| Export | 2 | Course/session attendance as CSV or JSON |

After creating a course, an administrator assigns instructors/TAs with
`PUT /api/v1/courses/{course_id}/staff`, body `{"user_ids":["staff-uuid"]}`.
Teaching access is checked against course assignment or session ownership.
Closed enrollment periods are archived when a student rejoins, so historical
attendance uses the roster for each session rather than today's roster.
Students may preview session metadata, but submitting attendance requires active
enrollment, an active course/session and an open check-in window. Administrators
and the owning instructor manage sessions; TAs have scoped review/read access.

## Authentication, errors and storage

- HS256 access tokens expire after one hour; refresh tokens after seven days.
  Refresh tokens rotate on use. Reusing an old refresh token revokes its login
  session, including access tokens. Logout, deactivation and role changes also
  invalidate affected login sessions. Only refresh-token digests are persisted.
- Passwords use bcrypt. No password hashes, face hashes or device public keys
  appear in ordinary resource responses.
- Ten consecutive incorrect passwords lock an existing account: the first nine
  failures return 401, the tenth and all later login attempts return 429
  (`ACCOUNT_LOCKED`), including attempts with the correct password. Successful
  login before lockout resets the counter. This state is persisted in PostgreSQL,
  independent of IP quotas and public-test compatibility, with no automatic expiry.
- Redis atomically limits login and registration to 100,000/hour/IP,
  authenticated API traffic to 1,000/hour/user, and check-ins to 10/minute/user.
  Exceeding a limit returns 429 with `Retry-After`; a protection-service outage
  returns 503. Login/registration quotas remain active in public-test mode.
  Per the grading contract, client IP is the first `X-Forwarded-For` entry when
  present, otherwise the socket address. A deployment proxy should overwrite
  incoming forwarded headers so callers cannot choose a false client address.
- Errors use `{"detail":...,"code":...,"request_id":...}`. Validation failures
  list all field errors without echoing request input. Responses carry
  `X-Request-ID`; server error logs contain stack locations without request bodies,
  credentials or upstream response contents.
- UUID identifiers, foreign keys, indexes and database uniqueness constraints
  protect relationships, duplicate enrollments and duplicate check-ins. Alembic
  migrations create the schema. SQLite is supported for isolated unit tests.
- Check-ins expire after the configured retention period (default 30 days).
  Deleted users are deactivated immediately and scheduled for removal after
  30 days. `python -m app.cli cleanup` runs the same cleanup as the hourly job.
  Audit records are retained; database triggers reject edits/deletion, allowing
  only the foreign-key unlink when a referenced user is actually removed.
- `/health` checks the database schema and Redis. `/metrics` exposes Prometheus
  request counts/durations using route patterns, without user identifiers.

### Account migration and recovery

Run `alembic upgrade head` before starting an updated backend (the Docker command
already does this). Migration `0004_login_failures` adds the failure counter with
zero as the default for existing users. The API has no automatic unlock or unlock
UI. After investigating a lockout, a database operator can explicitly reset
`users.failed_login_attempts` to zero for the selected account, for example:

```bash
docker compose exec postgres psql -U saiv -d saiv -c "UPDATE users SET failed_login_attempts = 0 WHERE email = 'student@example.com';"
```

### Singapore-only attendance

Every check-in must pass both offline country checks before session/face processing:
GPS inside Singapore's land polygons and a Singapore public IP or ordinary
private/local IPv4/IPv6 address. Outside GPS returns 403 `GPS_OUTSIDE_SINGAPORE`;
outside/noneligible IP returns 403 `IP_OUTSIDE_SINGAPORE`; invalid client-IP syntax
returns 403 `CLIENT_IP_INVALID`. The first forwarded address takes precedence over
the socket, including for the IP passed downstream to Module 3. Private IPs never
bypass GPS. These gates apply in public-test mode and with face checks disabled;
the classroom geofence still applies afterward.

The source data ships under `app/data`; there is no runtime GeoIP network request
or extra environment variable. Missing/corrupt data prevents startup. See
[data notices and update instructions](app/data/NOTICE.md) for licenses, checksums,
monthly IP refreshes, boundary provenance and accuracy/reclaimed-land limitations.

## Module 1 / 3 / 4 integration

Existing frontend paths, bearer headers, response fields, list shapes and partial
`PUT /users/me` are preserved. PATCH variants are also available. Ordinary
management lists use `{items,total,limit,offset}`; active sessions and personal
check-in/device/enrollment lists remain arrays.
The existing Module 4 API client's mixed indentation has been normalized so it
can import and call these paths. Its dashboard pages remain the responsibility
of Module 4.

`app/services/face.py` uses the actual `origin/arnav` request/response contract:

| Backend step | Module 3 endpoint | Stored evidence |
| --- | --- | --- |
| Enroll own face | `POST /face/enroll` | `face_template_hash` mapped to `User.face_embedding_hash` |
| Evaluate submitted image | `POST /liveness/check` | Score/pass result; optional template hash |
| Compare enrolled identity | `POST /face/verify` | Score/pass result; optional template hash |
| Assess combined signals | `POST /risk/assess` | Risk score and backend evidence reasons |

Images are processed in request memory and are never written to a Module 2 table,
file, Redis key or audit event. The backend rejects client-supplied identity or
risk scores, validates the upstream contract, limits image size, and applies
timeouts. It independently checks venue distance and device signatures.
Required failed face/liveness evidence or very high risk rejects a check-in;
incomplete device proof and borderline risks go to manual review. A duplicate
submission cannot create a second record.

The existing PWA sends a fingerprint without signing a challenge. Registration
still works, but device-bound attendance is **flagged**, not automatically trusted.
The extension is `POST /devices/{device_id}/challenge` with `session_id`; sign the
returned nonce and submit `device_challenge_id` plus base64 `device_signature`.
ECDSA P-256 DER/WebCrypto signatures and RSA signatures are supported.

The backend provides `/auth/logout`; the existing PWA currently only clears local
tokens on logout. Module 1 should call this endpoint to also revoke its server
session. Its existing offline queue also persists image payloads locally: the
Module 2 storage guarantees above do not make that frontend behavior compliant.

Module 3 model accuracy and spoof resistance require real-image/attack testing.
Backend contract tests do not establish those properties. A similarity hash is
still biometric data, and single-image liveness does not prove an interactive
challenge was completed.

## Verification

Verified on 2026-09-11: **68/68 backend regression tests passed** with the isolated
PostgreSQL and Redis checks enabled; **81 backend-facing public tests passed,
1 pre-existing skipped test**, against the built Docker image in explicit public
test mode. Alembic upgrade/schema checks, container health, and the existing
Module 4 API client's login/query/statistics/audit/CSV calls also passed. Module 3
calls were verified with HTTP contract tests, not a live recognition model.

Normal-security tests (from this directory):

```bash
pytest -q tests
```

Four infrastructure tests require **dedicated test instances** and are skipped
unless their URLs are explicitly supplied:

```bash
SAIV_TEST_REDIS_URL=redis://localhost:56380/0 \
SAIV_TEST_DATABASE_URL=postgresql://saiv:saiv_test_password@localhost:55434/saiv \
pytest -q tests
```

Migrate the isolated PostgreSQL database first with `DATABASE_URL=<test-url>
alembic upgrade head`. These checks exercise real Redis concurrency/TTL behavior
and PostgreSQL audit immutability/foreign-key cleanup.

The repository's original HTTP fixtures register privileged roles directly,
leave students' consent/evidence unset, and generate many accounts from one IP.
For **isolated public tests only**, run a separate backend/database with
`ENVIRONMENT=test PUBLIC_TEST_MODE=true`. This explicitly enables fixture role
creation, broad teaching access, forced session states, and relaxed fixture quotas.
Missing required evidence is still recorded as flagged with null verification
scores. Check-in limits remain enabled. The setting is rejected in development
and production and must never be used for shared deployments.

From the repository root, against that isolated HTTP server:

```bash
PYTHONPATH=tests TEST_BACKEND_URL=http://localhost:58000 pytest -q \
  tests/public/test_api_functional.py tests/public/test_security_basic.py \
  tests/public/test_privacy_basic.py tests/public/test_frontend_dashboard.py \
  tests/public/test_observability.py tests/public/test_performance.py \
  tests/public/test_integration.py
```

These are backend-facing public checks. The separate Module 3 face suite and a
real browser/camera flow require the corresponding services and hardware.
