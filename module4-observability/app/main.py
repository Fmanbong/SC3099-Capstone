"""
SAIV INSTRUCTOR DASHBOARD

Students must implement the instructor dashboard with session management,
attendance monitoring , and metrics visualisation
"""


import streamlit as st
import os
from api_client import APIClient

# page setup
st.set_page_config(
    page_title = "SAIV INSTRUCTOR DASHBOARD",
    page_icon  = "O..O", layout     = "wide",  # Layout == 'page elements use the entire screen width
)

BACKEND_URL    = os.getenv("BACKEND_URL",    "http://localhost:8000")  # environment vars
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")  # environment vars


if "api" not in st.session_state:  # asking if the current streamlit session already has an API client
    st.session_state.api = APIClient(BACKEND_URL)

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

st.title("SAIV INSTRUCTOR DASHBOARD")

st.session_state.logged_in = True  # TODO - super important to remove this when mod 2 login feature up and running

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


    st.subheader("SESSION CHECK-INS")

    # TODO - temp mock data
    # TODO - replace with st.session_state.api.get_session_checkins(selected_session_id) once mod 2 up and running

    # DICT with the key being the session ID
    mock_checkins = {

        1: [
                {
                    "student_id":          "S001",
                    "student_name":        "Alice Tan",
                    "timestamp":           "10-10-2026 10:00",
                    "verification_status": "success",
                    "risk_score":           0.10
                },

                {
                    "student_id":          "S002",
                    "student_name":        "Benny Lim",
                    "timestamp":           "10-10-2026 10:32",
                    "verification_status": "success",
                    "risk_score": 0.08
                },

                {
                    "student_id":          "S003",
                    "student_name":        "James Tay",
                    "timestamp":           "10-10-2026 10:28",
                    "verification_status": "flagged",
                    "risk_score": 0.80
                }
        ],


        2: [
                {
                    "student_id":          "S004",
                    "student_name":        "Sarah Lim",
                    "timestamp":           "10-10-2026 14:30",
                    "verification_status": "success",
                    "risk_score":           0.10
                }
        ],

        3: []
    }

    # e.g. suppose you select 'CS50 Lecture 1' whose ID is 1 --- becomes 'mock_checkins.get(1, [])'
    session_checkins = mock_checkins.get(
        selected_session_id,
        []
    )

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
            "timestamp":           "10-10-2026 10:30",
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
            "timestamp":           "10-10-2026 11:00",
            "verification_status": "flagged",
            "risk_score":           0.90,
            "liveness_score":       0.60,
            "face_match_score":     0.70,
            "latitude":             1.3500,
            "longitude":            103.8100
        },
    ]

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
            "VERFICATION STATUS",
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
    st.header("audit logs")
    st.write("TODO - audit logs goes here")

elif page == "METRICS":
    st.header("metrics")
    st.write("TODO - metrics and system health goes here")



# =============================================================================
# TODO: Implement the following pages/features
# =============================================================================

# -----------------------------------------------------------------------------
# Authentication
# -----------------------------------------------------------------------------
# - Login form for instructors
# - JWT token management
# - Session persistence

# -----------------------------------------------------------------------------
# Overview Page
# -----------------------------------------------------------------------------
# - Total sessions (active/inactive)
# - Total check-ins & success rate
# - Recent check-ins table
# - Check-ins by hour chart
# - Verification status pie chart

# -----------------------------------------------------------------------------
# Sessions Management
# -----------------------------------------------------------------------------
# - View all sessions with details
# - Create new sessions with geofence config
# - View check-ins per session
# - CSV export for gradebook

# -----------------------------------------------------------------------------
# Check-ins View
# -----------------------------------------------------------------------------
# - Filter by session, verification status
# - Real-time updates
# - CSV export for gradebook integration
# - Attendance data with all signals

# -----------------------------------------------------------------------------
# Audit Logs
# -----------------------------------------------------------------------------
# - Browse system events
# - Filter by event type, user, action
# - Color-coded by severity
# - Export audit trail

# -----------------------------------------------------------------------------
# Metrics Dashboard
# -----------------------------------------------------------------------------
# - API response times (p95 latency)
# - Request rates
# - Success rates
# - Risk score distribution
# - High-risk alerts
# - System health status

# =============================================================================
# CSV Export Format
# =============================================================================
# Required columns:
# - Check-in ID, Student ID, Session ID
# - Timestamp, Verification Status
# - Risk Score, Liveness Score, Face Match Score
# - GPS Coordinates (latitude, longitude)
