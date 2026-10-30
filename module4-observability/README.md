# Module 4 — instructor dashboard and monitoring

Module 4 contains the Streamlit instructor/admin dashboard, Prometheus configuration and Grafana panels. It uses the Module 2 API for authentication, course permissions, attendance and audit records.

## Start the services

Open Docker Desktop and wait for **Engine running**. From the repository root:

```bash
docker compose up -d --build postgres redis face-recognition backend prometheus grafana dashboard
curl -i http://localhost:8000/health
```

The health request should return HTTP 200.

| Service | Local address |
| --- | --- |
| Instructor dashboard | http://localhost:8501 |
| Backend API documentation | http://localhost:8000/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 |

For a first installation, copy the root `.env.example` to `.env` and set a random `SECRET_KEY` of at least 32 characters. Keep an existing working secret unchanged. Follow `module2-backend/README.md` to create administrator/instructor accounts, course assignments and enrollment. Normal operation uses `PUBLIC_TEST_MODE=false`.

The dashboard accepts instructor and administrator accounts. Instructors need active course assignments to create sessions and access course records. Students cannot enter the dashboard. Audit logs require an administrator account.

Grafana has a separate login. A new Grafana data volume uses the credentials configured in Compose; restarting Grafana does not reset an existing password. Open **SAIV → SAIV · Operations** for the provisioned dashboard.

## Rebuild after changing Module 4

```bash
docker compose up -d --no-deps --build dashboard
docker compose restart grafana
```

Reload the dashboard after rebuilding. Inspect service failures with `docker compose ps -a` and `docker compose logs --tail=60 dashboard`. For API failures, inspect the backend logs. Retain existing Docker volumes to preserve saved records.

## Configuration

| File or setting | Purpose |
| --- | --- |
| `BACKEND_URL` | API base URL; Compose connects the dashboard to `http://backend:8000`. |
| `PROMETHEUS_URL` | Prometheus base URL; Compose uses `http://prometheus:9090`. |
| `.streamlit/config.toml` | Dark theme and native widget colors. |
| `app/assets/dashboard.css` | Interface styling and local Inter font loading. |
| `app/assets/InterVariable.woff2` | Bundled Inter 4.1 variable font. |
| `prometheus.yml` | Metrics scrape targets and interval. |
| `grafana/provisioning/datasources/prometheus.yml` | Provisioned Prometheus datasource. |
| `grafana/provisioning/dashboards/provider.yml` | Grafana dashboard provider and source directory. |
| `grafana/provisioning/dashboards/json/saiv-operations.json` | Operations dashboard panels and queries. |

Keep credentials outside the public theme file. `.streamlit/secrets.toml` and local `.update-backups/` files are ignored by Git. Optional tracing in the API client requires an OTLP endpoint and compatible OpenTelemetry packages; attendance requests do not depend on tracing.

## Interface

The dashboard uses a dark neutral palette, a local Inter font, summary cards and compact filters. Inter is bundled with its [SIL Open Font License](app/assets/Inter-LICENSE.txt); font loading does not require an external service or a system font installation.

Session details are divided into **Attendance** and **Settings** tabs. Venue settings, verification evidence and event details are available in expandable sections. CSV preparation and review actions retain their confirmation controls. The Overview review shortcut opens the existing review queue.

The Metrics page opens Grafana with `theme=dark`. Grafana uses its native interface font and panel controls. Provisioned panels are configured in their JSON source.

## Attendance review

**Request re-check** is available only for an active course and active session inside its check-in window. The dashboard fetches current state again on submission. If a previously selected re-check expires, it clears the decision and confirmation. Approval and rejection remain available for flagged or appealed records after the window closes.

An existing pending record outside a valid window displays a warning. The Module 2 `POST /api/v1/checkins/{id}/reverify` endpoint must also validate course/session/window eligibility before changing status. The dashboard check cannot enforce rules for other API clients or make its final read and mutation atomic. Resolving a record already stranded as pending requires an authorized, audited backend operation.

CSV downloads contain prepared snapshots. After saving a review decision or changing session data, prepare a new export to include the saved result.

## Monitoring and refresh

- Prometheus scrapes backend metrics every 15 seconds.
- Grafana displays backend scrape status, request rate, successful-response percentage, error-response percentage, request-rate trends and p95 response time.
- Rate queries use five-minute windows and exclude `/metrics` and `/health` probes.
- Missing samples or no application traffic produce **No data**. Measured traffic with no errors produces **0%** errors.
- A successful metrics scrape does not establish database, Redis or recognition health. Use the backend health endpoint and relevant service checks.
- Grafana refreshes every 30 seconds. The optional dashboard checkbox refreshes Overview, Check-ins and Metrics every 30 seconds. Session-editing and audit pages do not automatically refresh.

## Automated tests

The tests run the dashboard against an isolated temporary SQLite backend with normal role checks and disabled rate limiting. They cover login, permissions, session operations, review decisions, counts, exports and re-check window boundaries. Docker, a shared database, camera hardware, face recognition and a running Prometheus instance are outside this test suite.

From the repository root, use a project-local environment:

```bash
python3 -m venv .venv-tests
.venv-tests/bin/python -m pip install -r module2-backend/requirements.txt -r module4-observability/requirements.txt
.venv-tests/bin/python module4-observability/tests/smoke_dashboard.py
```

The test-only serializer adapter supports formatted selectboxes in Streamlit 1.30's AppTest implementation.

## Integration checks

1. Sign in as an assigned instructor and check authorized sessions and attendance records.
2. Confirm student access is refused and audit logs require an administrator.
3. Request a re-check during an open window; confirm it is unavailable after closure while approval/rejection remain available for flagged or appealed records.
4. Save a review decision, prepare a fresh CSV and compare the saved status and attendance count.
5. Check Metrics and Grafana with application traffic and verify the optional dashboard refresh.
6. Check a desktop and narrow window, then restart services without deleting volumes and confirm saved data persists.

Inspect `git status` and `git diff --check` before committing. Stage intended source/configuration files and keep local copy files, credentials and backups out of the commit.
