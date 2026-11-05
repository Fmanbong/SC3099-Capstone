# MODULE 4 - INSTRUCTOR DASHBOARD & OBSERVABILITY

this module integrates the existing streamlit dashboard with the supplied module 2's API. the dashboard attendance operations go through the API with the signed in user's token and it does not connect directly to POSTGRESQL.

## GETTING STARTED
1. make sure to have your backend , POSTGRESQL , and Redis up and running and then build or start module 4's services:
   ```bash
   docker compose -f docker-compose.yml -f module4-observability/compose.monitoring.yml up -d --no-deps --build dashboard prometheus grafana otel-collector
   ```
   note that `--no-deps` keeps this command from starting or rebuilding the other application modules.

2. open http://localhost:8501 and sign in with your existing instructor account. then make sure to use the administrator account for the audit logs.

- note , without running PROMETHEUS , the measures (metrics) page shows a clear unavailable message while the attendance functionality remains available.
- note that if you only need the attendance dashboard , the simpler existing command still works well:
   ```bash
   docker compose up -d --no-deps --build dashboard
   ```

## GETTING EVERYTHING UP & RUNNING
- MAKE DOCKER AVAILABLE IN THE CURRENT TERMINAL
   ```bash
   export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
   ```
- OPEN DOCKER AND MAKE SURE "ENGINE RUNNING" AND MAKE SURE `docker --version` WORKS AS INTENDED
- GO TO THE PROJECT ROOT DIRECTORY
- START EVERYTHING
   ```bash
   docker compose up -d --build
   ```
- CHECK THE SERVICES
   ```bash
   docker compose ps -a
   ```
   ```bash
   curl -i http://localhost:8000/health
   ```
- CREATE AN ADMIN
- open `http://localhost:8501` - use admin or instructor account
- open `http://localhost:3001` - this opens Grafana
- open `http://localhost:9090` - this opens Prometheus
- THIS REBUILDS THE DASHBOARD AFTER CHANGING ITS PYTHON , CSS , OR CONFIG FILES
   ```bash
   docker compose up -d --no-deps --build dashboard
   ```
