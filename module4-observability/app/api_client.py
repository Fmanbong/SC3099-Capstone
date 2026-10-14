
""" commentary
- mod 4 HTTP API - backend contracts & safe errors
"""

import logging , math , os , re , requests
from contextlib import contextmanager ; from functools import lru_cache

@lru_cache(maxsize=1)
def _tracer():
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")
    if  not endpoint:
        return None  # tracing is optional and attendance does not depend on it
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logging.getLogger(__name__).warning(
            "tracing was configured but optional packages are unavailable so tracing is disabled"
        )
        return None
    provider = TracerProvider(resource=Resource.create({"service.name": "saiv-module4"}))
    provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=3)
    ))
    return provider.get_tracer("saiv.module4")

@contextmanager
def request_span(method, endpoint):
    tracer = _tracer()
    if  tracer is None:
        yield None
        return
    # UUIDs and query strings are not telemetry attributes
    route = re.sub(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", "{id}", endpoint.split("?")[0])
    with tracer.start_as_current_span(
         f"{method} {route}", attributes={"http.method": method, "http.route": route},
         record_exception=False, set_status_on_exception=False,
    ) as span:
         yield span

class APIError(Exception):
    """ an action error that does not include request bodies or credentials
    """

    def __init__(self, message, *, status=0, code=None, request_id=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id , self.retry_after = request_id , retry_after

class APIClient:

    """ one client per streamlit session
    and tokens remain in the server side memory
    """

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.token = None
        self.refresh_token = None
        self.user = None
        self.timeout = (5, 15)  # the first is connection timeout and then the second is response read timeout
        self.session = requests.Session()

    def set_token(self, token):
        self.token = token

    def clear_auth(self):
        self.token = self.refresh_token = self.user = None
        self.session.cookies.clear()

    @staticmethod
    def _error(response):
        defaults = {
            401: "your login has expired. sign in again",
            403: "your account does not have permission for this action",
            404: "the requested record no longer exists",
            429: "too many requests. wait before trying again",
            503: "a required backend service is unavailable",
        }
        message = defaults.get(response.status_code, "the backend could not complete the request")
        code = request_id = None
        try:
            body = response.json()
            if  isinstance(body, dict):
                code , request_id = body.get("code") , body.get("request_id")
                detail = body.get("detail")
                if   response.status_code <  500 and isinstance(detail, str):
                     message  = detail[:1200]
                elif response.status_code == 422 and isinstance(detail, list):
                     messages = []
                     for item in detail:
                         if  isinstance(item, dict) and isinstance(item.get("msg"), str):
                             field = ".".join(str(part) for part in item.get("loc", []) if part != "body")
                             messages.append(f"{field}: {item['msg']}")
                     if  messages:
                         message = "; ".join(messages)[:1200]
        except ValueError:
            pass  # do not display upstream HTML error or raw response
        return APIError(
            message , status = response.status_code, code = code,
            request_id = request_id or response.headers.get("X-Request-ID"),
            retry_after = response.headers.get("Retry-After"),
        )

    def _request(self, method, endpoint, *, data=None, params=None, authenticated=True, retry_auth=True, allowed_statuses=()):
        headers = {}
        if  authenticated and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            # telemetry records only method/route/status and never headers or payloads
            with request_span(method, endpoint) as span:
                 response = self.session.request(
                    method, f"{self.base_url}{endpoint}", json=data, params=params, headers=headers, timeout=self.timeout,
                )
                 if  span is not None:
                     span.set_attribute("http.status_code", response.status_code)
        except requests.Timeout:
            raise APIError(
                "the backend request timed out. for the create , edit or review , "
                "refresh the records before submitting again and the action may have completed",
                code="TIMEOUT") from None
        except requests.RequestException:
            raise APIError("cannot reach the backend. check that it is running", code="CONNECTION_ERROR") from None

        if  response.status_code == 401 and authenticated:
            if  retry_auth and self.refresh_token:
                self.refresh()
                return self._request(method, endpoint, data=data, params=params, authenticated=True, retry_auth=False, allowed_statuses=allowed_statuses)
            self.clear_auth()
        if  not response.ok and response.status_code not in allowed_statuses:
            raise self._error(response)
        return response

    @staticmethod
    def _json(response):
        if  response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise  APIError("the backend returned an invalid JSON response", code="INVALID_RESPONSE") from None

    def _store_tokens(self, data):
        if  not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
            self.clear_auth()
            raise APIError("the backend returned an invalid login response", code="INVALID_RESPONSE")
        self.token = data["access_token"]
        self.refresh_token = data.get("refresh_token")

    def login(self, email, password):
        self.clear_auth()
        data = self._json(self._request(
            "POST", "/api/v1/auth/login", data={"email": email, "password": password}, authenticated=False
        ))
        self._store_tokens(data)
        if  not isinstance(data.get("user"), dict):
            self.clear_auth()
            raise APIError("the login response has no user profile", code="INVALID_RESPONSE")
        self.user = data["user"]
        return data

    def refresh(self):
        if  not self.refresh_token:
            self.clear_auth()
            raise APIError("your login has expired. sign in again", status=401)
        try:
            data = self._json(self._request(
                "POST", "/api/v1/auth/refresh", data={"refresh_token": self.refresh_token}, authenticated=False
            ))
            # refresh tokens rotate . keep the new token and never reuse the old one
            self._store_tokens(data)
        except APIError as exc:
            self.clear_auth()
            raise APIError("your login could not be renewed. sign in again", status=401,
                            code=exc.code, request_id=exc.request_id) from None
        return data

    def logout(self):
        try:
            if  self.token:
                self._request("POST", "/api/v1/auth/logout")
        finally:
            self.clear_auth()

    def get_current_user(self):
        self.user = self.get("/api/v1/users/me")
        return self.user

    def get(self, endpoint, params=None):
        return self._json(self._request("GET",   endpoint, params=params))

    def post(self, endpoint, data=None):
        return self._json(self._request("POST",  endpoint, data=data))

    def patch(self, endpoint, data=None):
        return self._json(self._request("PATCH", endpoint, data=data))

    def delete(self, endpoint):
        return self._request("DELETE", endpoint)

    def download(self, endpoint, params=None):
        # the csv exports cannot be parsed as JSON
        return self._request("GET", endpoint, params=params).content

    @staticmethod
    def page(data):
        if (not isinstance(data, dict) or not isinstance(data.get("items"), list)
                or not isinstance(data.get("total"), int)):
            raise APIError("the backend returned an invalid paginated response", code="INVALID_RESPONSE")
        return data

    def _all_pages(self, endpoint, params=None):
        rows , offset = [] , 0
        for _ in range(100):
            data = self.page(self.get(endpoint, {**(params or {}), "limit": 100, "offset": offset}))
            rows.extend(data["items"])
            if  len(rows) >= data["total"]:
                return rows
            if  not data["items"]:
                break
            offset += len(data["items"])
        raise APIError("the full selector list could not be loaded. try refreshing or use a narrower scope")

    def get_courses(self, instructor_id=None, limit=100, offset=0):
        params = {"limit": limit, "offset": offset}
        if  instructor_id is not None:
            params["instructor_id"] = instructor_id
        return self.page(self.get("/api/v1/courses/", params))

    def get_all_courses(self, instructor_id=None):
        params = {"instructor_id": instructor_id} if instructor_id is not None else {}
        return self._all_pages("/api/v1/courses/", params)

    def get_course(self, course_id):
        return self.get(f"/api/v1/courses/{course_id}")

    def get_sessions(self, limit=50, offset=0, **filters):
        return self.page(self.get("/api/v1/sessions/", {"limit": limit, "offset": offset, **filters}))

    def get_all_sessions(self):
        return self._all_pages("/api/v1/sessions/")

    def get_session(self, session_id):
        return self.get(f"/api/v1/sessions/{session_id}")

    def create_session(self, data):
        return self.post("/api/v1/sessions/", data)

    def update_session(self, session_id, data):
        return self.patch(f"/api/v1/sessions/{session_id}", data)

    def delete_session(self, session_id):
        return self.delete(f"/api/v1/sessions/{session_id}")

    def get_checkins(self, status=None, session_id=None, limit=50, offset=0, review_only=False, **filters):
        params = {"limit": limit, "offset": offset, **filters}
        if  session_id is not None:
            params["session_id"] = session_id
        if  review_only:
            endpoint = "/api/v1/checkins/flagged"
        else:
            endpoint = "/api/v1/checkins/"
            if  status is not None:
                params["status"] = status
        return self.page(self.get(endpoint, params))

    def get_checkin(self, checkin_id):
        return self.get(f"/api/v1/checkins/{checkin_id}")

    def get_session_checkins(self, session_id):
        data = self.get(f"/api/v1/checkins/session/{session_id}")
        if  not isinstance(data, list):
            raise APIError("the backend returned an invalid session check in list")
        return data

    def get_flagged_checkins(self, **filters):
        return self.get_checkins(review_only=True, **filters)

    def review_checkin(self, checkin_id, status, review_notes):
        return self.post(f"/api/v1/checkins/{checkin_id}/review", {"status": status, "review_notes": review_notes})

    def get_overview_stats(self, course_id=None, days=7):
        params = {"days": days}
        if  course_id is not None:
            params["course_id"] = course_id
        return self.get("/api/v1/stats/overview", params)

    def get_session_stats(self, session_id):
        return self.get(f"/api/v1/stats/sessions/{session_id}")

    def get_course_stats(self, course_id, **filters):
        return self.get(f"/api/v1/stats/courses/{course_id}", filters)

    def get_student_stats(self, student_id):
        return self.get(f"/api/v1/stats/students/{student_id}")

    def get_audit_logs(self, limit=100, offset=0, **filters):
        return self.page(self.get("/api/v1/audit/", {"limit": limit, "offset": offset, **filters}))

    def get_audit_summary(self, days=7):
        return self.get("/api/v1/audit/summary", {"days": days})

    def export_attendance(self, course_id, **filters):
        return self.download(f"/api/v1/export/attendance/{course_id}", {"format": "csv", **filters})

    def export_session(self, session_id):
        return self.download(f"/api/v1/export/session/{session_id}", {"format": "csv"})

    def get_health(self):
        response = self._request("GET", "/health", authenticated=False, allowed_statuses=(503,))
        data = self._json(response)
        if  not isinstance(data, dict):
            raise APIError("the health endpoint returned invalid data")
        return data

# PROMETHEUS IS READ ONLY MONITORING SERVICE WHICH IS SEPARATE FROM MOD 2 LOGIN
# BUT WE USE MOD 2 METRIC NAMES AND EXCLUDE MONITORING PROBES FROM TRAFFIC
TRAFFIC  = 'saiv_http_requests_total{job="saiv-backend",path!~"/metrics|/health"}'
DURATION = 'saiv_http_request_duration_seconds_bucket{job="saiv-backend",path!~"/metrics|/health"}'
QUERIES  = {
    "requests / second": f"sum(rate({TRAFFIC}[5m]))",
    "successful responses (%)": (
        '100 * sum(rate(saiv_http_requests_total{job="saiv-backend",'
        'path!~"/metrics|/health",status=~"2..|3.."}[5m])) / '
        f"sum(rate({TRAFFIC}[5m]))"
    ),
    "error responses (%)": (
        '100 * sum(rate(saiv_http_requests_total{job="saiv-backend",'
        'path!~"/metrics|/health",status=~"4..|5.."}[5m])) / '
        f"sum(rate({TRAFFIC}[5m]))"
    ),
    "response p95 (ms)": f"1000 * histogram_quantile(0.95, sum by (le) (rate({DURATION}[5m])))",
}

class PrometheusClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.timeout  = (3, 8)

    def _get(self, path, params=None):
        try:
            response = requests.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError):
            raise APIError("prometheus is unavailable. attendance pages can still be used") from None
        if  not isinstance(data, dict) or data.get("status") != "success":
            raise APIError("prometheus returned an invalid query result")
        return data.get("data", {})

    def scalar(self, query):
        data = self._get("/api/v1/query", {"query": query})
        values = data.get("result", [])
        if  not values:
            return None
        try:
            value = float(values[0]["value"][1])
        except (ValueError, TypeError, KeyError, IndexError):
            raise APIError("prometheus returned an invalid metric value") from None
        return value if math.isfinite(value) else None

    def targets(self):
        data = self._get("/api/v1/targets", {"state": "active"})
        return [
            {"Job": item.get("labels", {}).get("job", "Unknown"),
             "Health": item.get("health", "unknown"),
             "Last scrape": item.get("lastScrape"),
             "Error": item.get("lastError", "")}
            for item in data.get("activeTargets", [])
        ]
