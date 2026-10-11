
""" COMMENTARY
- SAIV mod 4 instructor dashboard with the mod 2 APIs
"""
import csv , io , json , math , os
import pandas as pd
import plotly.express as px
import streamlit as st
from api_client import APIClient, APIError, PrometheusClient, QUERIES
from datetime   import datetime, time, timedelta, timezone
from uuid       import UUID
from zoneinfo   import ZoneInfo


try:
	from streamlit_autorefresh import st_autorefresh
except ImportError:
	st_autorefresh = None

st.set_page_config(page_title="SAIV INSTRUCTOR DASHBOARD", layout="wide")
BACKEND_URL    = os.getenv("BACKEND_URL",    "http://localhost:8000")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")

LOCAL_TZ = ZoneInfo("Asia/Singapore")  # TODO - does this need to be case sensitive
TRANSITIONS     = {"scheduled": ["active", "cancelled"], "active": ["closed", "cancelled"],
			       "closed": ["cancelled"], "cancelled": []}
CHECKIN_COLUMNS = ["id", "student_name", "student_email", "course_code", "session_name", "status", "checked_in_at", "risk_score"]

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
		if closes <= opens:
			raise ValueError("check-in closing time must be after opening time")
		if opens > end or closes < start:
			raise ValueError("the check-in window must overlap the session")


def date_filters(start, end):
	if  end < start:
		raise ValueError("the end date must not precede the start date.")
	# sending UTC offsets and the backend does not has to guess a timezone
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
			# this is so the text from the users will not become a spreadsheet formula on opening
			if  isinstance(value, str) and (value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))):
				value = "'" + value
			values.append(value)
		writer.writerow(values)
	return output.getvalue().encode("utf-8")

def reset_login(message=None):
	st.session_state.api.clear_auth()
	# this removes all the user data and prepared downloads and widgets before another login
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
		st.caption(f"backend request id: {exc.request_id}")

def changed(message):
	""" apply page resets at the start of the next run before widgets are created
	"""
	st.session_state.pop("prepared_export", None)
	st.session_state.reset_pages = True
	st.session_state.notice = message
	st.session_state.skip_submissions = True
	st.rerun()

def submit_button(label):
	clicked = st.form_submit_button(label)
	return clicked and not skip_submissions

def select_record(label, options, key):
	current = st.session_state.get(key)
	st.session_state[key] = current if current in options else next(iter(options))
	# the labels can change after editing
	# preserve the authorised ID across reruns
	return st.selectbox(label, list(options), index=None, format_func=options.get, key=key)

def dataframe(rows, columns=None):
	if  rows:
		frame = pd.DataFrame(rows)
		if  columns:
			frame = frame.reindex(columns=columns)
		st.dataframe(frame, use_container_width=True, hide_index=True)

def page_number(key, context):
	context_key = f"{key}_context"
	if  st.session_state.get(context_key) != context:
		st.session_state[key] = 1
		st.session_state[context_key] = context
	if  key not in st.session_state:
		st.session_state[key] = 1
	return int(st.number_input("PAGE", min_value=1, max_value=20001, step=1, key=key))  # no have value=1 .. that gives a warning on the website

def show_page(response, number, noun):
	start = (number - 1) * response["limit"]
	count, total = len(response["items"]), response["total"]
	st.caption(f"showing {count} of {total} authorized {noun}. page {number}")
	if   total and start >= total:
		 st.info("this page has no records. choose an earlier page")
	elif not count:
		st.info(f"no {noun} match the current filters")

def staff_courses(*, active_only=False):
	user = st.session_state.user
	# the course listing itself is not scoped
	# the instructors must request their assignments
	courses = st.session_state.api.get_all_courses(
		instructor_id=user["id"] if user["role"] == "instructor" else None
	)
	return [course for course in courses if course.get("is_active")] if active_only else courses

def prepared_download(label, scope, fetch, filename):
	key = (st.session_state.user["id"], *scope)
	prepared = st.session_state.get("prepared_export")
	if  prepared and prepared["key"] != key:
		st.session_state.pop("prepared_export", None)
	if  st.button(f"PREPARE {label}", key=f"prepare_{scope[0]}"):
		st.session_state.pop("prepared_export", None)
		try:
			st.session_state.prepared_export = {"key": key, "data": fetch()}
		except APIError as exc:
			show_error(exc)
	prepared = st.session_state.get("prepared_export")
	if  prepared and prepared["key"] == key:
		st.download_button(f"DOWNLOAD {label}", data=prepared["data"],
						   file_name=filename, mime="text/csv", key=f"download_{scope[0]}")
		st.caption("this is a prepared snapshot. prepare it again to include later changes.")

def overview_page():
	st.header("OVERVIEW")
	api = st.session_state.api
	overview = api.get_overview_stats()
	for col, label, value in zip(st.columns(4),
			["TOTAL SESSIONS", "ACTIVE SESSIONS", "CHECK-INS TODAY (UTC)", "APPROVAL RATE"],
			[overview["total_sessions"], overview["active_sessions"], overview["total_checkins_today"], f"{overview['approval_rate']:.1%}"]):
		col.metric(label, value)
	st.caption("approval rate uses all retained check ins. an active status does not on its own open a check in window")
	st.metric("AWAITING REVIEW", overview["flagged_pending_review"])
	recent = api.get_checkins(limit=10)["items"]
	left, right = st.columns(2)
	with left:
		st.subheader("RECENT VERIFICATION STATUS")
		if  recent:
			counts = pd.DataFrame(recent)["status"].value_counts().rename_axis("status").reset_index(name="count")
			st.plotly_chart(px.pie(counts, names="status", values="count"), use_container_width=True)
			st.caption("latest 10 authorized check ins. this is a sample not an all-time breakdown")
		else:
			st.info("no check ins are recorded for your courses yet")
	with right:
		st.subheader("DAILY CHECK-INS")
		trend = overview.get("trends", {}).get("checkins_by_day", [])
		if  trend:
			st.plotly_chart(px.line(pd.DataFrame(trend), x="date", y="count", markers=True), use_container_width=True)
			st.caption("daily totals are grouped by utc dates by module 2")
	st.subheader("RECENT CHECK-INS")
	dataframe(recent, CHECKIN_COLUMNS)
	if  st.checkbox("SHOW COURSE ATTENDANCE AND GRADEBOOK EXPORT", key="show_course_stats"):
		courses = staff_courses()
		if  not courses:
			st.info("no courses are assigned to your account")
			return
		options = {course["id"]: f"{course['code']} — {course['name']}" for course in courses}
		course_id = select_record("COURSE", options, "overview_course")
		# check again the course access before showing any prepared download
		api.get_course(course_id)
		stats = api.get_course_stats(course_id)
		cols  = st.columns(3)
		cols[0].metric("ENROLLED STUDENTS",  stats["total_enrolled"])
		cols[1].metric("SESSIONS TO DATE",   stats["total_sessions"])
		cols[2].metric("ATTENDANCE RATE", f"{stats['overall_attendance_rate']:.1%}")
		dataframe(stats.get("student_attendance", []))
		if  stats.get("low_attendance_alerts"):
			st.warning("students below 75% attendance for eligible sessions:")
			dataframe(stats["low_attendance_alerts"])
		prepared_download("COURSE CSV", ("course", course_id),
						   lambda: api.export_attendance(course_id), f"course_{course_id}_attendance.csv")
		st.caption("course statistics and export include non-cancelled sessions through the backend's current utc day")

def time_inputs(prefix, start, end, labels=("START", "END")):
	st.caption("dates and times use asia/singapore (utc+08:00)")
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
		raise ValueError("enter both venue coordinates when overriding the course venue")

def create_session_form():
	st.subheader("CREATE NEW SESSION")
	courses = staff_courses(active_only=True)
	if  not courses:
		st.info("you need an active course assignment before you can create a session.")
		return
	options  = {course["id"]: f"{course['code']} — {course['name']}" for course in courses}
	tomorrow = datetime.combine(datetime.now(LOCAL_TZ).date() + timedelta(days=1), time(9), LOCAL_TZ)
	override_venue = st.checkbox("SET A SESSION VENUE INSTEAD OF THE COURSE DEFAULT", key="create_override_venue")
	with st.form("create_session_form", clear_on_submit=False):
		name = st.text_input("SESSION NAME", max_chars=255, key="create_name")
		course_id = select_record("COURSE", options, "create_course")
		session_type = st.selectbox("SESSION TYPE", ["lecture", "tutorial", "lab", "exam"], key="create_type")
		description = st.text_area("DESCRIPTION (OPTIONAL)", max_chars=5000, key="create_description")
		start, end = time_inputs("create", tomorrow, tomorrow + timedelta(hours=1))
		venue = venue_inputs("create") if override_venue else {}
		st.caption("the backend sets check in opening to 15 min before the start and closing to 30 min after start. venue and face requirements are inherited from the course and liveness stays required")
		submitted = submit_button("CREATE SESSION")
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
			changed("session created and saved in the backend")
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
			kind = st.selectbox("SESSION TYPE", types, index=types.index(session["session_type"]), key=f"edit_type_{sid}")
			venue = venue_inputs(f"edit_{sid}", session) if edit_venue else {}
			times = None
			if  session["status"] == "scheduled":
				start, end    = time_inputs(f"edit_{sid}",   parse_datetime(session["scheduled_start"]),  parse_datetime(session["scheduled_end"]))
				opens, closes = time_inputs(f"window_{sid}", parse_datetime(session["checkin_opens_at"]), parse_datetime(session["checkin_closes_at"]), labels=("CHECK-IN OPEN", "CHECK-IN CLOSE"))
				st.caption("keep the check-in window overlapping the session")
				times = (start, end, opens, closes)
			else:
				st.caption("times can only be changed while a session is scheduled")
			saved = submit_button("SAVE SESSION CHANGES")
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
				changed("session changes saved in the backend")
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
			status = st.selectbox("NEW STATUS", transitions, key=f"session_status_{sid}")
			confirm = st.checkbox("I confirm this status change", key=f"confirm_status_{sid}")
			submitted = submit_button("UPDATE SESSION STATUS")
		if  submitted:
			if  not confirm:
				st.error("confirm the status change first")
			else:
				st.session_state.api.update_session(sid, {"status": status})
				changed(f"session status updated to {status}")
	if  session["status"] == "scheduled":
		with st.form(f"delete_form_{sid}"):
			confirm = st.checkbox("i confirm deletion of this scheduled session", key=f"confirm_delete_{sid}")
			submitted = submit_button("DELETE SESSION")
		if  submitted:
			if  not confirm:
				st.error("confirm deletion first")
			else:
				st.session_state.api.delete_session(sid)
				changed("scheduled session deleted")

def sessions_page():
	st.header("SESSIONS")
	create_session_form()  # this is to help keep creation reachable even when the session list is empty
	st.divider()
	st.subheader("ALL SESSIONS")
	status   = st.selectbox("SESSION STATUS FILTER", ["all", "scheduled", "active", "closed", "cancelled"], key="sessions_status_filter")
	number   = page_number("sessions_page", (status,))
	filters  = {"status": status} if status != "all" else {}
	response = st.session_state.api.get_sessions(limit=50, offset=(number - 1) * 50, **filters)
	sessions = response["items"]
	show_page(response, number, "sessions")
	dataframe(sessions, ["id", "name", "course_code", "status", "scheduled_start", "scheduled_end"])
	if  not sessions:
		return
	options = {row["id"]: f"{row['course_code']} — {row['name']} ({row['status']})" for row in sessions}
	sid = select_record("SELECT A SESSION", options, "selected_session")
	session = st.session_state.api.get_session(sid)
	st.subheader("SESSION DETAILS")
	cols = st.columns(2)
	with cols[0]:
		st.write(f"**SESSION ID:** {sid}")
		st.write(f"**NAME:** {session['name']}")
		st.write(f"**COURSE:** {session['course_code']}")
		st.write(f"**STATUS:** {session['status']}")
	with cols[1]:
		st.write(f"**START:** {display_time(session['scheduled_start'])}")
		st.write(f"**END:** {display_time(session['scheduled_end'])}")
		st.write(f"**CHECK-IN OPENS:** {display_time(session['checkin_opens_at'])}")
		st.write(f"**CHECK-IN CLOSES:** {display_time(session['checkin_closes_at'])}")
	now = datetime.now(LOCAL_TZ)
	window_open = parse_datetime(session["checkin_opens_at"]) <= now <= parse_datetime(session["checkin_closes_at"])
	st.info("the session status and check-in window are both open." if session["status"] == "active" and window_open
			else "attendance requires an active session and the current time inside its check-in window")
	st.caption("course activity, enrollment, consent and verification rules are also enforced by module 2")
	st.caption(f"venue: {session.get('venue_name') or 'Not configured'}, latitude: {session.get('venue_latitude')}, longitude: {session.get('venue_longitude')}; radius: {session.get('geofence_radius_meters')} metres. liveness required: {session['require_liveness_check']}, face match required: {session['require_face_match']}.")
	session_actions(session)
	st.subheader("SESSION ATTENDANCE")
	stats = st.session_state.api.get_session_stats(sid)
	for col, label, value in zip(st.columns(3), ["ENROLLED", "APPROVED", "ATTENDANCE RATE"],
							    [stats["total_enrolled"], stats["approved_count"], f"{stats['attendance_rate']:.1%}"]):
		col.metric(label, value)
	st.subheader("SESSION EXPORT")
	prepared_download("SESSION CSV", ("session", sid), lambda: st.session_state.api.export_session(sid),
					 f"session_{sid}_attendance.csv")
	st.subheader("SESSION CHECK-INS")
	checkins = st.session_state.api.get_session_checkins(sid)
	if  checkins:
		dataframe(checkins, CHECKIN_COLUMNS)
	else:
		st.info("no check-ins are recorded for this session yet")

def checkin_details(checkin_id):
	detail = st.session_state.api.get_checkin(checkin_id)
	st.subheader("CHECK-IN DETAILS")
	st.write(f"**STUDENT:** {detail.get('student_name')} ({detail.get('student_email')})")
	st.write(f"**SESSION:** {detail.get('session_name')} — {detail.get('course_code')}")
	st.write(f"**STATUS:**  {detail['status']} · **CHECKED IN:** {display_time(detail['checked_in_at'])}")
	cols = st.columns(3)
	cols[0].metric("RISK SCORE",       score_text(detail.get("risk_score")))
	cols[1].metric("LIVENESS SCORE",   score_text(detail.get("liveness_score")))
	cols[2].metric("FACE MATCH SCORE", score_text(detail.get("face_match_score")))
	st.write(f"**LIVENESS:** {verification_text(detail.get('liveness_passed'))} · **FACE MATCH:** {verification_text(detail.get('face_match_passed'))}")
	st.write(f"**LOCATION:** {detail.get('latitude')} / {detail.get('longitude')} · **DISTANCE FROM VENUE:** {detail.get('distance_from_venue_meters')} metres")
	if  detail.get("risk_factors"):
		st.write("**RISK FACTORS:**", detail["risk_factors"])
	if  detail.get("risk_signals"):
		st.subheader("RISK SIGNALS")
		dataframe(detail["risk_signals"], ["type", "score", "weight", "details"])
	if  detail.get("appeal_reason"):
		st.write("**APPEAL:**", detail["appeal_reason"])
	if  detail.get("review_notes"):
		st.write("**REVIEW NOTES:**", detail["review_notes"])
	if  detail["status"] in {"flagged", "appealed"}:
		with st.form(f"review_form_{checkin_id}"):
			decision = st.selectbox("REVIEW DECISION", ["approved", "rejected"], key=f"decision_{checkin_id}")
			notes = st.text_area("REVIEW NOTES", max_chars=2000, key=f"notes_{checkin_id}")
			confirm = st.checkbox("I confirm this review decision", key=f"review_confirm_{checkin_id}")
			submitted = submit_button("SUBMIT REVIEW")
		if  submitted:
			if  not confirm or not notes.strip():
				st.error("enter review notes and confirm the decision before submitting")
			else:
				st.session_state.api.review_checkin(checkin_id, decision, notes.strip())
				changed(f"check-in reviewed as {decision}.")
	else:
		st.info("only flagged or appealed check-ins can be reviewed")

def checkins_page():
	st.header("CHECK-INS")
	api         = st.session_state.api
	sessions    = api.get_all_sessions()
	options     = {"all": "ALL AUTHORIZED SESSIONS", **{row["id"]: f"{row['course_code']} — {row['name']}" for row in sessions}}
	selected    = select_record("SESSION FILTER", options, "checkins_session")
	session_id  = None if selected == "all" else selected
	review_only = st.checkbox("SHOW ONLY CHECK-INS AWAITING REVIEW", key="checkins_review_only")
	status = st.selectbox("STATUS FILTER", ["all", "approved", "flagged", "rejected", "pending", "appealed"], key="checkins_status", disabled=review_only)
	with st.expander("MORE FILTERS"):
		use_risk  = st.checkbox("FILTER BY MINIMUM RISK", key="checkins_use_risk", disabled=review_only)
		min_risk  = st.slider("MINIMUM RISK", 0.0, 1.0, 0.5, 0.05, key="checkins_min_risk", disabled=review_only or not use_risk)
		use_dates = st.checkbox("FILTER BY DATE RANGE (SGT)", key="checkins_use_dates", disabled=review_only)
		today     = datetime.now(LOCAL_TZ).date()
		start     = st.date_input("FROM DATE", value=today - timedelta(days=7), key="checkins_start", disabled=review_only or not use_dates)
		end       = st.date_input("TO DATE",   value=today,                     key="checkins_end",   disabled=review_only or not use_dates)
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
	context  = (session_id, review_only, None if review_only else status, tuple(sorted(filters.items())))
	number   = page_number("checkins_page", context)
	response = api.get_checkins(status=None if status == "all" else status, session_id=session_id,
							    review_only=review_only, limit=50, offset=(number - 1) * 50, **filters)
	rows = response["items"]
	show_page(response, number, "check-ins")
	if  review_only:
		st.caption("the review queue includes flagged and appealed records, oldest first. module 2 supports session filtering for this queue")
	dataframe(rows, CHECKIN_COLUMNS)
	if  not rows:
		return
	st.download_button("DOWNLOAD DISPLAYED PAGE CSV", data=safe_csv(rows, CHECKIN_COLUMNS),
					   file_name=f"checkins_page_{number}.csv", mime="text/csv")
	st.caption("this download contains the displayed page only. use sessions or course attendance for the full gradebook csv")
	detail_options = {row["id"]: f"{row.get('student_name')} — {row.get('session_name')} ({row['status']})" for row in rows}
	checkin_id = select_record("SELECT A CHECK-IN", detail_options, "selected_checkin")
	checkin_details(checkin_id)

def audit_page():
	st.header("AUDIT LOGS")
	if  st.session_state.user["role"] != "admin":
		st.info("the current backend restricts audit logs to administrators. sign in with an administrator account to view this page")
		return  # no request admin data with an instructor token
	api = st.session_state.api
	summary = api.get_audit_summary()
	st.metric("AUDIT EVENTS IN THE LAST 7 DAYS", summary["total_logs"])
	if  summary.get("by_day"):
		st.plotly_chart(px.bar(pd.DataFrame(summary["by_day"]), x="date", y="count"), use_container_width=True)
	with st.expander("FILTER AUDIT LOGS"):
		action = st.text_input("ACTION (EXACT MATCH)", max_chars=100, key="audit_action").strip()
		resource_type = st.text_input("RESOURCE TYPE (EXACT MATCH)", max_chars=100, key="audit_resource_type").strip()
		user_id = st.text_input("USER ID (OPTIONAL UUID)", key="audit_user_id").strip()
		resource_id = st.text_input("RESOURCE ID (OPTIONAL UUID)", key="audit_resource_id").strip()
		success = st.selectbox("RESULT", ["all", "successful", "failed"], key="audit_result")
		use_dates = st.checkbox("FILTER AUDIT DATES (SGT)", key="audit_use_dates")
		today = datetime.now(LOCAL_TZ).date()
		start = st.date_input("FROM DATE", value=today - timedelta(days=7), key="audit_start", disabled=not use_dates)
		end = st.date_input("TO DATE", value=today, key="audit_end", disabled=not use_dates)
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
		colours = {"info": "#dbeafe", "warning": "#fef3c7", "error": "#fee2e2", "critical": "#fecaca"}
		st.dataframe(frame.style.map(lambda value: f"background-color: {colours.get(value, '#f3f4f6')}; color: #111827", subset=["severity"]),
					 use_container_width=True, hide_index=True)
		st.download_button("DOWNLOAD DISPLAYED AUDIT PAGE CSV", data=safe_csv(rows, columns + ["details"]),
						    file_name=f"audit_page_{number}.csv", mime="text/csv")
		options = {row["id"]: f"{row['timestamp']} — {row['action']}" for row in rows}
		selected = select_record("AUDIT EVENT DETAILS", options, "selected_audit_event")
		event = next(row for row in rows if row["id"] == selected)
		st.json(event["details"])
		st.caption("details are redacted by module 2. the csv contains the displayed page only")

def metrics_page():
	st.header("METRICS")
	st.subheader("BACKEND HEALTH")
	try:
		health = st.session_state.api.get_health()
		dataframe([{"Component": field, "State": health.get(field, "unknown")} for field in ["api", "database", "redis"]])
		if  health.get("status") == "healthy":
			st.success("the backend health endpoint reports healthy")
		else:
			st.warning(f"backend health: {health.get('status', 'unknown')}")
	except APIError as exc:
		st.error(
		str(exc)
		)
	st.subheader("HTTP PERFORMANCE (PROMETHEUS)")
	prometheus = PrometheusClient(PROMETHEUS_URL)
	try:
		values = [prometheus.scalar(query) for query in QUERIES.values()]
		for col, (label, value) in zip(st.columns(4), zip(QUERIES, values)):
			col.metric(label, "No data" if value is None else f"{value:.2f}")
		st.caption("rates and p95 use the last 5 minutes. missing samples or no traffic can produce no data; this is not a measured zero")
		dataframe(prometheus.targets())
		st.markdown(f"[Open Grafana]({os.getenv('GRAFANA_URL', 'http://localhost:3001')})")
	except APIError as exc:
		st.warning(
		str(exc)
		)
	st.subheader("RISK IN RECENT CHECK-INS")
	recent = st.session_state.api.get_checkins(limit=100)["items"]
	if  recent:
		bands = pd.Series([risk_band(row.get("risk_score")) for row in recent]).value_counts().rename_axis("risk").reset_index(name="count")
		st.plotly_chart(px.bar(bands, x="risk", y="count"), use_container_width=True)
		st.caption(f"sample of the latest {len(recent)} authorized check-ins. missing scores are shown as not recorded")
	else:
		st.info("no check-ins are available for risk analysis yet")
	st.subheader("HIGH-RISK RECORDS TO INVESTIGATE")
	alerts = st.session_state.api.get_checkins(min_risk_score=0.5, limit=10)["items"]
	if  alerts:
		dataframe(alerts, CHECKIN_COLUMNS)
		st.caption("latest 10 records with risk ≥0.5, including previously reviewed records. review eligibility is determined by status, not the score alone")
	else:
		st.info("no authorized check-ins have risk ≥0.5")

# think streamlit reruns this file on interactions
# the only explicit form submissions mutate data
if "api" not in st.session_state:
	st.session_state.api = APIClient(BACKEND_URL)
skip_submissions = st.session_state.pop("skip_submissions", False)
if  st.session_state.pop("reset_pages", False):
	for key in ["sessions_page", "checkins_page", "audit_page"]:
		st.session_state[key] = 1
	for key in list(st.session_state):
		if  key.startswith(("confirm_status_", "confirm_delete_", "review_confirm_")):
			st.session_state[key] = False
st.title("SAIV INSTRUCTOR DASHBOARD")
message = st.session_state.pop("auth_message", None)
if  message:
	st.warning(message)
if  not st.session_state.api.token:
	st.subheader("INSTRUCTOR LOGIN")
	with st.form("login_form", clear_on_submit=True):
		email = st.text_input("email", key="login_email")
		password = st.text_input("password", type="password", key="login_password")
		submitted = st.form_submit_button("Login")
	if  submitted:
		try:
			data = st.session_state.api.login(email.strip(), password)
			if  data["user"]["role"] not in {"instructor", "admin"}:
				try:
					st.session_state.api.logout()
				except APIError:
					pass  # local tokens are cleared by logout even if revocation fails
				st.error("an instructor or administrator account is required")
			else:
				st.session_state.user = data["user"]
				st.rerun()
		except APIError as exc:
			st.error(
			str(exc)
			)
	st.stop()

st.sidebar.title("NAVIGATION")
page = st.sidebar.radio("GO TO", ["OVERVIEW", "SESSIONS", "CHECK-INS", "AUDIT LOGS", "METRICS"], key="navigation")
if  st.sidebar.button("LOGOUT"):
	warning = None
	try:
		st.session_state.api.logout()
	except APIError:
		warning = "you are signed out here, but backend session cancellation could not be confirmed"
	reset_login(warning)
try:
	user = st.session_state.api.get_current_user()
	if  user["role"] not in {"instructor", "admin"}:
		reset_login("this dashboard requires an instructor or administrator account")
	st.session_state.user = user
except APIError as exc:
	show_error(exc)
	st.stop()
st.sidebar.caption(f"{user['full_name']} · {user['role']}")
st.sidebar.caption("Time inputs: Singapore (UTC+08:00)")
if  st.sidebar.button("REFRESH DATA"):
	st.session_state.pop("prepared_export", None)
auto_refresh = st.sidebar.checkbox("AUTO-REFRESH EVERY 30 SECONDS", key="auto_refresh", disabled=st_autorefresh is None)
if  st_autorefresh is None:
	st.sidebar.caption("use refresh data to update the page")
if  st_autorefresh is not None and auto_refresh and page in {"OVERVIEW", "CHECK-INS", "METRICS"}:
	# a browser timer triggers read only reruns and forms do not resubmit on its own
	st_autorefresh(interval=30_000, key=f"refresh_{page}")
notice = st.session_state.pop("notice", None)
if  notice:
	st.success(notice)
try:
	{"OVERVIEW": overview_page, "SESSIONS": sessions_page, "CHECK-INS": checkins_page,
	 "AUDIT LOGS": audit_page, "METRICS": metrics_page}[page]()
except APIError as exc:
	# a failed authorisation check would also invalidates any prepared attendance csv
	st.session_state.pop("prepared_export", None)
	show_error(exc)
