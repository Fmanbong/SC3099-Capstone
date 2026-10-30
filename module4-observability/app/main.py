
"""Streamlit interface for attendance, sessions and monitoring."""
import csv , io , json , math , os
import pandas as pd
import plotly.express as px
import streamlit as st
from api_client import APIClient, APIError, PrometheusClient, QUERIES
from ui import apply_theme, brand, profile, detail_header, metadata, empty_state, service_states, panel
from datetime   import datetime, time, timedelta, timezone
from uuid       import UUID
from zoneinfo   import ZoneInfo


try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

st.set_page_config(page_title="SAIV instructor dashboard", layout="wide")
BACKEND_URL    = os.getenv("BACKEND_URL",    "http://localhost:8000")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")

LOCAL_TZ = ZoneInfo("Asia/Singapore")  # Interpret form inputs and display timestamps in Singapore time.
TRANSITIONS     = {"scheduled": ["active", "cancelled"], "active": ["closed", "cancelled"],
                   "closed": ["cancelled"], "cancelled": []}
CHECKIN_COLUMNS = ["id", "student_name", "student_email", "course_code", "session_name", "status", "checked_in_at", "risk_score"]
RECHECK         = "request re-check"
ACCENT          = "#b9acfa"
STATUS_LABELS   = {"approved": "Approved", "rejected": "Rejected", "flagged": "Flagged",
                   "appealed": "Appealed", "pending": "Re-check requested"}
STATUS_COLOURS  = {"approved": "#9acbb7", "flagged": "#d4bb8a", "rejected": "#d89aa6",
                   "pending": ACCENT, "appealed": "#9daed2"}

def dashboard_style():
    apply_theme()

def chart(figure, *, height=300):
    figure.update_layout(
        template="plotly_dark", height=height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "JetBrains Mono, sans-serif", "color": "#9a9fad", "size": 11},
        margin={"l": 8, "r": 12, "t": 20, "b": 16},
        colorway=[ACCENT, "#9acbb7", "#9daed2", "#d4bb8a", "#d89aa6"],
        legend={"title_text": "", "orientation": "h", "y": -0.22},
        hoverlabel={"bgcolor": "#292b35", "font_family": "JetBrains Mono, sans-serif", "font_size": 12},
    )
    figure.update_traces(marker_line_width=0)
    figure.update_xaxes(showgrid=False, title_text="", tickfont_size=10)
    figure.update_yaxes(gridcolor="#2a2d36", zeroline=False, title_text="", tickfont_size=10, automargin=True)
    st.plotly_chart(figure, use_container_width=True, config={"displayModeBar": False})

def page_intro(title, description):
    st.header(title)
    st.caption(description)

def local_datetime(day, clock):
    return datetime.combine(day, clock, tzinfo=LOCAL_TZ)

def parse_datetime(value):
    if  not value:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if  result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(LOCAL_TZ)

def display_time(value):
    result = parse_datetime(value)
    return result.strftime("%d %b %Y %H:%M SGT") if result else "not recorded"

def recheck_window(session, now=None):
    """Check whether the session is active and its check-in window is open."""
    if  session.get("status") != "active":
        return False, "Re-check is available only for an active session. You can still approve or reject this record."
    try:
        opens  = parse_datetime(session.get("checkin_opens_at"))
        closes = parse_datetime(session.get("checkin_closes_at"))
    except (ValueError, TypeError, AttributeError):
        return False, "The check-in window could not be verified. Refresh the record before requesting a re-check."
    if  opens is None or closes is None or closes <= opens:
        return False, "The check-in window could not be verified. Refresh the record before requesting a re-check."
    now = now or datetime.now(LOCAL_TZ)
    if  now < opens:
        return False, f"Re-check becomes available when check-in opens at {display_time(session['checkin_opens_at'])}."
    if  now > closes:
        return False, "The check-in window has closed. You can still approve or reject this record."
    return True, "The student can submit a fresh check-in while this window remains open."

def recheck_availability(session_id):
    session = st.session_state.api.get_session(session_id)
    allowed, message = recheck_window(session)
    if  allowed:
        course = st.session_state.api.get_course(session["course_id"])
        if  not course.get("is_active"):
            return False, "This course is inactive. You can still approve or reject this record."
    return allowed, message

def score_text(value):
    if  value is None:
        return "not recorded"
    try:
        number = float(value)
        return f"{number:.2f}" if math.isfinite(number) else "not recorded"
    except (ValueError, TypeError):
        return "not recorded"

def verification_text(value):
    return "passed" if value is True else "failed" if value is False else "not recorded"

def risk_band(value):
    if  score_text(value) == "not recorded":
        return "not recorded"
    number = float(value)
    return "Low (<0.3)" if number < 0.3 else "Medium (0.3–<0.5)" if number < 0.5 else "High (≥0.5)"

def validate_window(start, end, opens=None, closes=None, *, require_future=False):
    if  require_future and start <= datetime.now(LOCAL_TZ):
        raise ValueError("the session must start in the future")
    if  end <= start:
        raise ValueError("end time must be after start time")
    if  opens is not None and closes is not None:
        if  closes <= opens:
            raise ValueError("check-in closing time must be after opening time")
        if  opens > end or closes < start:
            raise ValueError("the check-in window must overlap the session")


def date_filters(start, end):
    if  end < start:
        raise ValueError("the end date must not precede the start date.")
    # Send explicit timezone offsets with date filters.
    return {"start_date": local_datetime(start, time.min).isoformat(),
            "end_date":   local_datetime(end,   time.max).isoformat()}

def safe_csv(rows, columns):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        values = []
        for column in columns:
            value = row.get(column)
            if   value is None:
                 value = ""
            elif isinstance(value, (dict, list)):
                 value = json.dumps(value, ensure_ascii=False)
            # Prevent user text from being evaluated as a spreadsheet formula.
            if  isinstance(value, str) and (value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))):
                value = "'" + value
            values.append(value)
        writer.writerow(values)
    return output.getvalue().encode("utf-8")

def reset_login(message=None):
    st.session_state.api.clear_auth()
    # Clear user data, downloads and widgets before the next login.
    st.session_state.clear()
    if  message:
        st.session_state.auth_message = message
    st.rerun()

def show_error(exc):
    if  exc.status == 401 or (exc.status == 403 and exc.code == "ACCOUNT_DISABLED"):
        reset_login("your login is no longer valid. please sign in again")
    st.error(str(exc))
    if  exc.retry_after:
        st.caption(f"retry after {exc.retry_after} seconds.")
    if  exc.request_id:
        st.caption(f"Request ID: {exc.request_id}")

def changed(message):
    """Reset pagination and confirmations before the next widget render."""
    st.session_state.pop("prepared_export", None)
    st.session_state.reset_pages = True
    st.session_state.notice = message
    st.session_state.skip_submissions = True
    st.rerun()

def submit_button(label):
    primary = label in {"Create session", "Save session changes", "Submit review"}
    clicked = st.form_submit_button(label, type="primary" if primary else "secondary")
    return clicked and not skip_submissions

def select_record(label, options, key):
    current = st.session_state.get(key)
    st.session_state[key] = current if current in options else next(iter(options))
    # Preserve the selected ID when a record label changes.
    return st.selectbox(label, list(options), index=None, format_func=options.get, key=key)

def dataframe(rows, columns=None):
    if  rows:
        frame = pd.DataFrame(rows)
        if  columns:
            frame = frame.reindex(columns=columns)
        # Format a copy so API rows and CSV exports remain unchanged.
        time_columns = {"checked_in_at", "scheduled_start", "scheduled_end", "timestamp", "created_at", "reviewed_at"}
        for column in time_columns.intersection(frame.columns):
            frame[column] = frame[column].map(lambda value: display_time(value) if pd.notna(value) else "—")
        if  "status" in frame:
            frame["status"] = frame["status"].map(lambda value: STATUS_LABELS.get(value, str(value).capitalize()))
        labels = {"id": "Record ID", "student_name": "Student", "student_email": "Email",
                  "course_code": "Course", "session_name": "Session", "scheduled_start": "Starts (SGT)",
                  "scheduled_end": "Ends (SGT)", "checked_in_at": "Checked in (SGT)", "risk_score": "Risk score"}
        frame = frame.rename(columns={column: labels.get(column, column.replace("_", " ").capitalize()) for column in frame.columns})
        config = {column: st.column_config.NumberColumn(column, format="%.2f")
                  for column in ["Risk score", "Score", "Weight", "Average risk score"] if column in frame}
        if  "Record ID" in frame:
            config["Record ID"] = None  # Full IDs remain available in selectors, details and exports.
        st.dataframe(frame, column_config=config, use_container_width=True, hide_index=True,
                     height=min(390, 38 * (len(frame) + 1) + 4))

def page_number(key, context):
    context_key = f"{key}_context"
    if  st.session_state.get(context_key) != context:
        st.session_state[key] = 1
        st.session_state[context_key] = context
    if  key not in st.session_state:
        st.session_state[key] = 1
    return int(st.number_input("Page", min_value=1, max_value=20001, step=1, key=key))  # Session State supplies the initial widget value.

def show_page(response, number, noun):
    start = (number - 1) * response["limit"]
    count, total = len(response["items"]), response["total"]
    st.caption(f"{count} of {total} {noun} · Page {number}")
    if   total and start >= total:
         st.info("this page has no records. choose an earlier page")
    elif not count:
         empty_state(f"No matching {noun}", "Try another filter or check back after new records are added.")

def staff_courses(*, active_only=False):
    user = st.session_state.user
    # Explicitly filter the course listing to instructor assignments.
    courses = st.session_state.api.get_all_courses(
        instructor_id=user["id"] if user["role"] == "instructor" else None
    )
    return [course for course in courses if course.get("is_active")] if active_only else courses

def prepared_download(label, scope, fetch, filename):
    key = (st.session_state.user["id"], *scope)
    prepared = st.session_state.get("prepared_export")
    if  prepared and prepared["key"] != key:
        st.session_state.pop("prepared_export", None)
    if  st.button(f"Prepare {label}", key=f"prepare_{scope[0]}"):
        st.session_state.pop("prepared_export", None)
        try:
            st.session_state.prepared_export = {"key": key, "data": fetch()}
        except APIError as exc:
            show_error(exc)
    prepared = st.session_state.get("prepared_export")
    if  prepared and prepared["key"] == key:
        st.download_button(f"Download {label}", data=prepared["data"],
                           file_name=filename, mime="text/csv", key=f"download_{scope[0]}")
        st.caption("Snapshot ready · Prepare again after making changes.")

def open_review_queue():
    st.session_state.navigation = "Check-ins"
    st.session_state.checkins_review_only = True

def overview_page():
    page_intro("Overview", "Attendance across your assigned courses.")
    api = st.session_state.api
    overview = api.get_overview_stats()
    values = [overview["total_sessions"], overview["active_sessions"], overview["total_checkins_today"], f"{overview['approval_rate']:.1%}"]
    helps = ["All authorized sessions.", "Active status; the check-in window must also be open.",
             "Check-ins grouped by UTC date.", "Approved check-ins as a share of retained check-ins."]
    for col, label, value, hint in zip(st.columns(4),
            ["Total sessions", "Active sessions", "Check-ins today", "Approval rate"], values, helps):
        col.metric(label, value, help=hint)
    recent = api.get_checkins(limit=10)["items"]
    left, right = st.columns([2.1, 1])
    with left:
        with panel():
            st.subheader("Check-in activity")
            trend = overview.get("trends", {}).get("checkins_by_day", [])
            if trend:
                figure = px.line(pd.DataFrame(trend), x="date", y="count", markers=True,
                                 color_discrete_sequence=[ACCENT])
                figure.update_traces(fill="tozeroy", fillcolor="rgba(185,172,250,0.07)", line_width=2)
                figure.update_yaxes(rangemode="tozero", dtick=max(1, math.ceil(max(point["count"] for point in trend) / 4)))
                chart(figure, height=325)
                st.caption("Daily totals · UTC")
            else:
                empty_state("No activity yet", "Daily check-ins will appear here.")
    with right:
        with panel():
            st.subheader("Needs attention")
            st.metric("Awaiting review", overview["flagged_pending_review"], help="Flagged and appealed records awaiting a decision.")
            st.button("Open review queue", key="overview_review_queue", on_click=open_review_queue, use_container_width=True)
            if recent:
                counts = pd.DataFrame(recent)["status"].value_counts().rename_axis("status").reset_index(name="count")
                counts["label"] = counts["status"].map(STATUS_LABELS)
                figure = px.bar(counts, x="count", y="label", orientation="h", color="status",
                                color_discrete_map=STATUS_COLOURS, text_auto=True)
                figure.update_layout(showlegend=False, bargap=.55)
                figure.update_xaxes(dtick=max(1, math.ceil(int(counts["count"].max()) / 3)))
                chart(figure, height=170)
                st.caption("Status mix · Latest 10 check-ins")
    with panel():
        st.subheader("Recent check-ins")
        if recent:
            dataframe(recent, CHECKIN_COLUMNS)
        else:
            empty_state("No check-ins yet", "Attendance records will appear after students check in.")
    with st.expander("Course attendance & gradebook"):
        show_course = st.checkbox("Show course attendance and gradebook export", key="show_course_stats")
        if show_course:
            courses = staff_courses()
            if not courses:
                st.info("No courses are assigned to this account.")
                return
            options = {course["id"]: f"{course['code']} — {course['name']}" for course in courses}
            course_id = select_record("Course", options, "overview_course")
            api.get_course(course_id)
            stats = api.get_course_stats(course_id)
            cols = st.columns(3)
            cols[0].metric("Enrolled students", stats["total_enrolled"])
            cols[1].metric("Sessions to date", stats["total_sessions"])
            cols[2].metric("Attendance rate", f"{stats['overall_attendance_rate']:.1%}")
            dataframe(stats.get("student_attendance", []))
            if stats.get("low_attendance_alerts"):
                st.warning("Attendance below 75% for eligible sessions.")
                dataframe(stats["low_attendance_alerts"])
            prepared_download("Course CSV", ("course", course_id), lambda: api.export_attendance(course_id),
                              f"course_{course_id}_attendance.csv")
            st.caption("Non-cancelled sessions through today (UTC).")

def time_inputs(prefix, start, end, labels=("Start", "End")):
    st.caption("Singapore time · UTC+08:00")
    cols = st.columns(2)
    with cols[0]:
         start_day   = st.date_input(f"{labels[0]} date", value=start.date(), key=f"{prefix}_start_day")
         start_clock = st.time_input(f"{labels[0]} time", value=start.time().replace(tzinfo=None), key=f"{prefix}_start_clock")
    with cols[1]:
         end_day     = st.date_input(f"{labels[1]} date", value=end.date(), key=f"{prefix}_end_day")
         end_clock   = st.time_input(f"{labels[1]} time", value=end.time().replace(tzinfo=None), key=f"{prefix}_end_clock")
    return local_datetime(start_day, start_clock), local_datetime(end_day, end_clock)

def venue_inputs(prefix, existing=None):
    existing = existing or {}
    name = st.text_input("Venue name", value=existing.get("venue_name") or "", max_chars=255, key=f"{prefix}_venue")
    cols = st.columns(3)
    latitude  = cols[0].number_input("Latitude",        min_value=-90.0,  max_value=90.0,    value=existing.get("venue_latitude"),  format="%.6f",       key=f"{prefix}_latitude")
    longitude = cols[1].number_input("Longitude",       min_value=-180.0, max_value=180.0,   value=existing.get("venue_longitude"), format="%.6f",       key=f"{prefix}_longitude")
    radius    = cols[2].number_input("Radius (metres)", min_value=1.0,    max_value=10000.0, value=float(existing.get("geofence_radius_meters") or 100), key=f"{prefix}_radius")
    return {"venue_name": name.strip() or None, "venue_latitude": latitude, "venue_longitude": longitude, "geofence_radius_meters": radius}

def validate_venue(venue):
    if  venue and (venue["venue_latitude"] is None or venue["venue_longitude"] is None):
        raise ValueError("enter both venue coordinates when overriding the course venue")

def create_session_form():
    st.subheader("Create new session")
    courses = staff_courses(active_only=True)
    if  not courses:
        st.info("you need an active course assignment before you can create a session.")
        return
    options  = {course["id"]: f"{course['code']} — {course['name']}" for course in courses}
    tomorrow = datetime.combine(datetime.now(LOCAL_TZ).date() + timedelta(days=1), time(9), LOCAL_TZ)
    override_venue = st.checkbox("Set a session venue instead of the course default", key="create_override_venue")
    with st.form("create_session_form", clear_on_submit=False):
        name = st.text_input("Session name", max_chars=255, key="create_name")
        course_id = select_record("Course", options, "create_course")
        session_type = st.selectbox("Session type", ["lecture", "tutorial", "lab", "exam"], format_func=str.capitalize, key="create_type")
        description = st.text_area("Description (optional)", max_chars=5000, key="create_description")
        start, end = time_inputs("create", tomorrow, tomorrow + timedelta(hours=1))
        venue = venue_inputs("create") if override_venue else {}
        st.caption("Check-in: 15 minutes before to 30 minutes after the start. Course verification rules and liveness apply.")
        submitted = submit_button("Create session")
    if  submitted:
        try:
            if  not name.strip():
                raise ValueError("a session name is required")
            validate_window(start, end, require_future=True)
            validate_venue(venue)
            st.session_state.api.create_session({
                "name": name.strip(), "course_id": course_id, "session_type": session_type,
                "description": description.strip() or None, "scheduled_start": start.isoformat(),
                "scheduled_end": end.isoformat(),
                **venue,
            })
            changed("Session created.")
        except ValueError as exc:
            st.error(str(exc))
        except APIError as exc:
            show_error(exc)

def session_edit_form(session):
    sid = session["id"]
    with st.expander("Edit session"):
        edit_venue = st.checkbox("Edit venue and geofence", key=f"edit_venue_{sid}")
        with st.form(f"edit_session_{sid}"):
            name = st.text_input("Session name", value=session["name"], max_chars=255, key=f"edit_name_{sid}")
            description = st.text_area("Description", value=session.get("description") or "", max_chars=5000, key=f"edit_description_{sid}")
            types = ["lecture", "tutorial", "lab", "exam"]
            kind = st.selectbox("Session type", types, index=types.index(session["session_type"]), format_func=str.capitalize, key=f"edit_type_{sid}")
            venue = venue_inputs(f"edit_{sid}", session) if edit_venue else {}
            times = None
            if  session["status"] == "scheduled":
                start, end    = time_inputs(f"edit_{sid}",   parse_datetime(session["scheduled_start"]),  parse_datetime(session["scheduled_end"]))
                opens, closes = time_inputs(f"window_{sid}", parse_datetime(session["checkin_opens_at"]), parse_datetime(session["checkin_closes_at"]), labels=("Check-in open", "Check-in close"))
                st.caption("keep the check-in window overlapping the session")
                times = (start, end, opens, closes)
            else:
                st.caption("times can only be changed while a session is scheduled")
            saved = submit_button("Save session changes")
        if  saved:
            try:
                if  not name.strip():
                    raise ValueError("a session name is required")
                data = {"name": name.strip(), "description": description.strip() or None, "session_type": kind}
                validate_venue(venue)
                data.update(venue)
                if  times:
                    validate_window(*times)
                    for field, value in zip(["scheduled_start", "scheduled_end", "checkin_opens_at", "checkin_closes_at"], times):
                        if  parse_datetime(session[field]) != value:
                            data[field] = value.isoformat()
                st.session_state.api.update_session(sid, data)
                changed("Session updated.")
            except ValueError as exc:
                st.error(str(exc))
            except APIError as exc:
                show_error(exc)

def session_actions(session):
    sid = session["id"]
    owner = st.session_state.user["role"] == "admin" or session["instructor_id"] == st.session_state.user["id"]
    if  not owner:
        st.info("you can view this session but editing is restricted to its owner or an administrator")
        return
    session_edit_form(session)
    transitions = TRANSITIONS.get(session["status"], [])
    if  transitions:
        with st.form(f"status_form_{sid}"):
            status = st.selectbox("New status", transitions, format_func=str.capitalize, key=f"session_status_{sid}")
            confirm = st.checkbox("I confirm this status change", key=f"confirm_status_{sid}")
            submitted = submit_button("Update session status")
        if  submitted:
            if  not confirm:
                st.error("confirm the status change first")
            else:
                st.session_state.api.update_session(sid, {"status": status})
                changed(f"session status updated to {status}")
    if  session["status"] == "scheduled":
        with st.form(f"delete_form_{sid}"):
            confirm = st.checkbox("i confirm deletion of this scheduled session", key=f"confirm_delete_{sid}")
            submitted = submit_button("Delete session")
        if  submitted:
            if  not confirm:
                st.error("confirm deletion first")
            else:
                st.session_state.api.delete_session(sid)
                changed("scheduled session deleted")

def sessions_page():
    page_intro("Sessions", "Your classes, check-in windows and attendance.")
    with st.expander("Create a session", expanded=False):
        create_session_form()
    with panel():
        st.subheader("All sessions")
        filter_col, page_col = st.columns([4, 1])
        with filter_col:
            status = st.selectbox("Session status filter", ["all", "scheduled", "active", "closed", "cancelled"],
                                  format_func=lambda value: "All statuses" if value == "all" else value.capitalize(), key="sessions_status_filter")
        with page_col:
            number = page_number("sessions_page", (status,))
        filters = {"status": status} if status != "all" else {}
        response = st.session_state.api.get_sessions(limit=50, offset=(number - 1) * 50, **filters)
        sessions = response["items"]
        show_page(response, number, "sessions")
        dataframe(sessions, ["id", "name", "course_code", "status", "scheduled_start", "scheduled_end"])
    if not sessions:
        return
    options = {row["id"]: f"{row['course_code']} — {row['name']} ({row['status']})" for row in sessions}
    sid = select_record("Select a session", options, "selected_session")
    session = st.session_state.api.get_session(sid)
    with panel():
        detail_header(session["name"], session["course_code"], session["status"])
        metadata([("Starts", display_time(session["scheduled_start"])), ("Ends", display_time(session["scheduled_end"])),
                  ("Check-in opens", display_time(session["checkin_opens_at"])), ("Check-in closes", display_time(session["checkin_closes_at"]))])
        now = datetime.now(LOCAL_TZ)
        window_open = parse_datetime(session["checkin_opens_at"]) <= now <= parse_datetime(session["checkin_closes_at"])
        st.caption("Check-in is open." if session["status"] == "active" and window_open else "Check-in is not open.")
    attendance, settings = st.tabs(["Attendance", "Settings"])
    with attendance:
        stats = st.session_state.api.get_session_stats(sid)
        for col, label, value in zip(st.columns(3), ["Enrolled", "Approved", "Attendance rate"],
                                    [stats["total_enrolled"], stats["approved_count"], f"{stats['attendance_rate']:.1%}"]):
            col.metric(label, value)
        with panel():
            st.subheader("Session check-ins")
            checkins = st.session_state.api.get_session_checkins(sid)
            if checkins:
                dataframe(checkins, CHECKIN_COLUMNS)
            else:
                empty_state("No check-ins yet", "Student attendance will appear here.")
            prepared_download("Session CSV", ("session", sid), lambda: st.session_state.api.export_session(sid),
                              f"session_{sid}_attendance.csv")
    with settings:
        session_actions(session)
        with st.expander("Venue & verification"):
            metadata([("Venue", session.get("venue_name") or "Not configured"),
                      ("Coordinates", f"{session.get('venue_latitude')} / {session.get('venue_longitude')}"),
                      ("Radius", f"{session.get('geofence_radius_meters')} m"),
                      ("Liveness required", "Yes" if session["require_liveness_check"] else "No"),
                      ("Face match required", "Yes" if session["require_face_match"] else "No"), ("Session ID", sid)])
            st.caption("Course activity, enrollment and consent requirements also apply.")

def checkin_details(checkin_id):
    detail = st.session_state.api.get_checkin(checkin_id)
    detail_header(detail.get("student_name") or "Student", f"{detail.get('session_name')} · {detail.get('course_code')}", detail["status"])
    for col, label, key in zip(st.columns(3), ["Risk score", "Liveness score", "Face match score"],
                               ["risk_score", "liveness_score", "face_match_score"]):
        score = score_text(detail.get(key))
        col.metric(label, "—" if score == "not recorded" else score)
    metadata([("Checked in", display_time(detail["checked_in_at"])), ("Student email", detail.get("student_email")),
              ("Liveness", verification_text(detail.get("liveness_passed"))), ("Face match", verification_text(detail.get("face_match_passed")))])
    with st.expander("Evidence & history"):
        metadata([("Location", f"{detail.get('latitude')} / {detail.get('longitude')}"),
                  ("Distance from venue", f"{detail.get('distance_from_venue_meters')} m")])
        if detail.get("risk_factors"):
            st.write("Risk factors", detail["risk_factors"])
        if detail.get("risk_signals"):
            dataframe(detail["risk_signals"], ["type", "score", "weight", "details"])
        if detail.get("appeal_reason"):
            st.write("Appeal", detail["appeal_reason"])
        if detail.get("review_notes"):
            st.write("Previous review", detail["review_notes"])
    if  detail["status"] in {"flagged", "appealed"}:
        can_recheck, window_message = recheck_availability(detail["session_id"])
        decisions = ["approved", "rejected"] + ([RECHECK] if can_recheck else [])
        decision_key = f"decision_{checkin_id}"
        if  st.session_state.get(decision_key) not in [None, *decisions]:
            # An expired re-check must never silently turn into an approval.
            st.session_state[decision_key] = None
            st.session_state[f"review_confirm_{checkin_id}"] = False
        st.caption(window_message)
        with st.form(f"review_form_{checkin_id}"):
            decision = st.selectbox("Review decision", decisions, index=None, key=decision_key,
                                    placeholder="Choose a decision",
                                    format_func=lambda value: "Request re-check" if value == RECHECK else value.capitalize(),
                                    help="Re-check is available only while the session is active and its check-in window is open.")
            notes = st.text_area("Review notes", max_chars=2000, height=85, placeholder="Reason for this decision", key=f"notes_{checkin_id}")
            confirm = st.checkbox("I confirm this review decision", key=f"review_confirm_{checkin_id}")
            submitted = submit_button("Submit review")
        if  submitted:
            if  not decision:
                st.error("Choose a review decision. The previous re-check option is no longer available.")
            elif not confirm or not notes.strip():
                st.error("Enter review notes and confirm the decision before submitting.")
            elif decision == RECHECK:
                # Fetch again on submit: the window may have closed while notes were being written.
                still_open, message = recheck_availability(detail["session_id"])
                if  not still_open:
                    st.warning(message)
                else:
                    st.session_state.api.reverify_checkin(checkin_id, notes.strip())
                    changed("Re-check requested. The student can submit again while the check-in window remains open.")
            else:
                st.session_state.api.review_checkin(checkin_id, decision, notes.strip())
                changed(f"check-in reviewed as {decision}.")
    elif detail["status"] == "pending":
        can_recheck, _ = recheck_availability(detail["session_id"])
        if  can_recheck:
            st.info("Waiting for the student to submit a fresh check-in.")
        else:
            st.warning("This re-check is pending, but the session is no longer open for resubmission. Contact an administrator to resolve the record.")
    else:
        st.caption("This record has no pending review.")

def checkins_page():
    page_intro("Check-ins", "Attendance records and review decisions.")
    api = st.session_state.api
    sessions = api.get_all_sessions()
    options = {"all": "All authorized sessions", **{row["id"]: f"{row['course_code']} — {row['name']}" for row in sessions}}
    with panel():
        session_col, status_col, page_col = st.columns([2.6, 1.5, .8])
        with session_col:
            selected = select_record("Session filter", options, "checkins_session")
        session_id = None if selected == "all" else selected
        review_only = st.checkbox("Show only check-ins awaiting review", key="checkins_review_only",
                                  help="Flagged and appealed records, oldest first.")
        with status_col:
            status = st.selectbox("Status filter", ["all", "approved", "flagged", "rejected", "pending", "appealed"],
                                  format_func=lambda value: "All statuses" if value == "all" else STATUS_LABELS[value], key="checkins_status", disabled=review_only)
        with st.expander("More filters"):
            risk_col, date_col = st.columns(2)
            with risk_col:
                use_risk = st.checkbox("Filter by minimum risk", key="checkins_use_risk", disabled=review_only)
                min_risk = st.slider("Minimum risk", 0.0, 1.0, 0.5, 0.05, key="checkins_min_risk", disabled=review_only or not use_risk)
            with date_col:
                use_dates = st.checkbox("Filter by date range (SGT)", key="checkins_use_dates", disabled=review_only)
                today = datetime.now(LOCAL_TZ).date()
                start = st.date_input("From date", value=today - timedelta(days=7), key="checkins_start", disabled=review_only or not use_dates)
                end = st.date_input("To date", value=today, key="checkins_end", disabled=review_only or not use_dates)
        filters = {}
        if not review_only:
            if use_risk:
                filters["min_risk_score"] = min_risk
            if use_dates:
                try:
                    filters.update(date_filters(start, end))
                except ValueError as exc:
                    st.error(str(exc))
                    return
        context = (session_id, review_only, None if review_only else status, tuple(sorted(filters.items())))
        with page_col:
            number = page_number("checkins_page", context)
    response = api.get_checkins(status=None if status == "all" else status, session_id=session_id,
                               review_only=review_only, limit=50, offset=(number - 1) * 50, **filters)
    rows = response["items"]
    with panel():
        show_page(response, number, "check-ins")
        dataframe(rows, CHECKIN_COLUMNS)
        if not rows:
            return
        st.download_button("Download displayed page CSV", data=safe_csv(rows, CHECKIN_COLUMNS),
                           file_name=f"checkins_page_{number}.csv", mime="text/csv",
                           help="Exports this page only. Use Sessions or the course gradebook for a full export.")
    detail_options = {row["id"]: f"{row.get('student_name')} — {row.get('session_name')} ({row['status']})" for row in rows}
    checkin_id = select_record("Select a check-in", detail_options, "selected_checkin")
    with panel():
        checkin_details(checkin_id)

def audit_page():
    page_intro("Audit logs", "Account activity and attendance decisions.")
    if  st.session_state.user["role"] != "admin":
        st.info("Audit logs are restricted to administrators.")
        return  # Do not request administrator data with an instructor token.
    api = st.session_state.api
    summary = api.get_audit_summary()
    totals, activity = st.columns([1, 3])
    with totals:
        st.metric("Events · Last 7 days", summary["total_logs"])
    with activity:
        with panel():
            st.subheader("Audit activity")
            if summary.get("by_day"):
                figure = px.bar(pd.DataFrame(summary["by_day"]), x="date", y="count", color_discrete_sequence=[ACCENT])
                figure.update_xaxes(type="category")  # Keep a single-day series on a date-category axis.
                figure.update_yaxes(dtick=max(1, math.ceil(max(day["count"] for day in summary["by_day"]) / 4)))
                figure.update_layout(bargap=.65)
                chart(figure, height=170)
            else:
                empty_state("No audit activity", "Account events will appear here.")
    with st.expander("Filter audit logs"):
        action = st.text_input("Action (exact match)", max_chars=100, key="audit_action").strip()
        resource_type = st.text_input("Resource type (exact match)", max_chars=100, key="audit_resource_type").strip()
        user_id = st.text_input("User ID (optional UUID)", key="audit_user_id").strip()
        resource_id = st.text_input("Resource ID (optional UUID)", key="audit_resource_id").strip()
        success = st.selectbox("Result", ["all", "successful", "failed"],
                               format_func=lambda value: "All results" if value == "all" else value.capitalize(), key="audit_result")
        use_dates = st.checkbox("Filter audit dates (SGT)", key="audit_use_dates")
        today = datetime.now(LOCAL_TZ).date()
        start = st.date_input("From date", value=today - timedelta(days=7), key="audit_start", disabled=not use_dates)
        end = st.date_input("To date", value=today, key="audit_end", disabled=not use_dates)
    filters = {}
    for field, value in [("action", action), ("resource_type", resource_type), ("user_id", user_id), ("resource_id", resource_id)]:
        if  value:
            if  field.endswith("_id"):
                try:
                    UUID(value)
                except ValueError:
                    st.error(f"{field} must be a valid UUID")
                    return
            filters[field] = value
    if  success != "all":
        filters["success"] = success == "successful"
    if  use_dates:
        try:
            filters.update(date_filters(start, end))
        except ValueError as exc:
            st.error(str(exc))
            return
    number = page_number("audit_page", tuple(sorted(filters.items())))
    response = api.get_audit_logs(limit=50, offset=(number - 1) * 50, **filters)
    rows = response["items"]
    show_page(response, number, "audit events")
    if  rows:
        columns = ["timestamp", "user_email", "action", "resource_type", "resource_id", "severity", "success"]
        frame   = pd.DataFrame(rows).reindex(columns=columns)
        frame["timestamp"] = frame["timestamp"].map(display_time)
        frame = frame.rename(columns={"timestamp": "Time (SGT)", "user_email": "User",
                                      "action": "Action", "resource_type": "Resource", "resource_id": "Resource ID",
                                      "severity": "Severity", "success": "Successful"})
        colours = {"info": "#252a36", "warning": "#3b3225", "error": "#3c2830", "critical": "#462b33"}
        st.dataframe(frame.style.map(lambda value: f"background-color: {colours.get(value, '#252a36')}; color: #e4dce6", subset=["Severity"]),
                     use_container_width=True, hide_index=True)
        st.download_button("Download displayed audit page CSV", data=safe_csv(rows, columns + ["details"]),
                            file_name=f"audit_page_{number}.csv", mime="text/csv")
        options = {row["id"]: f"{row['timestamp']} — {row['action']}" for row in rows}
        selected = select_record("Audit event details", options, "selected_audit_event")
        event = next(row for row in rows if row["id"] == selected)
        with st.expander("Event details"):
            st.json(event["details"])

def metrics_page():
    page_intro("Metrics", "Service health and application performance.")
    prometheus = PrometheusClient(PROMETHEUS_URL)
    try:
        values = [prometheus.scalar(query) for query in QUERIES.values()]
        labels = ["Requests / second", "Successful responses", "Error responses", "Response time · p95"]
        for index, (col, label, value) in enumerate(zip(st.columns(4), labels, values)):
            suffix = " req/s" if index == 0 else " ms" if index == 3 else "%"
            col.metric(label, "—" if value is None else f"{value:.2f}{suffix}",
                       help="Five-minute window, excluding health checks and monitoring probes. A dash means no measured value is available.")
    except APIError as exc:
        st.warning(str(exc))
    left, right = st.columns([1, 1.25])
    with left:
        with panel():
            st.subheader("Services")
            try:
                health = st.session_state.api.get_health()
                service_states(health)
                if health.get("status") != "healthy":
                    st.warning(f"Service health: {health.get('status', 'unknown')}")
            except APIError as exc:
                st.error(str(exc))
            grafana_url = os.getenv("GRAFANA_URL", "http://localhost:3001").rstrip("/")
            st.link_button("Open Grafana", f"{grafana_url}/d/saiv-operations/saiv-operations?theme=dark", use_container_width=True)
            with st.expander("Scrape targets"):
                try:
                    dataframe(prometheus.targets())
                except APIError as exc:
                    st.warning(str(exc))
    recent = st.session_state.api.get_checkins(limit=100)["items"]
    with right:
        with panel():
            st.subheader("Recent risk")
            if recent:
                bands = pd.Series([risk_band(row.get("risk_score")) for row in recent]).value_counts().rename_axis("risk").reset_index(name="count")
                figure = px.bar(bands, x="risk", y="count", color_discrete_sequence=[ACCENT])
                figure.update_layout(bargap=.6)
                chart(figure, height=210)
                st.caption(f"Latest {len(recent)} check-ins · Missing scores are unrecorded")
            else:
                empty_state("No risk data yet", "Scores appear after students check in.")
    with panel():
        st.subheader("High-risk check-ins")
        alerts = st.session_state.api.get_checkins(min_risk_score=0.5, limit=10)["items"]
        if alerts:
            dataframe(alerts, CHECKIN_COLUMNS)
            st.caption("Latest 10 with risk ≥0.5 · Review availability depends on status")
        else:
            empty_state("No high-risk records", "No authorized check-ins have a risk score of 0.5 or higher.")

# Mutations require explicit form submission.
if "api" not in st.session_state:
    st.session_state.api = APIClient(BACKEND_URL)
skip_submissions = st.session_state.pop("skip_submissions", False)
if  st.session_state.pop("reset_pages", False):
    for key in ["sessions_page", "checkins_page", "audit_page"]:
        st.session_state[key] = 1
    for key in list(st.session_state):
        if  key.startswith(("confirm_status_", "confirm_delete_", "review_confirm_")):
            st.session_state[key] = False
dashboard_style()
message = st.session_state.pop("auth_message", None)
if  message:
    st.warning(message)
if  not st.session_state.api.token:
    _, login_panel, _ = st.columns([1, 1.15, 1])
    with login_panel:
        brand()
        st.title("Welcome back")
        st.caption("Sign in to your attendance workspace.")
        with st.form("login_form", clear_on_submit=True):
            email = st.text_input("Email", key="login_email", placeholder="name@example.com")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if  submitted:
            try:
                data = st.session_state.api.login(email.strip(), password)
                if  data["user"]["role"] not in {"instructor", "admin"}:
                    try:
                        st.session_state.api.logout()
                    except APIError:
                        pass  # Logout clears local tokens even if server revocation fails.
                    st.error("An instructor or administrator account is required.")
                else:
                    st.session_state.user = data["user"]
                    st.rerun()
            except APIError as exc:
                st.error(str(exc))
        st.caption("For instructors and administrators.")
    st.stop()

with st.sidebar:
    brand()
page = st.sidebar.radio("Navigation", ["Overview", "Sessions", "Check-ins", "Audit logs", "Metrics"], key="navigation", label_visibility="collapsed")
try:
    user = st.session_state.api.get_current_user()
    if  user["role"] not in {"instructor", "admin"}:
        reset_login("this dashboard requires an instructor or administrator account")
    st.session_state.user = user
except APIError as exc:
    show_error(exc)
    st.stop()
st.sidebar.divider()
with st.sidebar:
    profile(user["full_name"], user["role"])
if  st.sidebar.button("Refresh data", use_container_width=True):
    st.session_state.pop("prepared_export", None)
auto_refresh = st.sidebar.checkbox("Auto-refresh · 30s", key="auto_refresh", disabled=st_autorefresh is None,
                                   help="Refreshes Overview, Check-ins and Metrics. Session forms and audit logs stay manual.")
if  st_autorefresh is None:
    st.sidebar.caption("Use Refresh data to update the page.")
if  st_autorefresh is not None and auto_refresh and page in {"Overview", "Check-ins", "Metrics"}:
    # Timer reruns do not resubmit forms.
    st_autorefresh(interval=30_000, key=f"refresh_{page}")
st.sidebar.caption(f"Updated {datetime.now(LOCAL_TZ):%H:%M} SGT")
if  st.sidebar.button("Sign out", use_container_width=True):
    warning = None
    try:
        st.session_state.api.logout()
    except APIError:
        warning = "You are signed out here, but backend session cancellation could not be confirmed."
    reset_login(warning)
notice = st.session_state.pop("notice", None)
if  notice:
    st.success(notice)
try:
    {"Overview": overview_page, "Sessions": sessions_page, "Check-ins": checkins_page,
     "Audit logs": audit_page, "Metrics": metrics_page}[page]()
except APIError as exc:
    # Invalidate prepared exports after an authorization failure.
    st.session_state.pop("prepared_export", None)
    show_error(exc)
