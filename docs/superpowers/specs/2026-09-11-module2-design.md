# Module 2 backend design

Implement the user's supplied REST/JWT/database/rate-limit/error-handling requirements and existing SAIV contracts. Work in the existing development checkout; preserve unrelated changes. No publishing or merging is required.

## Architecture and contracts

- FastAPI under `/api/v1`; SQLAlchemy 2 models with UUID strings, timezone-aware timestamps, foreign keys and uniqueness constraints. PostgreSQL in deployment, SQLite only for isolated automated tests/local smoke testing. Alembic owns schema migrations.
- Explicit serializers exclude passwords, JWTs, face templates and raw images from general responses. Errors contain `detail`, `code`, and a request ID; validation errors enumerate fields without echoing submitted passwords/images.
- Authentication: bcrypt >=10 rounds; HS256 access tokens (1 hour), refresh tokens (7 days), distinct token types and unique IDs. Store hashed refresh tokens in the database with atomic rotation/reuse protection. Logout revokes the login session; deactivated users cannot continue with old access tokens.
- Normal registration creates students only. A clearly named `public_test_mode`, allowed only with `environment=test`, accommodates provided fixtures that register privileged users and omit mandatory evidence. It also disables broad registration/login/API quotas only in that isolated compatibility environment. Normal application checks remain strict. Dedicated security tests exercise normal mode.
- Instructor/TA permissions are constrained by CourseStaff assignments or session ownership. Administrators manage assignments. Public-test-mode compatibility permits fixture instructors to operate fixture-created courses, without changing production authorization.
- Preserve documented compatibility routes (`PUT /users/me`, `/sessions/active`, `/checkins/my-checkins`, `/devices/register`) and add aliases only where existing consumers need them. Collection pagination uses `items,total,limit,offset`; active sessions, own check-ins/devices/enrollments and session check-ins retain their documented arrays. Flagged check-ins use the public tests' paginated contract.

## Check-in policy

Resolve the student from authentication. Validate enrollment, account/course/session activity and server UTC time before expensive work. Validate coordinate ranges, consent, image size and evidence required by the session. Compute distance using the session's venue overrides then course defaults. Reject >2x geofence radius; flag outside radius. Required failed liveness/face match rejects regardless of aggregate risk. Missing required evidence never auto-approves. Enforce one check-in per student/session at the database layer, returning documented 400 on duplicate.

Module 3 is currently in `origin/arnav`. Integrate its exact `/face/enroll`, `/face/verify`, `/liveness/check`, `/risk/assess` contracts through an injectable HTTP client. Never persist images or submit request bodies to logs/telemetry. Invalid/timeout service responses produce safe errors, not approval. Backend verifies device challenge signatures independently; fingerprints alone are untrusted. Backend chooses the final status using course/session thresholds, not Module 3's fixed pass_threshold.

## Operations

Redis atomically enforces login 60/hour/IP, registration 10/hour/IP, API 1000/hour/user and check-in 10/minute/user, with Retry-After on 429. Record append-only audit events with database enforcement against updates; retain audit metadata without raw images, credentials or tokens. Scheduled retention deletes expired check-ins/risk signals and eligible user data. Expose health and Prometheus metrics. Provide explicit admin bootstrap, configuration, run instructions and compatibility decisions.

## Verification

Start from failing auth/public-contract tests; add negative tests for token type/reuse/revocation, privilege escalation, ownership, concurrent duplicate check-ins, consent, service failures, critical-risk overrides, sanitization and actual Redis rate limiting. Exercise Module 3 HTTP contracts with controlled external-service responses, distinguish these from real-model validation. Run existing backend-facing public suites against a dedicated test-mode service, and PostgreSQL/Redis integration checks when Docker is available. Document all remaining environmental limits.
