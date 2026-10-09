"""
SAIV INSTRUCTOR DASHBOARD

Students must implement the instructor dashboard with session management,
attendance monitoring , and metrics visualisation

TODOS
- audit log color coded by severity
- overview check-ins by hour chart
- overview verification status pie chart

- MODULE 2 INTEGRATION
    > replace mock authentication with module 2 login
    > replace mock sessions with API session data
    > replace mock checkins with API checkin data
    > replace mock audit logs with API audit logs
    > replace mock metrics with Prometheus data
    > connect session creation to module 2
    > connect session update/delete to module 2
    > ADD real-time check-in refresh
"""


import streamlit as st
import pandas as pd
import plotly.express as px
import os
from api_client import APIClient
from datetime   import datetime, timedelta, time
from zoneinfo   import ZoneInfo

# page setup
st.set_page_config(
    page_title = "SAIV INSTRUCTOR DASHBOARD",
    page_icon  = "O..O", layout     = "wide",  # Layout == page elements use the entire screen width
)

BACKEND_URL    = os.getenv("BACKEND_URL",    "http://localhost:8000")  # environment vars
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")  # environment vars


if "api" not in st.session_state:  # asking if the current streamlit session already has an API client
    st.session_state.api = APIClient(BACKEND_URL)

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

st.title("SAIV INSTRUCTOR DASHBOARD")
logout_warning = st.session_state.pop("logout warning", None)
if  logout_warning:
    st.warning(logout_warning)


if  not st.session_state.logged_in:
    st.subheader("INSTRUCTOR LOGIN")

    email = st.text_input("email")
    password = st.text_input("password", type="password")

    if  st.button("Login"):
        try:
            login_data = st.session_state.api.login(email, password)
            user = login_data["user"]

            if  user["role"] not in {"instructor", "admin"}:
                st.session_state.api.set_token(None)
                st.error("need instructor or admin account")
            else:
                st.session_state.login_data = login_data
                st.session_state.logged_in  = True
                st.rerun()
        except Exception as e:
            st.error(f"Login Failed : {e}")

    st.stop()


# navigation
st.sidebar.title("NAVIGATION")
page = st.sidebar.radio(  # this creates a group of selectable options
    "GO TO",
    [
        "OVERVIEW",
        "SESSIONS",
        "CHECK-INS",
        "AUDIT LOGS",
        "METRICS",
    ]
)


if  st.sidebar.button("LOGOUT"):
    try:
        st.session_state.api.logout()
    except Exception:
        st.session_state.logout_warning = (
            "You are logged out of this dashboard but backend session cancellation could not be confirmed"
        )
    finally:
        st.session_state.logged_in = False
        st.session_state.login_data = None
        st.session_state.pop("session export", None)  # clear attendance data when log out
    st.rerun()


if "sessions" not in st.session_state:  # TODO - this is mock data .. remove once mod 2 is up and running
    st.session_state.sessions = [
        {
            "id":          1,
            "name":       "CS50 Lecture 1",
            "course":     "CS50",
            "status":     "active",
            "start_time": "01-03-2026 10:00",
            "end_time":   "01-03-2026 12:00"
        },

        {
            "id":          2,
            "name":       "CS50 Seminar 1",
            "course":     "CS50",
            "status":     "inactive",
            "start_time": "01-01-2026 14:00",
            "end_time":   "01-01-2026 16:00"
        },

        {
            "id":          3,
            "name":       "CS88 Lecture 3",
            "course":     "CS88",
            "status":     "active",
            "start_time": "01-03-2026 10:00",
            "end_time":   "01-03-2026 12:00"
        }
    ]
sessions = st.session_state.sessions


every_checkin = [
    {
        "checkin_id":          "C001",
        "student_id":          "S001",
        "student_name":        "Alice Tan",
        "session_id":           1,
        "timestamp":           "10-10-2026 10:00",
        "verification_status": "success",
        "risk_score":           0.10,
        "liveness_score":       0.95,
        "face_match_score":     0.95,
        "latitude":             1.3521,
        "longitude":            103.8198
    },

    {
        "checkin_id":          "C002",
        "student_id":          "S002",
        "student_name":        "Benny Lim",
        "session_id":           1,
        "timestamp":           "10-10-2026 12:30",
        "verification_status": "success",
        "risk_score":           0.08,
        "liveness_score":       0.90,
        "face_match_score":     0.90,
        "latitude":             1.3333,
        "longitude":            102.8196
    },

    {
        "checkin_id":          "C003",
        "student_id":          "S003",
        "student_name":        "James Tay",
        "session_id":           1,
        "timestamp":           "10-10-2026 16:00",
        "verification_status": "flagged",
        "risk_score":           0.90,
        "liveness_score":       0.60,
        "face_match_score":     0.70,
        "latitude":             1.3500,
        "longitude":            103.8100
    },
]


# pages
if    page == "OVERVIEW":
    st.header("OVERVIEW")

    try:
        overview = st.session_state.api.get_overview_stats()
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric(
                "TOTAL  SESSIONS", overview["total_sessions"]
            )

        with col2:
            st.metric(
                "ACTIVE SESSIONS", overview["active_sessions"]
            )

        with col3:
            st.metric("CHECK INS TODAY (UTC)", overview["total_checkins_today"])

        with col4:
            approval_rate = overview["approval_rate"] * 100
            st.metric("APPROVAL RATE", f"{approval_rate:.1f}%")

        checkins_response = st.session_state.api.get_checkins()
        recent_checkins = checkins_response["items"][:10]  # sort new , display latest 10

        st.subheader("VERIFICATION STATUS — LATEST 10 CHECK INS")

        if  recent_checkins:
            status_df = pd.DataFrame(
                {"status": checkin["status"]}
                for checkin in recent_checkins
            )
            verification_df = (status_df["status"].value_counts().rename_axis("status").reset_index(name="count"))

            st.plotly_chart(
                px.pie(
                    verification_df,
                    names  = "status",
                    values = "count",
                ),
                use_container_width=True,
            )
        else:
            st.info("no check ins are available to display verification statuses")

        st.subheader("RECENT CHECK-INS")

        if  recent_checkins:
            recent_rows = [
                {
                    "Student": checkin["student_name"],
                    "Session": checkin["session_name"],
                    "Time":    checkin["checked_in_at"],
                    "Status":  checkin["status"],
                }
                for checkin in recent_checkins
            ]

            st.dataframe(
                recent_rows,
                use_container_width=True,  # makes the table use the available width of the page
                hide_index=True,
            )

        else:
            st.info("no check ins available for your courses yet")

        st.subheader("CHECK INS BY DAY (UTC)")

        daily_checkins = overview["trends"]["checkins_by_day"]
        daily_df = pd.DataFrame(daily_checkins)

        if  daily_df.empty:
            st.info("no daily check in data available")
        else:
            st.plotly_chart(
                px.bar(
                    daily_df,
                    x="date",
                    y="count",
                    labels={
                        "date":  "Date (UTC)",
                        "count": "Check ins",
                    },
                ),
                use_container_width=True,
            )

        st.json(overview)
    except Exception as e:
        st.error(f"could not load overview statistics : {e}")


elif  page == "SESSIONS":
    st.header("SESSIONS")

    deleted_message = st.session_state.pop("session_deleted_message", None)
    if  deleted_message:
        st.success(deleted_message)

    st.subheader("CREATE NEW SESSION")

    try:
        # we want to use the authenticated user id and not the manual entered one
        instructor_id = st.session_state.login_data["user"]["id"]
        courses_response = st.session_state.api.get_courses(
            instructor_id
        )
        courses = courses_response["items"]

    except Exception:
        st.error("could not load your assigned courses.")

    else:
        if  courses:
            course_options = {
                course["id"]: f"{course['code']} — {course['name']}"
                for course in courses
            }

            selected_course_id = st.selectbox(
                "COURSE",
                options=list(course_options),
                format_func=lambda course_id: course_options[course_id],
                key="create_session_course",
            )

            singapore = ZoneInfo("Asia/Singapore")  # we use singapore time
            tomorrow  = (
                datetime.now(singapore) + timedelta(days=1)
            ).date()

            created_message = st.session_state.pop("session_created_message", None)
            if  created_message:
                st.success(created_message)

            with st.form("backend_create_session_form"):
                session_name = st.text_input("SESSION NAME")

                start_date = st.date_input(
                    "START DATE", value=tomorrow
                )
                start_time = st.time_input(
                    "START TIME (SINGAPORE)", value=time(9, 0)
                )
                end_date   = st.date_input(
                    "END DATE", value=tomorrow
                )
                end_time   = st.time_input(
                    "END TIME (SINGAPORE)", value=time(10, 0)
                )

                st.caption(
                    "venue and geofence settings use course defaults. "
                    "liveness checking remains enabled."
                )

                submitted = st.form_submit_button("CREATE SESSION")

            if  submitted:
                # Include the UTC offset in timestamps sent to Module 2.
                scheduled_start = datetime.combine(
                    start_date, start_time, tzinfo=singapore
                )
                scheduled_end = datetime.combine(
                    end_date, end_time, tzinfo=singapore
                )

                if   not session_name.strip():
                    st.error("a session name is required.")
                elif scheduled_start <= datetime.now(singapore):
                    st.error("the session must start in the future.")
                elif scheduled_end <= scheduled_start:
                    st.error("the end must be after the start.")
                else:
                    try:
                        created_session = (
                            st.session_state.api.create_session(
                                {
                                    "course_id": selected_course_id,
                                    "name": session_name.strip(),
                                    "scheduled_start": scheduled_start.isoformat(),
                                    "scheduled_end": scheduled_end.isoformat(),
                                }
                            )
                        )

                        st.session_state.session_created_message = (
                            f"created session: {created_session['name']}"
                        )
                        st.rerun()

                    except Exception:
                        st.error(
                            "could not create the session. "
                            "the backend must also accept its settings "
                            "and your course permissions."
                        )

        else:
            st.info(
                "no assigned courses are available. "
                "an administrator must assign a course before "
                "you can create a session"
            )

    st.divider()

    st.subheader("ALL SESSIONS")

    try:
        sessions_response = st.session_state.api.get_sessions()
        sessions = sessions_response["items"]

        st.caption(
            f"Showing {len(sessions)} of "
            f"{sessions_response['total']} authorized sessions"  # show all result count even when API return one page
        )

        if sessions:
            st.dataframe(
                sessions,
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("no sessions are available for your courses yet")

    except Exception:
        st.error("could not load sessions. check your backend connection")
        st.stop()

    if  not sessions:  # empty list then cannot provide session to select
        st.stop()

    session_options = {
        session["id"]: session["name"]
        for session in sessions
    }

    selected_session_id = st.selectbox(
        "SELECT A SESSION",
        options=list(session_options),
        format_func=lambda session_id: session_options[session_id],
    )

    try:
        selected_session = st.session_state.api.get_session(selected_session_id)  # fetch details from mod 2 API
    except Exception:
        st.error("could not load the selected session")
        st.stop()

    st.subheader("SESSION DETAILS")

    col1, col2 = st.columns(2)

    with col1:
        st.write(f"**SESSION ID:** {selected_session['id']}")
        st.write(f"**NAME:** {selected_session['name']}")
        st.write(f"**COURSE:** {selected_session['course_code']}")

    with col2:
        st.write(f"**STATUS:** {selected_session['status']}")
        st.write(f"**START:** {selected_session['scheduled_start']}")
        st.write(f"**END:** {selected_session['scheduled_end']}")

    st.subheader("EDIT SESSION NAME")

    user = st.session_state.login_data["user"]

    # this si to match the backend owner rule
    can_manage_session = (
        user["role"] == "admin"
        or selected_session["instructor_id"] == user["id"]
    )

    update_message = st.session_state.pop(
        "session_update_message", None
    )
    if  update_message:
        st.success(update_message)

    if  can_manage_session:
        with st.form(f"edit_session_{selected_session_id}"):
            updated_name = st.text_input(
                "SESSION NAME",
                value=selected_session["name"],
                key=f"edit_name_{selected_session_id}",
            )
            save_name = st.form_submit_button("SAVE NAME")

        if  save_name:
            if not updated_name.strip():
                st.error("a session name is required.")
            else:
                try:
                    st.session_state.api.update_session(
                        selected_session_id,
                        {"name": updated_name.strip()},
                    )

                    # a previously prepared csv might contain old name
                    st.session_state.pop("session_export", None)
                    st.session_state.session_update_message = (
                        "Session name updated."
                    )
                    st.rerun()

                except Exception:
                    st.error("could not update the session name.")
    else:
        st.info(
            "only the owning instructor or an administrator "
            "can modify this session."
        )

    st.subheader("SESSION STATUS")

    # this is only transitions by mod 2
    allowed_transitions = {
        "scheduled": ["active", "cancelled"],
        "active": ["closed", "cancelled"],
        "closed": ["cancelled"],
        "cancelled": [],
    }

    current_status = selected_session["status"]
    next_statuses  = allowed_transitions.get(current_status, [])

    if  can_manage_session and next_statuses:
        with st.form(f"session_status_{selected_session_id}"):
            next_status = st.selectbox(
                "NEW STATUS",
                options=next_statuses,
            )

            # nned an explicit acknowledgement before changing status
            confirmed = st.checkbox(
                "I confirm this session status change."
            )
            change_status = st.form_submit_button("UPDATE STATUS")

        if  change_status:
            if not confirmed:
                st.error("confirm the status change first.")
            else:
                try:
                    st.session_state.api.update_session(
                        selected_session_id,
                        {"status": next_status},
                    )

                    st.session_state.pop("session_export", None)
                    st.session_state.session_update_message = (
                        f"Session status updated to {next_status}."
                    )
                    st.rerun()

                except Exception:
                    st.error("could not update the session status.")

    elif can_manage_session:
        st.info("This session has no further status transitions.")
    else:
        st.caption(
            "status changes require the session owner "
            "or an administrator."
        )

    if  can_manage_session and current_status == "scheduled":
        st.subheader("DELETE SCHEDULED SESSION")

        with st.form(f"delete_session_{selected_session_id}"):
            confirm_delete = st.checkbox(
                "i understand that this permanently deletes "
                "the selected session."
            )
            delete_submitted = st.form_submit_button("DELETE SESSION")

        if  delete_submitted:
            if not confirm_delete:
                st.error("Confirm deletion first.")
            else:
                try:
                    # successful deletion returns 204 with no json body.
                    st.session_state.api.delete_session(selected_session_id)
                    st.session_state.pop("session_export", None)
                    st.session_state.session_deleted_message = ("Scheduled session deleted.")
                    st.rerun()
                except Exception:
                    st.error("Could not delete the session.")

    st.subheader("SESSION EXPORT")

    # the prepared data for both the logged in user and selected session
    export_key = (
        st.session_state.login_data["user"]["id"],
        selected_session_id,
    )

    if  st.button("PREPARE SESSION CSV"):
        st.session_state.pop("session_export", None)  # clear older download before fresh export

        try:
            csv_data = st.session_state.api.export_session(
                selected_session_id
            )
            st.session_state.session_export = {
                "key":  export_key,
                "data": csv_data,
            }
        except Exception:
            st.error("could not prepare the session export.")

    prepared_export = st.session_state.get("session_export")

    if  prepared_export and prepared_export["key"] == export_key:
        st.download_button(
            "DOWNLOAD SESSION CSV",
            data=prepared_export["data"],
            file_name=f"session_{selected_session_id}_attendance.csv",
            mime="text/csv",
        )

    st.subheader("SESSION CHECK INS")

    try:
        session_checkins = (
            st.session_state.api.get_session_checkins(selected_session_id)  # returns an array
        )

        if  session_checkins:
            st.dataframe(
                session_checkins,
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("no check-ins are recorded for this session yet.")  # no records is valid result for new session

    except Exception:
        st.error("Could not load check-ins for this session.")

elif page ==  "CHECK-INS":
    st.header("CHECK-INS")

    try:
        sessions_response = st.session_state.api.get_sessions()
        filter_sessions   = sessions_response["items"]
    except Exception:
        st.error("could not load sessions for the filter.")
        st.stop()

    # None represents all sessions ,  real IDs remain UUID strings
    session_labels = {
        session["id"]: session["name"]
        for session in filter_sessions
    }

    selected_filter_session_id = st.selectbox(
        "SESSION",
        options=[None] + list(session_labels),
        format_func=lambda session_id: ("All" if session_id is None else session_labels[session_id]),
        key="checkins_session_filter",
    )

    # make the current pagination limit visible.
    if  sessions_response["total"] > len(filter_sessions):
        st.caption(
            "the session dropdown currently shows only "
            "the first page of authorized sessions."
        )

    review_only = st.checkbox("ONLY SHOW CHECK-INS NEEDING REVIEW", key="checkins_review_only")

    # these are the actual statuses supported by module 2.
    status_filter = st.selectbox(
        "STATUS",
        options=["All", "approved", "flagged", "rejected", "pending", "appealed",],
        key="checkins_status_filter",
        disabled=review_only
    )

    filter_context = (selected_filter_session_id, status_filter, review_only)
    if  st.session_state.get("checkins_filter_context") != filter_context:
        st.session_state.checkins_page = 1
        st.session_state.checkins_filter_context = filter_context

    # session state initializes and resets the page number
    page_number = st.number_input("PAGE", min_value=1, step=1, key="checkins_page")
    page_size = 50
    offset = (page_number - 1) * page_size

    st.button("REFRESH CHECK-INS")  # reruns the page and fetch fresh data

    try:
        # "All" means no status restriction in the backend request
        selected_status = (None if status_filter == "All" else status_filter)

        # mod 2 applies both filters and enforces teaching access.
        checkins_response = st.session_state.api.get_checkins(
            status=selected_status,
            session_id=selected_filter_session_id,
            limit=page_size,
            offset=offset,
            review_only=review_only,
        )
        checkins = checkins_response["items"]

        # explain an empty result caused by requesting a later page.
        total = checkins_response["total"]
        total_pages = max(1, (total + page_size - 1) // page_size)

        st.caption(f"Page {page_number}; available pages: {total_pages}")

        if  page_number > total_pages:
            st.warning("This page is beyond the results. Select a lower page.")

        st.caption(
            f"Showing {len(checkins)} of "
            f"{checkins_response['total']} authorized check-ins."
        )

        if  checkins:
            checkin_rows = [
                {
                    "Check-in ID": checkin["id"],
                    "Student": checkin.get("student_name"),
                    "Session": checkin.get("session_name"),
                    "Time": checkin["checked_in_at"],
                    "Status": checkin["status"],
                    "Risk score": checkin.get("risk_score"),
                }
                for checkin in checkins
            ]

            st.dataframe(
                checkin_rows,
                use_container_width=True,
                hide_index=True,
            )

            st.subheader("CHECK-IN DETAILS")

            # offer records from the currently displayed page.
            checkin_labels = {
                checkin["id"]: (
                    f"{checkin.get('student_name') or 'Unknown student'}"
                    f" — {checkin['id']}"
                )
                for checkin in checkins
            }

            selected_checkin_id = st.selectbox(
                "SELECT CHECK-IN",
                options=list(checkin_labels),
                format_func=lambda checkin_id: checkin_labels[checkin_id],
                key="checkin_detail_selection",
            )

            try:
                detail = st.session_state.api.get_checkin(selected_checkin_id)

                st.write(f"**STATUS:** {detail['status']}")

                # missing evidence must remain visibly missing.
                score_columns = st.columns(3)
                score_fields = [
                    ("RISK SCORE", "risk_score"),
                    ("LIVENESS SCORE", "liveness_score"),
                    ("FACE MATCH SCORE", "face_match_score"),
                ]

                for column, (label, field) in zip(
                    score_columns, score_fields
                ):
                    score = detail.get(field)
                    column.metric(
                        label,
                        "Not available" if score is None
                        else f"{score:.2f}",
                    )

                st.write(
                    "**LIVENESS PASSED:**",
                    detail.get("liveness_passed")
                    if detail.get("liveness_passed") is not None
                    else "Not evaluated",
                )
                st.write(
                    "**FACE MATCH PASSED:**",
                    detail.get("face_match_passed")
                    if detail.get("face_match_passed") is not None
                    else "Not evaluated",
                )

                st.subheader("RISK SIGNALS")
                signals = detail.get("risk_signals") or []
                if  signals:
                    st.json(signals)
                else:
                    st.info("No risk signals are recorded.")

            except Exception:
                st.error("Could not load check-in details.")
        else:
            st.info("no check-ins are available for your courses yet.")

    except Exception:
        st.error("could not load check-ins from the backend.")

elif  page == "AUDIT LOGS":
    st.header("AUDIT LOGS")

    # TODO - temp mock data
    # TODO - replace with st.session_state.api.get_audit_logs() when mod 2 running okay

    audit_logs = [

        {
            "timestamp":  "2026-10-10 10:30:50",
            "event_type": "CHECK-IN",
            "user":       "S001",
            "action":     "student checked in",
            "severity":   "INFO",
            "details":    "successful attendance verification"
        },

        {
            "timestamp":  "2026-10-10 11:30:00",
            "event_type": "LOGIN",
            "user":       "I001",
            "action":     "Instructor Login",
            "severity":   "INFO",
            "details":    "Instructor successful Login"
        },

        {
            "timestamp":  "2026-10-10 09:50:50",
            "event_type": "SYSTEM",
            "user":       "system",
            "action":     "backend connection failure",
            "severity":   "ERROR",
            "details":    "unable to connect to module 2 backend"
        }
    ]

    st.subheader("FILTERS")

    col1, col2, col3 = st.columns(3)

    with col1:
        event_types  = ["ALL"] + sorted(
            set(log["event_type"] for log in audit_logs)  # the usage of set() here is just to remove duplicates
            )
        event_filter = st.selectbox(
            "EVENT TYPE",
            event_types
            )


    with col2:
        users = ["ALL"] + sorted(
            set(log["user"] for log in audit_logs)
        )

        user_filter = st.selectbox(
            "USER",
            users
        )


    with col3:
        actions = ["ALL"] + sorted(
            set(log["action"] for log in audit_logs)
        )

        action_filter = st.selectbox(
            "ACTION",
            actions
        )

    filtered_logs = audit_logs

    if  event_filter  != "ALL":
        filtered_logs = [
            log
            for log in filtered_logs
            if log["event_type"] == event_filter
        ]

    if  user_filter   != "ALL":
        filtered_logs = [
            log
            for log in filtered_logs
            if log["user"] == user_filter
        ]

    if  action_filter != "ALL":
        filtered_logs = [
            log
            for log in filtered_logs
            if log["action"] == action_filter
        ]

    # ^^^ Take whatever survived the prev filter and filter it again

    st.subheader("AUDIT EVENTS")


    def color_severity(row):
        if  row["severity"] == "ERROR":
            return ["background-color: #FF0000"] * len(row)

        if  row["severity"] == "WARNING":
            return ["background-color: #FFA500"] * len(row)

        if  row["severity"] == "INFO":
            return ["background-color: #0000FF"] * len(row)

        return [""] * len(row)  # this is for if we somehow encounter another severity not specified , no color


    audit_logs_df = pd.DataFrame(filtered_logs)
    audit_csv_data = audit_logs_df.to_csv(index=False)

    st.download_button(
        "DOWNLOAD AUDIT LOGS CSV",
        audit_csv_data,
        "audit_logs.csv",
        "text/csv"
        )

    colored_audit_logs = audit_logs_df.style.apply(
        color_severity,
        axis = 1  # this is too apply the function row by row
    )

    if  filtered_logs:
        st.dataframe(
            colored_audit_logs,
            use_container_width = True
            )
    else:
        st.info("no audit events match the selected filters")



elif  page == "METRICS":
    st.header("SYSTEM METRICS")

    api_requests = 1248
    api_success_rate = 98.8
    api_error_rate = 1.3

    col1 , col2 , col3 = st.columns(3)

    col1.metric("API REQUESTS", api_requests)
    col2.metric("SUCCESS RATE", f"{api_success_rate}%")
    col3.metric("ERROR RATE",   f"{api_error_rate}%")

    api_response_p95 = 245  # rmb this is mock data but this is suppose to simulate a 95TH percentile API response time

    st.metric("API RESPONSE P95", f"{api_response_p95} ms")


    st.subheader("RISK SCORE DISTRIBUTION")

    risk_distribution = {
        "LOW": 72,
        "MEDIUM": 18,
        "HIGH": 10
    }

    st.bar_chart(risk_distribution)


    st.subheader("HIGH-RISK ALERTS")

    high_risk_alerts = [
        {
            "checkin_id": "C003",
            "student_id": "S103",
            "risk_score":  0.90,
            "reason":     "low face match score"
        },

        {
            "checkin_id": "C004",
            "student_id": "S104",
            "risk_score":  0.85,
            "reason":     "low liveness score"
        }
    ]

    if  high_risk_alerts:
        st.dataframe(high_risk_alerts, use_container_width=True)
    else:
        st.info("no high-risk alerts")


    st.subheader("SYSTEM HEALTH")

    backend_status    = "HEALTHY"  # TODO - rmb these are temp / mock values
    database_status   = "HEALTHY"  # TODO - rmb these are temp / mock values
    prometheus_status = "HEALTHY"  # TODO - rmb these are temp / mock values

    health_col1 , health_col2 , health_col3 = st.columns(3)


    health_col1.metric("BACKEND", backend_status)
    health_col2.metric("DATABASE", database_status)
    health_col3.metric("PROMETHEUS", prometheus_status)
