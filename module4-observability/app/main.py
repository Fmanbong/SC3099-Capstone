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

st.session_state.logged_in = True  # TODO - super important to remove this when mod 2 login feature up and running - this just helps us run our mock data currently

if not st.session_state.logged_in:
    st.subheader("INSTRUCTOR LOGIN")

    email = st.text_input("email")
    password = st.text_input("password", type="password")

    if st.button("Login"):
        try:
            login_data = st.session_state.api.login(email, password)
            st.session_state.login_data = login_data
            st.session_state.logged_in  = True
            st.success("Login success!")
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


if st.sidebar.button("LOGOUT"):
    st.session_state.logged_in  = False
    st.session_state.login_data = None
    st.session_state.api.token  = None
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
if   page == "OVERVIEW":
    st.header("OVERVIEW")

    try:

        # TODO - temp mock data , remember to remove once we have mod 2 up and running
        # TODO - this will be replaced with "overview = st.session_state.api.get_overview_stats()"

        overview = {
            "total_sessions":          12,
            "current_active_sessions": 2,
            "total_checkins":          482,
            "current_active_checkins": 453
        }

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric(
                "TOTAL SESSIONS",
                overview["total_sessions"]
            )

        with col2:
            st.metric(
                "CURRENT ACTIVE SESSIONS",
                overview["current_active_sessions"]
            )

        with col3:
            st.metric(
                "TOTAL CHECK-INS",
                overview["total_checkins"]
            )

        with col4:
            success_rate = (
                (overview["current_active_checkins"] / overview["total_checkins"]) * 100
            )

            st.metric(
                "SUCCESS RATE",
                f"{success_rate:.1f}%"
            )


        verification_counts = {}

        for checkin in every_checkin:
            status = checkin["verification_status"]

            if status not in verification_counts:
                verification_counts[status] = 0
            verification_counts[status] += 1

        verification_df = pd.DataFrame(
            list(verification_counts.items()),
            columns = ["verification_status", "count"]
        )

        st.subheader("VERIFICATION STATUS")

        st.plotly_chart(
            px.pie(
                verification_df,
                names = "verification_status",
                values = "count"
                ),
            use_container_width = True
        )


        st.subheader("RECENT CHECK-INS")

        recent_checkins = [
            {
                "student": "Alice Tan",
                "session": "CS50 Lecture 1",
                "time":    "10:32 AM",
                "status":  "success"
            },


            {
                "student": "Benny Lim",
                "session": "CS50 Lecture 1",
                "time":    "10:30 AM",
                "status":  "success"
            },


            {
                "student": "James Tay",
                "session": "CS50 Lecture 1",
                "time":    "10:28 AM",
                "status":  "flagged"
            }
        ]

        st.dataframe(
            recent_checkins,
            use_container_width = True  # makes the table use the available width of the page
        )


        hour_counts = {}

        for checkin in every_checkin:
            timestamp = pd.to_datetime(
                checkin["timestamp"],
                format = "%d-%m-%Y %H:%M"
            )

            hour = timestamp.hour

            if hour not in hour_counts:
                hour_counts[hour] = 0

            hour_counts[hour] += 1

        hour_df = pd.DataFrame(
            list(hour_counts.items()),
            columns = ["hour", "checkins"]
        )

        hour_df = hour_df.sort_values("hour")

        st.subheader("CHECK-INS BY HOUR")

        st.plotly_chart(
            px.bar(
                hour_df,
                x = "hour",
                y = "checkins",
                labels = {
                    "hour" : "Hour",
                    "checkins": "Check-ins"
                }
            ),
            use_container_width = True
        )


        st.json(overview)
    except Exception as e:
        st.error(f"could not load overview statistics : {e}")


elif page == "SESSIONS":
    st.header("SESSIONS")

    # TODO - temporary mock data
    # TODO - replace with st.session_state.api.get_sessions() when mod 2 is running

    st.subheader("ALL SESSIONS")

    st.dataframe(
        sessions,
        use_container_width = True
    )

    session_options = {
        session["id"]: session["name"]
        for session in sessions  # For every session in the sessions list , take ID and name and put into DICT
    }

    selected_session_id = st.selectbox(
        "SELECT A SESSION",
        options = list(session_options.keys()),
        format_func = lambda session_id: session_options[session_id]
    )

    # this is just the entire DICT for the selected session
    selected_session = next(
        session for session in sessions
        if session["id"] == selected_session_id
    )

    st.subheader("SESSION DETAILS")

    col1 , col2 = st.columns(2)

    with col1:
        st.write(f"**SESSION ID:** {selected_session['id']}")
        st.write(f"**NAME:** {selected_session['name']}")
        st.write(f"**COURSE:** {selected_session['course']}")

    with col2:
        st.write(f"**STATUS:** {selected_session['status']}")
        st.write(f"**START:** {selected_session['start_time']}")
        st.write(f"**END:** {selected_session['end_time']}")


    st.subheader("SESSION EXPORT")

    export_checkins = [
        checkin
        for checkin in every_checkin
        if checkin["session_id"] == selected_session["id"]
    ]

    export_checkins_df = pd.DataFrame(export_checkins)
    session_csv_data = export_checkins_df.to_csv(index=False)

    st.download_button(
        "DOWNLOAD SESSION CSV",
        session_csv_data,
        f"session_{selected_session['id']}_checkins.csv",
        "text/csv"
        )


    st.subheader("SESSION CHECK-INS")

    # TODO - temp mock data
    # TODO - replace with st.session_state.api.get_session_checkins(selected_session_id) once mod 2 up and running

    session_checkins = [
        checkin
        for checkin in every_checkin
        if checkin["session_id"] == selected_session_id
    ]

    if session_checkins:
        st.dataframe(
            session_checkins,
            use_container_width = True
        )
    else:
        st.info("no checkins found for this session")  # this is if module 2 gives us an empty list or session



    st.divider()
    st.subheader("CREATE NEW SESSION")

    with st.form("create_session_form"):
        session_name = st.text_input("SESSION NAME")
        course_id = st.text_input("COURSE ID")

        start_time = st.text_input(
            "START TIME",
            placeholder = "DD-MM-YYYY HH:MM"
        )

        end_time = st.text_input(
            "END TIME",
            placeholder = "DD-MM-YYYY HH:MM"
        )

        st.write("GEOFENCE CONFIGURATION")  # For the geographical boundary of the session

        latitude = st.number_input(
            "LATITUDE",
            value = 1.3521,  # TODO - hardcoded temp value - remove once mod 2 up and running
            format = "%.6f"
        )

        longitude = st.number_input(
            "LONGITUDE",
            value = 103.8198,
            format = "%.6f"
        )

        radius = st.number_input(
            "RADIUS (METRES)",
            min_value = 1,  # cannot enter 0 or a negative number
            value = 100
        )

        create_button = st.form_submit_button("CREATE SESSION")

        if create_button:
            if not session_name or not course_id:
                st.error("session name and course id are required")
            else:
                new_session = {
                    "id":         len(st.session_state.sessions) + 1,
                    "name":       session_name,
                    "course":  course_id,
                    "status":     "active",
                    "start_time": start_time,
                    "end_time":   end_time,
                    "geofence": {
                        "latitude":  latitude,
                        "longitude": longitude,
                        "radius":    radius
                    }
                }

                st.session_state.sessions.append(new_session)
                st.success("session form completed")
                st.rerun()


elif page == "CHECK-INS":
    st.header("CHECK-INS")

    # TODO - temp mock data
    # TODO - replace with st.session_state.api.get_checkins() when mod 2 is running

    st.subheader("FILTERS")

    col1 , col2 = st.columns(2)

    with col1:
        session_filter = st.selectbox(
            "SESSION",
            ["ALL"] + [
                f"{session['id']} - {session['name']}"
                for session in sessions
            ]
        )

    with col2:
        status_filter = st.selectbox(
            "VERIFICATION STATUS",
            ["ALL" , "success" , "flagged"]
        )

    filtered_checkins = every_checkin

    if session_filter != "ALL":
        selected_filter_session_id = int(session_filter.split(" - ")[0])

        filtered_checkins = [
            checkin
            for checkin in filtered_checkins
            if checkin["session_id"] == selected_filter_session_id
        ]

    if status_filter != "ALL":
        filtered_checkins = [
            checkin
            for checkin in filtered_checkins
            if checkin["verification_status"] == status_filter
        ]

    st.subheader("CHECK-IN RECORDS")

    if filtered_checkins:  # only run this code if anything survived our filters
        st.dataframe(
            filtered_checkins,
            use_container_width = True
        )
    else:
        st.info("no checkins match the selected filters")


    checkins_df = pd.DataFrame(filtered_checkins)
    csv_data = checkins_df.to_csv(index=False)  # False because I do not want pandas auto generated row numbers

    st.download_button(
        "DOWNLOAD CHECK-INS CSV",
        csv_data,
        "checkins.csv",
        "text/csv"
        )


    if filtered_checkins:
        st.divider()

        st.subheader("CHECK-IN DETAILS")

        checkin_options = {
            checkin["checkin_id"]: checkin
            for checkin in filtered_checkins
        }

        selected_checkin_id = st.selectbox(
            "SELECT CHECK-IN",
            options = list(checkin_options.keys()),
            format_func = lambda checkin_id: (
                f"{checkin_id} - "
                f"{checkin_options[checkin_id]['student_name']}"
            )
        )

        selected_checkin = checkin_options[selected_checkin_id]

        # NOTE - this is just temp data so I just added a few fields , you can add more such as the student name , timestamp , etc
        st.write(
            f"**STUDENT ID:** {selected_checkin['student_id']}"
        )

        st.write(
            f"**CHECKIN ID:** {selected_checkin['checkin_id']}"
        )

        st.write(
            f"**VERIFICATION STATUS:** "
            f"{selected_checkin['verification_status']}"
        )

        st.subheader("VERIFICATION SIGNALS")

        col1 , col2 , col3 = st.columns(3)

        with col1:
            st.metric(
                "RISK SCORE",
                f"{selected_checkin['risk_score']:.2f}"

            )

        with col2:
            st.metric(
                "LIVENESS SCORE",
                f"{selected_checkin['liveness_score']:.2f}"

            )

        with col3:
            st.metric(
                "FACE MATCH SCORE",
                f"{selected_checkin['face_match_score']:.2f}"

            )

        st.subheader("GPS LOCATION")

        col1 , col2 = st.columns(2)

        with col1:
            st.write(
                f"**LATITUDE:** {selected_checkin['latitude']}"
            )

        with col2:
            st.write(
                f"**LONGITUDE:** {selected_checkin['longitude']}"
            )


elif page == "AUDIT LOGS":
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

    if event_filter  != "ALL":
        filtered_logs = [
            log
            for log in filtered_logs
            if log["event_type"] == event_filter
        ]

    if user_filter   != "ALL":
        filtered_logs = [
            log
            for log in filtered_logs
            if log["user"] == user_filter
        ]

    if action_filter != "ALL":
        filtered_logs = [
            log
            for log in filtered_logs
            if log["action"] == action_filter
        ]

    # ^^^ Take whatever survived the prev filter and filter it again

    st.subheader("AUDIT EVENTS")


    def color_severity(row):
        if row["severity"] == "ERROR":
            return ["background-color: #FF0000"] * len(row)

        if row["severity"] == "WARNING":
            return ["background-color: #FFA500"] * len(row)

        if row["severity"] == "INFO":
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

    if filtered_logs:
        st.dataframe(
            colored_audit_logs,
            use_container_width = True
            )
    else:
        st.info("no audit events match the selected filters")



elif page == "METRICS":
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

    if high_risk_alerts:
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
