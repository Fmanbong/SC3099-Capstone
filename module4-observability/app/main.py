
"""THIS IS THE STREAMLIT INTERFACE FOR ATTENDANCE , SESSIONS , AND MONITORING
"""

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

LOCAL_TZ = ZoneInfo("Asia/Singapore")
TRANSITIONS      = {"scheduled": ["active" , "cancelled"] , "active": ["closed" , "cancelled"] , "closed": ["cancelled"] , "cancelled": []}
CHECKIN_COLUMNS  = ["id" , "student_name" , "student_email" , "course_code" , "session_name" , "status" , "checked_in_at" , "risk_score"]  # TODO - are this gettting displayed as UI elements?
RECHECK , ACCENT = "REQUEST RE-CHECK" , "#b9acfa"
STATUS_LABELS    = {"approved": "APPROVED" , "rejected": "REJECTED" , "flagged": "FLAGGED" , "appealed": "APPEALED" , "pending": "RE-CHECK REQUESTED"}
STATUS_COLORS    = {"approved": "#9acbb7" , "flagged": "#d4bb8a" , "rejected": "#d89aa6" , "pending": ACCENT , "appealed": "#9daed2"}


def dashboard_style(): apply_theme()

def chart(figure, *, height=300):
    figure.update_layout(
        colorway=[ACCENT, "#9acbb7", "#9daed2", "#d4bb8a", "#d89aa6"],
        legend={"title_text": "", "orientation": "h", "y": -0.22}, height=height , paper_bgcolor="rgba(0,0,0,0)" , plot_bgcolor="rgba(0,0,0,0)",
        margin={"l": 8, "r": 12, "t": 20, "b": 16}, hoverlabel={"bgcolor": "#292b35", "font_family": "JetBrains Mono, sans-serif", "font_size": 12},
        template="plotly_dark" , font={"family": "JetBrains Mono, sans-serif" , "color": "#9a9fad", "size": 11},
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
    return result.strftime("%d %b %Y %H:%M SGT") if result else "NOT RECORDED"

def recheck_window(session, now=None):
    """to check whether the session is active and its check in window is open
    """

    if  session.get("status") != "active":
        return False, "RE-CHECK IS AVAILABLE ONLY FOR AN ACTIVE SESSION. YOU CAN STILL APPROVE OR REJECT THIS RECORD"
    try:
        opens  = parse_datetime(session.get("checkin_opens_at"))
        closes = parse_datetime(session.get("checkin_closes_at"))
    except (ValueError, TypeError, AttributeError):
        return False, "THE CHECK-IN WINDOW COULD NOT BE VERIFIED. REFRESH THE RECORD BEFORE REQUESTING A RE-CHECK"
    if  opens is None or closes is None or closes <= opens:
        return False, "THE CHECK-IN WINDOW COULD NOT BE VERIFIED. REFRESH THE RECORD BEFORE REQUESTING A RE-CHECK"
    now = now or datetime.now(LOCAL_TZ)
    if  now < opens:
        return False, f"RE-CHECK BECOMES AVAILABLE WHEN CHECK-IN OPENS AT {display_time(session['checkin_opens_at'])}."
    if  now > closes:
        return False, "THE CHECK-IN WINDOW HAS CLOSED. YOU CAN STILL APPROVE OR REJECT THIS RECORD"
    return True, "THE STUDENT CAN SUBMIT A FRESH CHECK-IN WHILE THIS WINDOW REMAINS OPEN"

def recheck_availability(session_id):
    session = st.session_state.api.get_session(session_id)
    allowed, message = recheck_window(session)
    if  allowed:
        course = st.session_state.api.get_course(session["course_id"])
        if  not course.get("is_active"):
            return False, "THIS COURSE IS INACTIVE. YOU CAN STILL APPROVE OR REJECT THIS RECORD"
    return allowed, message

def score_text(value):
    if  value is None:
        return "NOT RECORDED"
    try:
        number = float(value)
        return f"{number:.2f}" if math.isfinite(number) else "NOT RECORDED"
    except (ValueError, TypeError):
        return "NOT RECORDED"

def verification_text(value):
    return "PASSED" if value is True else "failed" if value is False else "NOT RECORDED"

def risk_band(value):
    if  score_text(value) == "NOT RECORDED":
        return "NOT RECORDED"
    number = float(value)
    return "LOW (<0.3)" if number < 0.3 else "MEDIUM (0.3–<0.5)" if number < 0.5 else "HIGH (≥0.5)"

def validate_window(start, end, opens=None, closes=None, *, require_future=False):
    if  require_future and start <= datetime.now(LOCAL_TZ):
        raise ValueError("THE SESSION MUST START IN THE FUTURE")
    if  end <= start:
        raise ValueError("SO END TIME MUST BE AFTER START TIME")
    if  opens is not None and closes is not None:
        if  closes <= opens:
            raise ValueError("CHECK-IN CLOSING TIME MUST BE AFTER OPENING TIME")
        if  opens > end or closes < start:
            raise ValueError("THE CHECK-IN WINDOW HAS TO OVERLAP THE SESSION")

def date_filters(start, end):
    if  end < start:
        raise ValueError("THE END DATE MUST NOT PRECEDE THE START DATE")
    # send explicit timezone offsets with date filters
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
            # prevent user text from being evaluated as a spreadsheet formula
            if   isinstance(value, str) and (value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))):
                 value = "'" + value
            values.append(value)
        writer.writerow(values)
    return output.getvalue().encode("utf-8")

def reset_login(message=None):
    st.session_state.api.clear_auth()
    # clear user data and downloads and widgets before the next login
    st.session_state.clear()
    if  message:
        st.session_state.auth_message = message
    st.rerun()

def show_error(exc):
    if  exc.status == 401 or (exc.status == 403 and exc.code == "ACCOUNT_DISABLED"):
        reset_login("YOUR LOGIN IS NO LONGER VALID. PLEASE SIGN IN AGAIN")
    st.error(str(exc))
    if  exc.retry_after:
        st.caption(f"RETRY AFTER {exc.retry_after} SECONDS")
    if  exc.request_id:
        st.caption(f"REQUEST ID: {exc.request_id}")

def changed(message):
    """the reset pagination and confirmations before the next widget render
    """
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
    # preserve the selected ID when a record label changes
    return st.selectbox(label, list(options), index=None, format_func=options.get, key=key)

def dataframe(rows, columns=None):
    if  rows:
        frame = pd.DataFrame(rows)
        if  columns:
            frame = frame.reindex(columns=columns)
        # format a copy so api rows and csv exports remain unchanged
        time_columns = {"checked_in_at", "scheduled_start", "scheduled_end", "timestamp", "created_at", "reviewed_at"}
        for column in time_columns.intersection(frame.columns):
            frame[column] = frame[column].map(lambda value: display_time(value) if pd.notna(value) else "—")
        if  "status" in frame:
            frame["status"] = frame["status"].map(lambda value: STATUS_LABELS.get(value, str(value).capitalize()))
        labels = {
            "id": "Record ID", "student_name": "Student", "student_email": "Email", "course_code": "Course", "session_name": "Session",
            "scheduled_start": "Starts (SGT)", "scheduled_end": "Ends (SGT)", "checked_in_at": "Checked in (SGT)", "risk_score": "Risk score"
            }

        # for formatting nested details for display without changing source evidence
        if "details" in frame: frame["details"] = frame["details"].map(lambda value: str(value).upper() if value is not None else "-")

        frames = frame.rename(columns={column: labels.get(column, column.replace("_", " ").capitalize()) for column in frame.columns})  # TODO - `frames' could give error
        config = {column: st.column_config.NumberColumn(column.upper(), format="%.2f")
                  for column in ["Risk score", "Score", "Weight", "Average risk score"] if column in frames}
        if  "Record ID" in frames:
            config["Record ID"] = None  # full IDs remain available in selectors, details , and exports

        # this is to uppercase displayed text and headers without changing source records
        for column in frames.columns:
            frames[column] = frames[column].map(lambda value: value.upper() if isinstance(value, str) else value)
        frames.columns = frames.columns.str.upper()
        config = {column.upper(): setting for column, setting in config.items()}

        st.dataframe(frames, column_config=config, use_container_width=True, hide_index=True,
                     height=min(390, 38 * (len(frames) + 1) + 4))

def page_number(key, context):
    context_key = f"{key}_context"
    if  st.session_state.get(context_key) != context:
        st.session_state[key] = 1
        st.session_state[context_key] = context
    if  key not in st.session_state:
        st.session_state[key] = 1
    return int(st.number_input("PAGE", min_value=1, max_value=20001, step=1, key=key))

def show_page(response, number, noun):
    start = (number - 1) * response["limit"]
    count, total = len(response["items"]), response["total"]
    st.caption(f"{count} OF {total} {noun.upper()} · PAGE {number}")
    if   total and start >= total:
         st.info("THIS PAGE HAS NO RECORDS. PLEASE CHOOSE AN EARLIER PAGE")
    elif not count:
         empty_state(f"NO MATCHING {noun.upper()}", "TRY ANOTHER FILTER OR CHECK BACK AFTER NEW RECORDS ARE ADDED")

def staff_courses(*, active_only=False):
    user = st.session_state.user
    # explicitly filter the course listing to instructor assignments
    courses = st.session_state.api.get_all_courses(
        instructor_id=user["id"] if user["role"] == "instructor" else None
    )
    return [course for course in courses if course.get("is_active")] if active_only else courses

def prepared_download(label, scope, fetch, filename):
    key = (st.session_state.user["id"], *scope)
    prepared = st.session_state.get("prepared_export")
    if  prepared and prepared["key"] != key:
        st.session_state.pop("prepared_export", None)
    if  st.button(f"PREPARE {label.upper()}", key=f"prepare_{scope[0]}"):
        st.session_state.pop("prepared_export", None)
        try:
            st.session_state.prepared_export = {"key": key, "data": fetch()}
        except APIError as exc:
            show_error(exc)
    prepared = st.session_state.get("prepared_export")
    if  prepared and prepared["key"] == key:
        st.download_button(f"DOWNLOAD {label.upper()}", data=prepared["data"],
                           file_name=filename, mime="text/csv", key=f"download_{scope[0]}")
        st.caption("SNAPSHOT READY · PREPARE AGAIN AFTER MAKING CHANGES")

def open_review_queue():
    st.session_state.navigation = "Check-ins"
    st.session_state.checkins_review_only = True

def overview_page():
    page_intro("OVERVIEW", "ATTENDANCE ACROSS YOUR ASSIGNED COURSES")
    api = st.session_state.api
    overview = api.get_overview_stats()
    values = [overview["total_sessions"], overview["active_sessions"], overview["total_checkins_today"], f"{overview['approval_rate']:.1%}"]
    helps = ["ALL AUTHORIZED SESSIONS.", "ACTIVE STATUS; THE CHECK-IN WINDOW MUST ALSO BE OPEN.",
             "CHECK-INS GROUPED BY UTC DATE.", "APPROVED CHECK-INS AS A SHARE OF RETAINED CHECK-INS."]
    for col, label, value, hint in zip(st.columns(4),
            ["TOTAL SESSIONS", "ACTIVE SESSIONS", "CHECK-INS TODAY", "APPROVAL RATE"], values, helps):
        col.metric(label, value, help=hint)
    recent = api.get_checkins(limit=10)["items"]
    left, right = st.columns([2.1, 1])
    with left:
         with panel():
              st.subheader("CHECK-IN ACTIVITY")
              trend = overview.get("trends", {}).get("checkins_by_day", [])
              if  trend:
                  figure = px.line(pd.DataFrame(trend), x="date", y="count", markers=True, color_discrete_sequence=[ACCENT])
                  figure.update_traces(fill="tozeroy", fillcolor="rgba(185,172,250,0.07)", line_width=2)
                  figure.update_yaxes(rangemode="tozero", dtick=max(1, math.ceil(max(point["count"] for point in trend) / 4)))

                  # we format tick labels while retaining the original date values
                  dates = [point["date"] for point in trend]
                  figure.update_xaxes(tickmode="array", tickvals=dates, ticktext=[pd.to_datetime(day).strftime("%d %b").upper() for day in dates],)

                  chart(figure, height=325)
                  st.caption("DAILY TOTALS · UTC")
              else:
                  empty_state("NO ACTIVITY YET", "DAILY CHECK INS WILL APPEAR HERE")
    with right:
         with panel():
              st.subheader("NEEDS ATTENTION")
              st.metric("AWAITING REVIEW", overview["flagged_pending_review"], help="FLAGGED AND APPEALED RECORDS AWAITING A DECISION.")
              st.button("OPEN REVIEW QUEUE", key="overview_review_queue", on_click=open_review_queue, use_container_width=True)
              if  recent:
                  counts = pd.DataFrame(recent)["status"].value_counts().rename_axis("status").reset_index(name="count")
                  counts["label"] = counts["status"].map(STATUS_LABELS)
                  figure = px.bar(counts, x="count", y="label", orientation="h", color="status",
                                  color_discrete_map=STATUS_COLORS, text_auto=True)
                  figure.update_layout(showlegend=False, bargap=.55)
                  figure.update_xaxes(dtick=max(1, math.ceil(int(counts["count"].max()) / 3)))
                  chart(figure, height=170)
                  st.caption("STATUS MIX · LATEST 10 CHECK-INS")
    with panel():
         st.subheader("RECENT CHECK-INS")
         if  recent:
             dataframe(recent, CHECKIN_COLUMNS)
         else:
             empty_state("NO CHECK-INS YET", "ATTENDANCE RECORDS WILL APPEAR AFTER STUDENTS CHECK IN.")
    with st.expander("COURSE ATTENDANCE & GRADEBOOK"):
         show_course = st.checkbox("SHOW COURSE ATTENDANCE AND GRADEBOOK EXPORT", key="show_course_stats")
         if  show_course:
             courses = staff_courses()
             if  not courses:
                 st.info("NO COURSES ARE ASSIGNED TO THIS ACCOUNT")
                 return
             options = {course["id"]: f"{course['code']} — {course['name']}".upper() for course in courses}
             course_id = select_record("COURSE", options, "overview_course")
             api.get_course(course_id)
             stats = api.get_course_stats(course_id)
             cols = st.columns(3)
             cols[0].metric("ENROLLED STUDENTS", stats["total_enrolled"])
             cols[1].metric("SESSIONS TO DATE", stats["total_sessions"])
             cols[2].metric("ATTENDANCE RATE", f"{stats['overall_attendance_rate']:.1%}")
             dataframe(stats.get("student_attendance", []))
             if  stats.get("low_attendance_alerts"):
                 st.warning("ATTENDANCE BELOW 75% FOR ELIGIBLE SESSIONS")
                 dataframe(stats["low_attendance_alerts"])
             prepared_download("Course CSV", ("course", course_id), lambda: api.export_attendance(course_id),
                              f"course_{course_id}_attendance.csv")
             st.caption("NON-CANCELLED SESSIONS THROUGH TODAY (UTC)")

def time_inputs(prefix, start, end, labels=("START", "END")):
    st.caption("SINGAPORE TIME · UTC+08:00")
    cols = st.columns(2)
    with cols[0]:
         start_day   = st.date_input(f"{labels[0]} DATE", value=start.date(), key=f"{prefix}_start_day")
         start_clock = st.time_input(f"{labels[0]} TIME", value=start.time().replace(tzinfo=None), key=f"{prefix}_start_clock")
    with cols[1]:
         end_day     = st.date_input(f"{labels[1]} DATE", value=end.date(), key=f"{prefix}_end_day")
         end_clock   = st.time_input(f"{labels[1]} TIME", value=end.time().replace(tzinfo=None), key=f"{prefix}_end_clock")
    return local_datetime(start_day, start_clock), local_datetime(end_day, end_clock)

def venue_inputs(prefix, existing=None):
    existing = existing or {}
    name = st.text_input("VENUE NAME", value=existing.get("venue_name") or "", max_chars=255, key=f"{prefix}_venue")
    cols = st.columns(3)
    latitude  = cols[0].number_input("LATITUDE",        min_value=-90.0,  max_value=90.0,    value=existing.get("venue_latitude"),  format="%.6f",       key=f"{prefix}_latitude")
    longitude = cols[1].number_input("LONGITUDE",       min_value=-180.0, max_value=180.0,   value=existing.get("venue_longitude"), format="%.6f",       key=f"{prefix}_longitude")
    radius    = cols[2].number_input("RADIUS (METRES)", min_value=1.0,    max_value=10000.0, value=float(existing.get("geofence_radius_meters") or 100), key=f"{prefix}_radius")
    return {"venue_name": name.strip() or None, "venue_latitude": latitude, "venue_longitude": longitude, "geofence_radius_meters": radius}

def validate_venue(venue):
    if  venue and (venue["venue_latitude"] is None or venue["venue_longitude"] is None):
        raise ValueError("ENTER BOTH VENUE COORDINATES WHEN OVERRIDING THE COURSE VENUE")

def create_session_form():
    st.subheader("CREATE NEW SESSION")
    courses = staff_courses(active_only=True)
    if  not courses:
        st.info("YOU NEED AN ACTIVE COURSE ASSIGNMENT BEFORE YOU CAN CREATE A SESSION")
        return
    options  = {course["id"]: f"{course['code']} — {course['name']}".upper() for course in courses}
    tomorrow = datetime.combine(datetime.now(LOCAL_TZ).date() + timedelta(days=1), time(9), LOCAL_TZ)
    override_venue = st.checkbox("SET A SESSION VENUE INSTEAD OF THE COURSE DEFAULT", key="create_override_venue")
    with st.form("create_session_form", clear_on_submit=False):
         name = st.text_input("SESSION NAME", max_chars=255, key="create_name")
         course_id = select_record("COURSE", options, "create_course")
         session_type = st.selectbox("SESSION TYPE", ["lecture", "tutorial", "lab", "exam"], format_func=str.upper, key="create_type")
         description = st.text_area("DESCRIPTION (OPTIONAL)", max_chars=5000, key="create_description")
         start, end = time_inputs("create", tomorrow, tomorrow + timedelta(hours=1))
         venue = venue_inputs("create") if override_venue else {}
         st.caption("CHECK-IN: 15 MINUTES BEFORE TO 30 MINUTES AFTER THE START. COURSE VERIFICATION RULES AND LIVENESS APPLY")
         submitted = submit_button("CREATE SESSION")
    if   submitted:
         try:
             if  not name.strip():
                 raise ValueError("A SESSION NAME IS REQUIRED")
             validate_window(start, end, require_future=True)
             validate_venue(venue)
             st.session_state.api.create_session({
                 "name": name.strip(), "course_id": course_id, "session_type": session_type,
                 "description": description.strip() or None, "scheduled_start": start.isoformat(),
                 "scheduled_end": end.isoformat(),
                 **venue,
             })
             changed("SESSION CREATED")
         except ValueError as exc:
             st.error(str(exc))
         except APIError as exc:
             show_error(exc)

def session_edit_form(session):
    sid = session["id"]
    with st.expander("EDIT SESSION"):
         edit_venue = st.checkbox("EDIT VENUE AND GEOFENCE", key=f"edit_venue_{sid}")
         with st.form(f"edit_session_{sid}"):
              name = st.text_input("SESSION NAME", value=session["name"], max_chars=255, key=f"edit_name_{sid}")
              description = st.text_area("DESCRIPTION", value=session.get("description") or "", max_chars=5000, key=f"edit_description_{sid}")
              types = ["lecture", "tutorial", "lab", "exam"]
              kind = st.selectbox("SESSION TYPE", types, index=types.index(session["session_type"]), format_func=str.upper, key=f"edit_type_{sid}")
              venue = venue_inputs(f"edit_{sid}", session) if edit_venue else {}
              times = None
              if  session["status"] == "scheduled":
                  start, end    = time_inputs(f"edit_{sid}",   parse_datetime(session["scheduled_start"]),  parse_datetime(session["scheduled_end"]))
                  opens, closes = time_inputs(f"window_{sid}", parse_datetime(session["checkin_opens_at"]), parse_datetime(session["checkin_closes_at"]), labels=("Check-in open", "Check-in close"))
                  st.caption("KEEP THE CHECK-IN WINDOW OVERLAPPING THE SESSION")
                  times = (start, end, opens, closes)
              else:
                  st.caption("TIMES CAN ONLY BE CHANGED WHILE A SESSION IS SCHEDULED")
              saved = submit_button("SAVE SESSION CHANGES")
         if  saved:
             try:
                 if  not name.strip():
                     raise ValueError("A SESSION NAME IS REQUIRED")
                 data = {"name": name.strip(), "description": description.strip() or None, "session_type": kind}
                 validate_venue(venue)
                 data.update(venue)
                 if  times:
                     validate_window(*times)
                     for field, value in zip(["scheduled_start", "scheduled_end", "checkin_opens_at", "checkin_closes_at"], times):
                         if  parse_datetime(session[field]) != value:
                             data[field] = value.isoformat()
                 st.session_state.api.update_session(sid, data)
                 changed("SESSION UPDATED")
             except ValueError as exc:
                 st.error(str(exc))
             except APIError as exc:
                 show_error(exc)

def session_actions(session):
    sid = session["id"]
    owner = st.session_state.user["role"] == "admin" or session["instructor_id"] == st.session_state.user["id"]
    if  not owner:
        st.info("YOU CAN VIEW THIS SESSION BUT EDITING IS RESTRICTED TO ITS OWNER OR AN ADMINISTRATOR")
        return
    session_edit_form(session)
    transitions = TRANSITIONS.get(session["status"], [])
    if  transitions:
        with st.form(f"status_form_{sid}"):
             status = st.selectbox("NEW STATUS", transitions, format_func=str.upper, key=f"session_status_{sid}")
             confirm = st.checkbox("I CONFIRM THIS STATUS CHANGE", key=f"confirm_status_{sid}")
             submitted = submit_button("UPDATE SESSION STATUS")
        if   submitted:
            if  not confirm:
                st.error("CONFIRM THE STATUS CHANGE FIRST")
            else:
                st.session_state.api.update_session(sid, {"status": status})
                changed(f"session status updated to {status}")
    if  session["status"] == "scheduled":
        with st.form(f"delete_form_{sid}"):
             confirm = st.checkbox("I CONFIRM DELETION OF THIS SCHEDULED SESSION", key=f"confirm_delete_{sid}")
             submitted = submit_button("DELETE SESSION")
        if   submitted:
            if  not confirm:
                st.error("CONFIRM DELETION FIRST")
            else:
                st.session_state.api.delete_session(sid)
                changed("SCHEDULED SESSION DELETED")

def sessions_page():
    page_intro("SESSIONS", "YOUR CLASSES, CHECK-IN WINDOWS AND ATTENDANCE")
    with st.expander("CREATE A SESSION", expanded=False):
         create_session_form()
    with panel():
         st.subheader("ALL SESSIONS")
         filter_col, page_col = st.columns([4, 1])
         with filter_col:
              status = st.selectbox("SESSION STATUS FILTER", ["all", "scheduled", "active", "closed", "cancelled"],
                                    format_func=lambda value: "ALL STATUSES" if value == "all" else value.upper(), key="sessions_status_filter")
         with page_col:
              number = page_number("sessions_page", (status,))
         filters = {"status": status} if status != "all" else {}
         response = st.session_state.api.get_sessions(limit=50, offset=(number - 1) * 50, **filters)
         sessions = response["items"]
         show_page(response, number, "sessions")
         dataframe(sessions, ["id", "name", "course_code", "status", "scheduled_start", "scheduled_end"])
    if  not sessions:
        return
    options = {row["id"]: f"{row['course_code']} — {row['name']} ({row['status']})".upper() for row in sessions}
    sid = select_record("SELECT A SESSION", options, "selected_session")
    session = st.session_state.api.get_session(sid)
    with panel():
         detail_header(session["name"].upper(), session["course_code"].upper(), session["status"])
         metadata([("STARTS", display_time(session["scheduled_start"]).upper()), ("ENDS", display_time(session["scheduled_end"]).upper()),
                   ("CHECK-IN OPENS", display_time(session["checkin_opens_at"]).upper()), ("CHECK-IN CLOSES", display_time(session["checkin_closes_at"]).upper())])
         now = datetime.now(LOCAL_TZ)
         window_open = parse_datetime(session["checkin_opens_at"]) <= now <= parse_datetime(session["checkin_closes_at"])
         st.caption("CHECK-IN IS OPEN." if session["status"] == "active" and window_open else "CHECK-IN IS NOT OPEN")
    attendance, settings = st.tabs(["ATTENDANCE", "SETTINGS"])
    with attendance:
         stats = st.session_state.api.get_session_stats(sid)
         for col, label, value in zip(st.columns(3), ["ENROLLED", "APPROVED", "ATTENDANCE RATE"],
                                     [stats["total_enrolled"], stats["approved_count"], f"{stats['attendance_rate']:.1%}"]):
             col.metric(label, value)
         with panel():
              st.subheader("SESSION CHECK-INS")
              checkins = st.session_state.api.get_session_checkins(sid)
              if  checkins:
                  dataframe(checkins, CHECKIN_COLUMNS)
              else:
                  empty_state("NO CHECK-INS YET", "STUDENT ATTENDANCE WILL APPEAR HERE")
              prepared_download("SESSION CSV", ("session", sid), lambda: st.session_state.api.export_session(sid), f"session_{sid}_attendance.csv")
    with settings:
         session_actions(session)
         with st.expander("VENUE & VERIFICATION"):
              metadata([("VENUE", session.get("venue_name") or "NOT CONFIGURED"),
                        ("COORDINATES", f"{session.get('venue_latitude')} / {session.get('venue_longitude')}".upper()),
                        ("RADIUS", f"{session.get('geofence_radius_meters')} m"),
                        ("LIVENESS REQUIRED", "YES" if session["require_liveness_check"] else "NO"),
                        ("FACE MATCH REQUIRED", "YES" if session["require_face_match"] else "NO"), ("SESSION ID", sid)])
              st.caption("COURSE ACTIVITY, ENROLLMENT AND CONSENT REQUIREMENTS ALSO APPLY")

def checkin_details(checkin_id):
    detail = st.session_state.api.get_checkin(checkin_id)
    detail_header((detail.get("student_name") or "STUDENT").upper(), f"{detail.get('session_name')} · {detail.get('course_code')}".upper(), detail["status"])
    for col, label, key in zip(st.columns(3), ["RISK SCORE", "LIVENESS SCORE", "FACE MATCH SCORE"], ["risk_score", "liveness_score", "face_match_score"]):
        score = score_text(detail.get(key))
        col.metric(label, "—" if score == "not recorded" else score)
    metadata([
        ("CHECKED IN", display_time(detail["checked_in_at"])),
        ("STUDENT EMAIL", detail.get("student_email")),
        ("LIVENESS", verification_text(detail.get("liveness_passed"))),
        ("FACE MATCH", verification_text(detail.get("face_match_passed")))
        ])
    with st.expander("EVIDENCE & HISTORY"):
         metadata([("LOCATION", f"{detail.get('latitude')} / {detail.get('longitude')}"), ("DISTANCE FROM VENUE", f"{detail.get('distance_from_venue_meters')} m")])

         if detail.get("risk_factors"):  st.write("RISK FACTORS", detail["risk_factors"])
         if detail.get("risk_signals"):  dataframe(detail["risk_signals"], ["type", "score", "weight", "details"])
         if detail.get("appeal_reason"): st.write("APPEAL", detail["appeal_reason"].upper())
         if detail.get("review_notes"):  st.write("PREVIOUS REVIEW", detail["review_notes"].upper())

    if  detail["status"] in {"flagged", "appealed"}:
        can_recheck, window_message = recheck_availability(detail["session_id"])
        decisions = ["approved", "rejected"] + ([RECHECK] if can_recheck else [])
        decision_key = f"decision_{checkin_id}"
        if  st.session_state.get(decision_key) not in [None, *decisions]:
            # note that an expired re-check must never silently turn into an approval
            st.session_state[decision_key] = None
            st.session_state[f"review_confirm_{checkin_id}"] = False
        st.caption(window_message)
        with st.form(f"review_form_{checkin_id}"):
             decision = st.selectbox(
                 "REVIEW DECISION", decisions, index=None, key=decision_key,
                  placeholder="CHOOSE A DECISION", format_func=lambda value: "REQUEST RE-CHECK" if value == RECHECK else value.capitalize(),
                  help="RE-CHECK IS AVAILABLE ONLY WHILE THE SESSION IS ACTIVE AND ITS CHECK-IN WINDOW IS OPEN"
                  )
             notes = st.text_area("REVIEW NOTES", max_chars=2000, height=85, placeholder="REASON FOR THIS DECISION", key=f"notes_{checkin_id}")
             confirm = st.checkbox("I CONFIRM THIS REVIEW DECISION", key=f"review_confirm_{checkin_id}")
             submitted = submit_button("SUBMIT REVIEW")
        if  submitted:
            if  not decision:
                st.error("CHOOSE A REVIEW DECISION. THE PREVIOUS RE-CHECK OPTION IS NO LONGER AVAILABLE")
            elif not confirm or not notes.strip():
                st.error("ENTER REVIEW NOTES AND CONFIRM THE DECISION BEFORE SUBMITTING")
            elif decision == RECHECK:
                # fetch again on submit. note the window may have closed while notes were being written
                still_open, message = recheck_availability(detail["session_id"])
                if  not still_open:
                    st.warning(message)
                else:
                    st.session_state.api.reverify_checkin(checkin_id, notes.strip())
                    changed("RE-CHECK REQUESTED. THE STUDENT CAN SUBMIT AGAIN WHILE THE CHECK-IN WINDOW REMAINS OPEN")
            else:
                st.session_state.api.review_checkin(checkin_id, decision, notes.strip())
                changed(f"check-in reviewed as {decision}.")
    elif detail["status"] == "pending":
        can_recheck, _ = recheck_availability(detail["session_id"])
        if  can_recheck:
            st.info("WAITING FOR THE STUDENT TO SUBMIT A FRESH CHECK-IN")
        else:
            st.warning("THIS RE-CHECK IS PENDING, BUT THE SESSION IS NO LONGER OPEN FOR RESUBMISSION. CONTACT AN ADMINISTRATOR TO RESOLVE THE RECORD")
    else:
        st.caption("THIS RECORD HAS NO PENDING REVIEW")

def face_enrollments_page():
    page_intro("FACE ENROLLMENTS", "APPROVE A STUDENT'S FACE ONLY AFTER CONFIRMING THEIR IDENTITY IN PERSON")
    api  = st.session_state.api
    data = api.get_pending_face_enrollments()
    if  not data["items"]:
        empty_state("NO ENROLLMENTS WAITING", "STUDENTS APPEAR HERE AFTER ENROLLING WHEN APPROVAL IS SWITCHED ON")
        return
    # face photos are never stored, so the in-person check is the identity evidence
    st.caption(f"{data['total']} WAITING · ASK THE STUDENT FOR THEIR STUDENT CARD AND ENROLLMENT SCREEN BEFORE DECIDING")
    for item in data["items"]:
        sid = item["id"]
        with panel():
             detail_header(item["full_name"].upper(), item["email"], "pending", label="Awaiting approval")
             metadata([
                 ("COURSES", ", ".join(item["courses"]) or "—"),
                 ("SUBMITTED", display_time(item["requested_at"])),
                 ("REPLACES AN APPROVED FACE", "YES" if item["replaces_existing_face"] else "NO"),
             ])
             with st.form(f"face_review_{sid}"):
                  decision = st.radio("DECISION", ["approve", "reject"], horizontal=True, key=f"face_decision_{sid}", format_func=str.upper)
                  notes    = st.text_area("NOTES", max_chars=2000, height=70, key=f"face_notes_{sid}",
                                          placeholder="E.G. CHECKED STUDENT CARD IN LT26 · A REASON IS REQUIRED WHEN REJECTING")
                  confirm  = st.checkbox("I CHECKED THIS STUDENT'S IDENTITY IN PERSON", key=f"face_confirm_{sid}")
                  submitted = submit_button("SUBMIT DECISION")
        if  submitted:
            if  not confirm:
                st.error("CONFIRM YOU CHECKED THE STUDENT'S IDENTITY IN PERSON BEFORE DECIDING")
            elif decision == "reject" and not notes.strip():
                st.error("GIVE THE STUDENT A REASON FOR THE REJECTION")
            elif decision == "approve":
                api.approve_face_enrollment(sid, notes.strip() or None)
                changed(f"FACE ENROLLMENT APPROVED FOR {item['full_name'].upper()}")
            else:
                api.reject_face_enrollment(sid, notes.strip())
                changed(f"FACE ENROLLMENT REJECTED FOR {item['full_name'].upper()}. THEY CAN ENROLL AGAIN")

def checkins_page():
    page_intro("CHECK-INS", "ATTENDANCE RECORDS AND REVIEW DECISIONS")
    api = st.session_state.api
    sessions = api.get_all_sessions()
    options = {"all": "ALL AUTHORIZED SESSIONS", **{row["id"]: f"{row['course_code']} — {row['name']}".upper() for row in sessions}}
    with panel():
         session_col, status_col, page_col = st.columns([2.6, 1.5, .8])
         with session_col:
              selected = select_record("SESSION FILTER", options, "checkins_session")
         session_id = None if selected == "all" else selected
         review_only = st.checkbox("SHOW ONLY CHECK-INS AWAITING REVIEW", key="checkins_review_only", help="FLAGGED AND APPEALED RECORDS, OLDEST FIRST")
         with status_col:
              status = st.selectbox(
                  "STATUS FILTER", ["all", "approved", "flagged", "rejected", "pending", "appealed"], key="checkins_status", disabled=review_only,
                  format_func=lambda value: "ALL STATUSES" if value == "all" else STATUS_LABELS[value].upper(),
              )
         with st.expander("MORE FILTERS"):
              risk_col, date_col = st.columns(2)
              with risk_col:
                   use_risk = st.checkbox("FILTER BY MINIMUM RISK", key="checkins_use_risk", disabled=review_only)
                   min_risk = st.slider("MINIMUM RISK", 0.0, 1.0, 0.5, 0.05, key="checkins_min_risk", disabled=review_only or not use_risk)
              with date_col:
                   use_dates = st.checkbox("FILTER BY DATE RANGE (SGT)", key="checkins_use_dates", disabled=review_only)
                   today = datetime.now(LOCAL_TZ).date()
                   start = st.date_input("FROM DATE", value=today - timedelta(days=7), key="checkins_start", disabled=review_only or not use_dates)
                   end   = st.date_input("TO DATE", value=today, key="checkins_end", disabled=review_only or not use_dates)
         filters = {}
         if  not review_only:
             if  use_risk:
                 filters["min_risk_score"] = min_risk
             if  use_dates:
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
         if  not rows:
             return
         st.download_button(
             "DOWNLOAD DISPLAYED PAGE CSV", data=safe_csv(rows, CHECKIN_COLUMNS), file_name=f"checkins_page_{number}.csv", mime="text/csv",
             help="EXPORTS THIS PAGE ONLY. USE SESSIONS OR THE COURSE GRADEBOOK FOR A FULL EXPORT"
         )

    names = {row["id"]: str(row.get("student_name") or "").upper() for row in rows}  # this is to align displayed columns using uppercase text and non breaking spaces
    name_parts = {key: value.rpartition(" ") for key, value in names.items()}  # again this is to align the final word in displayed student names
    prefix_width = max(len(prefix) for prefix, separator, last in name_parts.values())
    names = {
        key: prefix.ljust(prefix_width, "\u00a0") + "\u00a0" + last if separator else last
        for key, (prefix, separator, last) in name_parts.items()
    }
    sessions = {row["id"]: str(row.get("session_name") or "").upper() for row in rows}
    student_width = max(map(len, names.values()))
    session_width = max(map(len, sessions.values()))
    detail_options = {
        row["id"]: (
            names[row["id"]].ljust(student_width, "\u00a0")
            + " - "
            + sessions[row["id"]].ljust(session_width, "\u00a0")
            + f" ({row['status']})".upper()
        )
        for row in rows
    }

    checkin_id = select_record("SELECT A CHECK-IN", detail_options, "selected_checkin")
    with panel():
         checkin_details(checkin_id)

def audit_page():
    page_intro("AUDIT LOGS", "ACCOUNT ACTIVITY AND ATTENDANCE DECISIONS")
    if  st.session_state.user["role"] != "admin":
        st.info("AUDIT LOGS ARE RESTRICTED TO ADMINISTRATORS")
        return  # do not request administrator data with an instructor token
    api = st.session_state.api
    summary = api.get_audit_summary()
    totals, activity = st.columns([1, 3])
    with totals:
         st.metric("EVENTS · LAST 7 DAYS", summary["total_logs"])
    with activity:
        with panel():
             st.subheader("AUDIT ACTIVITY")
             if  summary.get("by_day"):
                 figure = px.bar(pd.DataFrame(summary["by_day"]), x="date", y="count", color_discrete_sequence=[ACCENT])
                 figure.update_xaxes(type="category")  # we keep a single day series on a date-category axis
                 figure.update_yaxes(dtick=max(1, math.ceil(max(day["count"] for day in summary["by_day"]) / 4)))
                 figure.update_layout(bargap=.65)
                 chart(figure, height=170)
             else:
                 empty_state("NO AUDIT ACTIVITY", "ACCOUNT EVENTS WILL APPEAR HERE")
    with st.expander("FILTER AUDIT LOGS"):
         action        = st.text_input("ACTION (EXACT MATCH)", max_chars=100, key="audit_action").strip()
         resource_type = st.text_input("RESOURCE TYPE (EXACT MATCH)", max_chars=100, key="audit_resource_type").strip()
         user_id       = st.text_input("USER ID (OPTIONAL UUID)", key="audit_user_id").strip()
         resource_id   = st.text_input("RESOURCE ID (OPTIONAL UUID)", key="audit_resource_id").strip()
         success = st.selectbox("RESULT", ["all", "successful", "failed"], format_func=lambda value: "ALL RESULTS" if value == "all" else value.upper(), key="audit_result")
         use_dates = st.checkbox("FILTER AUDIT DATES (SGT)", key="audit_use_dates")
         today = datetime.now(LOCAL_TZ).date()
         start = st.date_input("FROM DATE", value=today - timedelta(days=7), key="audit_start", disabled=not use_dates)
         end   = st.date_input("TO DATE", value=today, key="audit_end", disabled=not use_dates)
    filters = {}
    for field, value in [("action", action), ("resource_type", resource_type), ("user_id", user_id), ("resource_id", resource_id)]:
        if  value:
            if  field.endswith("_id"):
                try:
                    UUID(value)
                except ValueError:
                    st.error(f"{field} MUST BE A VALID UUID")
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

        # this is to uppercase table text while preserving booleans and source records
        for column in frame.columns:
            frame[column] = frame[column].map(lambda value: value.upper() if isinstance(value, str) else "NONE" if value is None else value)

        frame = frame.rename(columns={
            "timestamp": "TIME (SGT)", "user_email": "USER", "action": "ACTION",
            "resource_type": "RESOURCE", "resource_id": "RESOURCE ID", "severity": "SEVERITY", "success": "SUCCESSFUL"
        })

        colors = {"info": "#252a36", "warning": "#3b3225", "error": "#3c2830", "critical": "#462b33"}
        st.dataframe(frame.style.map(lambda value: f"background-color: {colors.get(str(value).lower(), '#252a36')}; color: #e4dce6", subset=["SEVERITY"]),
                     use_container_width=True, hide_index=True)
        st.download_button("DOWNLOAD DISPLAYED AUDIT PAGE CSV", data=safe_csv(rows, columns + ["details"]), file_name=f"audit_page_{number}.csv", mime="text/csv")
        options = {row["id"]: f"{row['timestamp']} — {row['action']}".upper() for row in rows}
        selected = select_record("AUDIT EVENT DETAILS", options, "selected_audit_event")
        event = next(row for row in rows if row["id"] == selected)
        with st.expander("EVENT DETAILS"): st.json(event["details"])

def metrics_page():
    page_intro("METRICS", "SERVICE HEALTH AND APPLICATION PERFORMANCE")
    prometheus = PrometheusClient(PROMETHEUS_URL)
    try:
        values = [prometheus.scalar(query) for query in QUERIES.values()]
        labels = ["REQUESTS / SECOND", "SUCCESSFUL RESPONSES", "ERROR RESPONSES", "RESPONSE TIME · P95"]
        for index, (col, label, value) in enumerate(zip(st.columns(4), labels, values)):
            suffix = " REQ PER SEC" if index == 0 else " ms" if index == 3 else "%"
            col.metric(label, "—" if value is None else f"{value:.2f}{suffix}",
                       help="FIVE-MINUTE WINDOW, EXCLUDING HEALTH CHECKS AND MONITORING PROBES. A DASH MEANS NO MEASURED VALUE IS AVAILABLE.")
    except APIError as exc:
        st.warning(str(exc))
    left, right = st.columns([1, 1.25])
    with left:
        with panel():
             st.subheader("SERVICES")
             try:
                 health = st.session_state.api.get_health()
                 service_states(health)
                 if  health.get("status") != "healthy":
                     st.warning(f"SERVICE HEALTH: {health.get('status', 'unknown')}")
             except APIError as exc:
                 st.error(str(exc))
             grafana_url = os.getenv("GRAFANA_URL", "http://localhost:3001").rstrip("/")
             st.link_button("OPEN GRAFANA", f"{grafana_url}/d/saiv-operations/saiv-operations?theme=dark", use_container_width=True)
             with st.expander("SCRAPE TARGETS"):
                  try:
                      dataframe(prometheus.targets())
                  except APIError as exc:
                      st.warning(str(exc))
    recent = st.session_state.api.get_checkins(limit=100)["items"]
    with right:
         with panel():
              st.subheader("RECENT RISK")
              if  recent:
                  bands  = pd.Series([risk_band(row.get("risk_score")) for row in recent]).value_counts().rename_axis("risk").reset_index(name="count")
                  figure = px.bar(bands, x="risk", y="count", color_discrete_sequence=[ACCENT])
                  figure.update_layout(bargap=.6)
                  chart(figure, height=210)
                  st.caption(f"LATEST {len(recent)} CHECK-INS · MISSING SCORES ARE UNRECORDED")
              else:
                  empty_state("NO RISK DATA YET", "SCORES APPEAR AFTER STUDENTS CHECK IN")
    with panel():
         st.subheader("HIGH-RISK CHECK-INS")
         alerts = st.session_state.api.get_checkins(min_risk_score=0.5, limit=10)["items"]
         if  alerts:
             dataframe(alerts, CHECKIN_COLUMNS)
             st.caption("LATEST 10 WITH RISK ≥0.5 · REVIEW AVAILABILITY DEPENDS ON STATUS")
         else:
             empty_state("NO HIGH-RISK RECORDS", "NO AUTHORIZED CHECK-INS HAVE A RISK SCORE OF 0.5 OR HIGHER.")

# mutations require explicit form submission
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
         st.title("WELCOME BACK")
         st.caption("SIGN IN TO YOUR ATTENDANCE WORKSPACE")
         with st.form("login_form", clear_on_submit=True):
              st.markdown('<span class="saiv-login-font"></span>', unsafe_allow_html=True)
              email     = st.text_input("EMAIL", key="login_email", placeholder="name@example.com")
              password  = st.text_input("PASSWORD", type="password", key="login_password")
              submitted = st.form_submit_button("SIGN IN", type="primary", use_container_width=True)
         if   submitted:
              try:
                  data = st.session_state.api.login(email.strip(), password)
                  if  data["user"]["role"] not in {"instructor", "admin"}:
                      try:
                          st.session_state.api.logout()
                      except APIError:
                          pass  # logout clears local tokens even if server revocation fails
                      st.error("AN INSTRUCTOR OR ADMINISTRATOR ACCOUNT IS REQUIRED")
                  else:
                      st.session_state.user = data["user"]
                      st.rerun()
              except APIError as exc:
                  st.error(str(exc))
         st.caption("FOR INSTRUCTORS AND ADMINISTRATORS")
    st.stop()

with st.sidebar: brand()

navigation_labels = {"Overview": "OVERVIEW", "Sessions": "SESSIONS", "Check-ins": "CHECKINS", "Face enrollments": "FACE ENROLLMENTS",
                     "Audit logs": "SYS LOGS - AUDIT", "Metrics": "MEASURES"}
page = st.sidebar.radio("Navigation",options=list(navigation_labels),format_func=lambda value: navigation_labels[value],key="navigation",label_visibility="collapsed")

try:
    user = st.session_state.api.get_current_user()
    if  user["role"] not in {"instructor", "admin"}:
        reset_login("THIS DASHBOARD REQUIRES AN INSTRUCTOR OR ADMINISTRATOR ACCOUNT")
    st.session_state.user = user
except APIError as exc:
    show_error(exc)
    st.stop()
st.sidebar.divider()
with st.sidebar:
     profile(user["full_name"], user["role"])
if   st.sidebar.button("REFRESH DATA", use_container_width=True):
     st.session_state.pop("prepared_export", None)
auto_refresh = st.sidebar.checkbox("AUTO-REFRESH · 30S", key="auto_refresh", disabled=st_autorefresh is None,
                                   help="REFRESHES OVERVIEW, CHECK-INS AND METRICS. SESSION FORMS AND AUDIT LOGS STAY MANUAL")
if  st_autorefresh is None:
    st.sidebar.caption("USE REFRESH DATA TO UPDATE THE PAGE")
if  st_autorefresh is not None and auto_refresh and page in {"Overview", "Check-ins", "Metrics"}:
    # timer reruns do not resubmit forms
    st_autorefresh(interval=30_000, key=f"refresh_{page}")
st.sidebar.caption(f"UPDATED {datetime.now(LOCAL_TZ):%H:%M} SGT")
if  st.sidebar.button("SIGN OUT", use_container_width=True):
    warning = None
    try:
        st.session_state.api.logout()
    except APIError:
        warning = "YOU ARE SIGNED OUT HERE, BUT BACKEND SESSION CANCELLATION COULD NOT BE CONFIRMED"
    reset_login(warning)
notice = st.session_state.pop("notice", None)
if  notice:
    st.success(notice)
try:
    {"Overview": overview_page, "Sessions": sessions_page, "Check-ins": checkins_page, "Face enrollments": face_enrollments_page,
     "Audit logs": audit_page, "Metrics": metrics_page}[page]()
except APIError as exc:
    # invalidate prepared exports after an authorization failure
    st.session_state.pop("prepared_export", None)
    show_error(exc)
